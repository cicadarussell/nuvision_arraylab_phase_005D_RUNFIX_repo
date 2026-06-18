from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.db_models import Base
from app.schemas.geometry import PanelPackingOverrideCreate, PanelPackingRequest, ProjectCreate, RoofPlaneCreate, SiteCreate
from app.schemas.project import RoofType
from app.services.panel_packing import (
    build_selected_layout_export_payload,
    create_panel_packing_override,
    list_panel_packing_overrides,
    panel_layout_edit_contract,
    panel_packing_governance_self_check,
    run_panel_packing,
)
from app.services.project_geometry import add_roof_plane, create_project, upsert_site


def make_db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def make_packable_project():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_gov_pack", title="Governance packable roof"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, source_type="postcode_lookup"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35,
        azimuth_deg=180,
        height_m=6.0,
        polygon_local_m=[[0, 0], [10, 0], [10, 6], [0, 6]],
        source_confidence=0.65,
    ))
    return db, project.project_id


def test_persistent_override_history_is_append_only_and_selected_layout_export_is_stable():
    db, project_id = make_packable_project()
    packing = run_panel_packing(db, project_id, PanelPackingRequest(allow_dev_fallback_panels=True, score_goal="aesthetic"))
    selected = packing.selected_candidate_ids[0]
    first = create_panel_packing_override(db, project_id, packing.calculation_run_id, PanelPackingOverrideCreate(
        selected_candidate_id=selected,
        override_reason="Installer prefers this selected preview candidate because rows are cleaner.",
        reviewer="Thomas",
        reviewer_role="founder_test",
        intended_use="preview",
    ))
    first_hash = first.selected_candidate_hash_sha256
    first_export_hash = first.selected_layout_export_hash_sha256
    second = create_panel_packing_override(db, project_id, packing.calculation_run_id, PanelPackingOverrideCreate(
        selected_candidate_id=selected,
        override_reason="Second review appends evidence instead of mutating the first record.",
        reviewer="CICADA QA",
        reviewer_role="quality_gate",
        intended_use="preview",
    ))
    history = list_panel_packing_overrides(db, project_id)
    assert history.override_count == 2
    by_id = {row.override_id: row for row in history.overrides}
    assert first.override_id in by_id
    assert second.override_id in by_id
    assert by_id[first.override_id].selected_candidate_hash_sha256 == first_hash
    assert by_id[first.override_id].selected_layout_export_hash_sha256 == first_export_hash

    export_a = build_selected_layout_export_payload(db, project_id, packing.calculation_run_id)
    export_b = build_selected_layout_export_payload(db, project_id, packing.calculation_run_id)
    assert export_a.selected_layout_export_hash_sha256 == export_b.selected_layout_export_hash_sha256
    assert export_a.latest_override is not None
    assert export_a.row_annotations
    assert export_a.downstream_contracts["stringing_input"]["status"].startswith("blocked")


def test_final_use_override_cannot_be_created_from_preview_dev_fallback_run():
    db, project_id = make_packable_project()
    packing = run_panel_packing(db, project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    selected = packing.selected_candidate_ids[0]
    try:
        create_panel_packing_override(db, project_id, packing.calculation_run_id, PanelPackingOverrideCreate(
            selected_candidate_id=selected,
            override_reason="Trying to pretend preview fallback data is final should fail.",
            reviewer="Thomas",
            reviewer_role="founder_test",
            intended_use="final",
        ))
        assert False, "final-use override should have been blocked"
    except Exception as exc:
        assert "Final-use override" in str(exc)


def test_override_must_reference_selected_candidate_from_the_run():
    db, project_id = make_packable_project()
    packing = run_panel_packing(db, project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    non_selected = next(c["candidate_id"] for c in packing.candidate_summaries if c["candidate_id"] not in packing.selected_candidate_ids and c.get("panel_count", 0) > 0)
    try:
        create_panel_packing_override(db, project_id, packing.calculation_run_id, PanelPackingOverrideCreate(
            selected_candidate_id=non_selected,
            override_reason="Persistent override should require rerun with candidate selected first.",
            reviewer="Thomas",
            reviewer_role="founder_test",
            intended_use="preview",
        ))
        assert False, "non-selected candidate override should require a rerun first"
    except Exception as exc:
        assert "Rerun packing" in str(exc)


def test_layout_edit_contract_is_available_and_explicitly_non_mutating():
    contract = panel_layout_edit_contract("prj_contract")
    assert "request_panel_delete_draft" in contract.allowed_actions
    assert "source_calculation_run_id" in contract.required_fields_by_action["request_panel_delete_draft"]
    assert any("not applied" in blocker for blocker in contract.blockers)


def test_panel_packing_governance_self_check_passes():
    db = make_db()
    result = panel_packing_governance_self_check(db)
    assert result.status == "ok"
    assert result.override_history_immutable is True
    assert result.final_override_blocked_from_preview_fallback is True
    assert result.selected_layout_export_hash_stable is True
    assert result.layout_edit_contract_available is True


def test_routes_include_panel_packing_governance_endpoints():
    client = TestClient(app)
    restore = client.get("/api/debug/restore-check")
    assert restore.status_code == 200
    assert restore.json()["current_phase"] == "NVA_005D_RUNFIX"
    assert restore.json()["checks"]["has_persistent_candidate_override_records"] is True
    route_map = client.get("/api/debug/route-map").json()
    routes = {item["path"] for item in route_map["routes"]}
    assert "/api/debug/panel-packing-governance-self-check" in routes
    assert "/api/projects/{project_id}/panel-packing/overrides" in routes
    assert "/api/projects/{project_id}/panel-packing/runs/{calculation_run_id}/selected-layout-export" in routes
    assert "/api/projects/{project_id}/panel-packing/layout-edit-contract" in routes

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.db_models import Base, CalculationRun
from app.schemas.geometry import PanelPackingRequest, ProjectCreate, RoofPlaneCreate, SiteCreate
from app.schemas.project import RoofType
from app.services.panel_packing import panel_packing_self_check, run_panel_packing
from app.services.project_geometry import add_roof_plane, create_project, upsert_site


def make_db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def make_packable_project():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_pack", title="Packable roof"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, source_type="postcode_lookup"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35,
        azimuth_deg=180,
        height_m=6.0,
        polygon_local_m=[[0, 0], [8, 0], [8, 5], [0, 5]],
        source_confidence=0.65,
    ))
    return db, project.project_id


def test_preview_panel_packing_uses_dev_fallback_and_writes_calculation_run():
    db, project_id = make_packable_project()
    result = run_panel_packing(db, project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    assert result.status in {"ok", "warnings"}
    assert result.design_status == "preview_only"
    assert len(result.placements) > 0
    assert result.summary["total_kwp"] > 0
    assert result.output_hash_sha256
    assert result.geometry_quality_report_hash_sha256
    assert any(issue.code == "DEV_FALLBACK_PANEL_USED" for issue in result.validation_report.issues)
    row = db.get(CalculationRun, result.calculation_run_id)
    assert row is not None
    assert row.run_type == "panel_packing"
    assert row.output_hash_sha256 == result.output_hash_sha256


def test_final_design_blocks_dev_fallback_panels():
    db, project_id = make_packable_project()
    result = run_panel_packing(db, project_id, PanelPackingRequest(allow_dev_fallback_panels=True, design_mode="final"))
    assert result.status == "blocked"
    codes = {issue.code for issue in result.validation_report.issues}
    assert "DEV_FALLBACK_BLOCKS_FINAL_DESIGN" in codes
    assert result.design_status == "blocked"


def test_invalid_geometry_cannot_be_packed():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_bad_pack", title="Bad polygon"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35,
        azimuth_deg=180,
        height_m=6.0,
        polygon_local_m=[[0, 0], [6, 0], [0, 6], [6, 6]],
    ))
    result = run_panel_packing(db, project.project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    assert result.status == "blocked"
    assert result.placements == []
    codes = {issue.code for issue in result.validation_report.issues}
    assert "ROOF_POLYGON_INVALID" in codes


def test_panel_packing_self_check_passes():
    db = make_db()
    result = panel_packing_self_check(db)
    assert result.status == "ok"
    assert result.preview_panels_fit is True
    assert result.final_blocks_dev_fallback is True
    assert result.invalid_geometry_blocked is True
    assert result.roof_aligned_candidate_differs is True


def test_routes_include_panel_packing_endpoints():
    client = TestClient(app)
    restore = client.get("/api/debug/restore-check")
    assert restore.status_code == 200
    assert restore.json()["current_phase"] == "NVA_005D_RUNFIX"
    assert restore.json()["checks"]["has_panel_packing_engine"] is True
    route_map = client.get("/api/debug/route-map").json()
    routes = {item["path"] for item in route_map["routes"]}
    assert "/api/debug/panel-packing-self-check" in routes
    assert "/api/projects/{project_id}/panel-packing/run" in routes


def test_roof_aligned_packing_rotates_panels_when_roof_azimuth_is_not_south():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_rotated_pack", title="Rotated roof"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, source_type="postcode_lookup"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35,
        azimuth_deg=135,
        height_m=6.0,
        polygon_local_m=[[0, 0], [10, 0], [10, 8], [0, 8]],
        source_confidence=0.65,
    ))
    aligned = run_panel_packing(db, project.project_id, PanelPackingRequest(allow_dev_fallback_panels=True, packing_alignment="roof_azimuth"))
    axis = run_panel_packing(db, project.project_id, PanelPackingRequest(allow_dev_fallback_panels=True, packing_alignment="axis_aligned"))
    assert aligned.placements
    assert axis.placements
    assert aligned.placements[0].rotation_deg != axis.placements[0].rotation_deg
    assert aligned.panel_placements_geojson["type"] == "FeatureCollection"
    assert len(aligned.panel_placements_geojson["features"]) == len(aligned.placements)


def test_candidate_summaries_mark_one_selected_per_roof_plane():
    db, project_id = make_packable_project()
    result = run_panel_packing(db, project_id, PanelPackingRequest(allow_dev_fallback_panels=True, score_goal="best_fit"))
    selected = [c for c in result.candidate_summaries if c.get("selected")]
    assert len(selected) == 1
    assert selected[0]["candidate_id"] in result.selected_candidate_ids
    assert selected[0]["score_goal"] == "best_fit"


def test_more_setback_reduces_or_preserves_panel_count():
    db = make_db()
    project_small_margin = create_project(db, ProjectCreate(project_id="prj_small_margin", title="Small margin"))
    upsert_site(db, project_small_margin.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, source_type="postcode_lookup"))
    add_roof_plane(db, project_small_margin.project_id, RoofPlaneCreate(
        roof_type=RoofType.ground_mount,
        pitch_deg=35,
        azimuth_deg=180,
        height_m=1.5,
        polygon_local_m=[[0, 0], [10, 0], [10, 6], [0, 6]],
    ))
    project_large_margin = create_project(db, ProjectCreate(project_id="prj_large_margin", title="Large margin"))
    upsert_site(db, project_large_margin.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, source_type="postcode_lookup"))
    add_roof_plane(db, project_large_margin.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35,
        azimuth_deg=180,
        height_m=6.0,
        polygon_local_m=[[0, 0], [10, 0], [10, 6], [0, 6]],
    ))
    small = run_panel_packing(db, project_small_margin.project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    large = run_panel_packing(db, project_large_margin.project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    assert small.summary["panel_count"] >= large.summary["panel_count"]

from app.models.db_models import Product, ProductSpec
from app.services.panel_packing import list_reviewed_panel_model_options


def add_reviewed_panel(db, product_id="prd_q3_panel", design_ready=True, quality="Q3_reviewed"):
    product = Product(
        product_id=product_id,
        nuvision_sku="Q3-PANEL-1",
        manufacturer="NuVision Test",
        manufacturer_model="Q3-430",
        category="panel",
        title="Q3 reviewed test panel 430W",
        status="active",
        quality_level=quality,
        design_ready=design_ready,
    )
    db.add(product)
    for field, value, unit in [
        ("power_stc_w", 430, "W"),
        ("length_mm", 1722, "mm"),
        ("width_mm", 1134, "mm"),
    ]:
        db.add(ProductSpec(
            spec_id=f"spec_{product_id}_{field}",
            product_id=product_id,
            field_name=field,
            value_text=str(value),
            value_number=float(value),
            unit=unit,
            normalized_value_si=float(value),
            normalized_unit=unit,
            quality_level="Q3_reviewed",
            source_type="manufacturer_datasheet",
            source_url="https://manufacturer.example/test.pdf",
            source_file_hash_sha256="a" * 64,
            source_page=1,
            source_text_quote=f"{field}: {value} {unit}",
            extraction_method="manual_review",
            confidence=0.99,
            review_status="reviewed",
            reviewed_by="tester",
        ))
    db.commit()
    return product


def test_reviewed_panel_model_picker_excludes_q0_q1_products_and_lists_q3():
    db = make_db()
    db.add(Product(
        product_id="prd_q0_panel",
        manufacturer="Shop Text Inc",
        category="panel",
        title="Q0 scraped panel",
        status="active",
        quality_level="Q0_scraped",
        design_ready=False,
    ))
    add_reviewed_panel(db)
    result = list_reviewed_panel_model_options(db)
    ids = {model.product_id for model in result.models}
    assert "prd_q3_panel" in ids
    assert "prd_q0_panel" not in ids
    assert result.excluded_count >= 1


def test_final_mode_accepts_explicit_reviewed_q3_panel_and_blocks_unreviewed_choice():
    db, project_id = make_packable_project()
    add_reviewed_panel(db, product_id="prd_final_panel")
    good = run_panel_packing(db, project_id, PanelPackingRequest(
        allow_dev_fallback_panels=False,
        design_mode="final",
        panel_product_ids=["prd_final_panel"],
    ))
    assert good.status in {"ok", "warnings"}
    assert good.design_status == "design_draft_requires_electrical_and_mounting_review"
    assert good.panel_models[0].product_id == "prd_final_panel"
    assert good.summary["candidate_comparison_hash_sha256"]

    db.add(Product(
        product_id="prd_q1_panel",
        manufacturer="Unreviewed Inc",
        category="panel",
        title="Q1 linked panel",
        status="active",
        quality_level="Q1_datasheet_linked",
        design_ready=False,
    ))
    db.commit()
    bad = run_panel_packing(db, project_id, PanelPackingRequest(
        allow_dev_fallback_panels=False,
        design_mode="final",
        panel_product_ids=["prd_q1_panel"],
    ))
    assert bad.status == "blocked"
    codes = {issue.code for issue in bad.validation_report.issues}
    assert "PANEL_Q3_SPECS_MISSING" in codes


def test_candidate_summaries_have_explanations_and_comparison_hash():
    db, project_id = make_packable_project()
    result = run_panel_packing(db, project_id, PanelPackingRequest(allow_dev_fallback_panels=True, score_goal="max_kwp"))
    assert result.candidate_summaries
    assert result.summary["candidate_comparison_hash_sha256"]
    assert result.summary["candidate_compare_count"] == len(result.candidate_summaries)
    assert all("reason_codes" in c and "score_explanation" in c for c in result.candidate_summaries)


def test_mixed_layout_and_aesthetic_scores_are_reported():
    db, project_id = make_packable_project()
    result = run_panel_packing(db, project_id, PanelPackingRequest(
        allow_dev_fallback_panels=True,
        candidate_layout_mode="all",
        score_goal="aesthetic",
    ))
    assert result.candidate_summaries
    assert any(c.get("layout_style") == "mixed_portrait_landscape" for c in result.candidate_summaries)
    assert all("aesthetic_score" in c and "goal_scores" in c for c in result.candidate_summaries)
    assert "aesthetic" in result.summary["candidate_goal_rankings"]


def test_manual_candidate_override_records_evidence_and_changes_selection_when_valid():
    db, project_id = make_packable_project()
    baseline = run_panel_packing(db, project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    override_id = next(
        c["candidate_id"] for c in baseline.candidate_summaries
        if c["candidate_id"] not in baseline.selected_candidate_ids and c.get("panel_count", 0) > 0
    )
    overridden = run_panel_packing(db, project_id, PanelPackingRequest(
        allow_dev_fallback_panels=True,
        selected_candidate_override_id=override_id,
        override_reason="installer wants this layout for access and row neatness",
    ))
    assert override_id in overridden.selected_candidate_ids
    assert overridden.summary["manual_override_record"]["candidate_id"] == override_id
    assert overridden.summary["manual_override_record"]["override_reason"].startswith("installer")


def test_missing_candidate_override_is_blocked_and_preserves_truth_boundary():
    db, project_id = make_packable_project()
    result = run_panel_packing(db, project_id, PanelPackingRequest(
        allow_dev_fallback_panels=True,
        selected_candidate_override_id="cand_does_not_exist",
        override_reason="operator typo should not silently pick another layout",
    ))
    assert result.status == "blocked"
    assert result.placements == []
    assert "CANDIDATE_OVERRIDE_NOT_FOUND" in {issue.code for issue in result.validation_report.issues}


def test_panel_packing_candidate_export_replays_candidate_evidence():
    from app.services.panel_packing import export_panel_packing_candidate_run

    db, project_id = make_packable_project()
    result = run_panel_packing(db, project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    exported = export_panel_packing_candidate_run(db, project_id, result.calculation_run_id)
    assert exported.candidate_comparison_hash_sha256 == result.summary["candidate_comparison_hash_sha256"]
    assert exported.selected_candidate_ids == result.selected_candidate_ids
    assert exported.candidate_summaries
    assert "not structural" in exported.truth_boundary.lower()

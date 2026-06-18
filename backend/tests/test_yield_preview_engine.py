from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db_models import Base
from app.schemas.geometry import PanelPackingRequest
from app.schemas.project import RoofType
from app.schemas.yield_preview import YieldPreviewRequest
from app.services.panel_packing import run_panel_packing
from app.services.project_geometry import add_roof_plane, create_project, upsert_site
from app.schemas.geometry import ProjectCreate, RoofPlaneCreate, SiteCreate
from app.services.yield_preview import list_yield_assumption_sets, run_yield_preview, yield_preview_self_check


def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def make_layout(db):
    project = create_project(db, ProjectCreate(title="Yield test"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, timezone="Europe/London", source_type="postcode_lookup"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35,
        azimuth_deg=180,
        height_m=6,
        polygon_local_m=[[0, 0], [11, 0], [11, 6], [0, 6]],
        source_confidence=0.7,
    ))
    layout = run_panel_packing(db, project.project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    return project, layout


def test_yield_preview_requires_selected_layout():
    db = session()
    project = create_project(db, ProjectCreate(title="No layout"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, timezone="Europe/London", source_type="postcode_lookup"))
    empty_layout = run_panel_packing(db, project.project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    result = run_yield_preview(db, project.project_id, YieldPreviewRequest(selected_layout_calculation_run_id=empty_layout.calculation_run_id))
    assert result.status == "blocked"
    assert any(issue.code == "YIELD_REQUIRES_SELECTED_PANEL_LAYOUT" for issue in result.validation_report.issues)


def test_t0_yield_monthly_sum_and_hash_change_with_assumptions():
    db = session()
    project, layout = make_layout(db)
    result = run_yield_preview(db, project.project_id, YieldPreviewRequest(selected_layout_calculation_run_id=layout.calculation_run_id))
    changed = run_yield_preview(db, project.project_id, YieldPreviewRequest(selected_layout_calculation_run_id=layout.calculation_run_id, system_loss_pct=22.0))
    assert result.status == "ok"
    assert result.total_dc_kwp > 0
    assert result.annual_kwh_preview > 0
    assert abs(sum(month.kwh for month in result.monthly) - result.annual_kwh_preview) < 0.02
    assert result.output_hash_sha256 != changed.output_hash_sha256
    assert result.pvgis_request_stub["status"] == "backend_stub_not_called"


def test_pvgis_monthly_override_used_when_supplied():
    db = session()
    project, layout = make_layout(db)
    override = [100.0] * 12
    result = run_yield_preview(db, project.project_id, YieldPreviewRequest(selected_layout_calculation_run_id=layout.calculation_run_id, pvgis_monthly_kwh_override=override))
    assert result.annual_kwh_preview == 1200.0
    assert len(result.monthly) == 12
    assert result.monthly[0].kwh == 100.0


def test_yield_assumption_sets_seed_default():
    db = session()
    sets = list_yield_assumption_sets(db)
    assert any(item.assumption_set_id == "UK_ROOF_PREVIEW_V0_1" for item in sets)
    assert sets[0].specific_yield_kwh_per_kwp_year > 0


def test_yield_preview_self_check():
    db = session()
    check = yield_preview_self_check(db)
    assert check.status == "ok"
    assert check.annual_kwh_positive
    assert check.monthly_sum_matches_annual
    assert check.assumption_change_changes_hash
    assert check.panel_layout_required_blocked

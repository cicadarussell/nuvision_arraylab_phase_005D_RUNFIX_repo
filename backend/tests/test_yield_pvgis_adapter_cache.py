from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db_models import Base
from app.schemas.geometry import PanelPackingRequest
from app.schemas.project import RoofType
from app.schemas.yield_preview import YieldPreviewRequest, YieldModelTier
from app.services.panel_packing import run_panel_packing
from app.services.project_geometry import add_roof_plane, create_project, upsert_site
from app.schemas.geometry import ProjectCreate, RoofPlaneCreate, SiteCreate
from app.services.pvgis_adapter import (
    PvgisFetchResult,
    arraylab_azimuth_to_pvgis_aspect,
    build_pvgis_pvcalc_params,
    get_or_fetch_pvgis_monthly,
    parse_pvgis_monthly_response,
)
from app.services.yield_preview import run_yield_preview, yield_preview_self_check


def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def fake_pvgis_payload(values=None):
    values = values or [80, 110, 180, 240, 300, 330, 320, 270, 210, 150, 90, 70]
    return {"outputs": {"monthly": {"fixed": [{"month": i + 1, "E_m": value} for i, value in enumerate(values)]}, "totals": {"fixed": {"E_y": sum(values)}}}}


def make_layout(db):
    project = create_project(db, ProjectCreate(title="PVGIS adapter test"))
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


def test_pvgis_aspect_conversion_matches_arraylab_convention():
    assert arraylab_azimuth_to_pvgis_aspect(180) == 0
    assert arraylab_azimuth_to_pvgis_aspect(90) == -90
    assert arraylab_azimuth_to_pvgis_aspect(270) == 90


def test_pvgis_monthly_parser_and_cache_hit():
    db = session()
    params = build_pvgis_pvcalc_params(lat=50.8155, lon=-3.2806, peakpower_kwp=4.0, slope_deg=35, azimuth_true_deg=180, loss_pct=14)
    monthly, annual = parse_pvgis_monthly_response(fake_pvgis_payload())
    assert len(monthly) == 12
    assert annual == 2350

    calls = {"count": 0}

    def fetcher(url, request_params, timeout_seconds):
        calls["count"] += 1
        return PvgisFetchResult(status_code=200, payload=fake_pvgis_payload(), final_url=url + "?mock=1")

    fetched = get_or_fetch_pvgis_monthly(db, params=params, fetcher=fetcher, requested_by="test")
    cached = get_or_fetch_pvgis_monthly(db, params=params, allow_network=False, requested_by="test")
    assert fetched.status == "succeeded"
    assert cached.status == "succeeded"
    assert cached.cache_hit_count >= 1
    assert calls["count"] == 1


def test_yield_preview_uses_cached_pvgis_monthly_and_compares_to_t0():
    db = session()
    project, layout = make_layout(db)
    t0 = run_yield_preview(db, project.project_id, YieldPreviewRequest(selected_layout_calculation_run_id=layout.calculation_run_id))
    params = build_pvgis_pvcalc_params(lat=50.8155, lon=-3.2806, peakpower_kwp=t0.total_dc_kwp, slope_deg=35, azimuth_true_deg=180, loss_pct=14)

    def fetcher(url, request_params, timeout_seconds):
        return PvgisFetchResult(status_code=200, payload=fake_pvgis_payload([100] * 12), final_url=url + "?mock=1")

    get_or_fetch_pvgis_monthly(db, params=params, fetcher=fetcher, requested_by="test")
    pvgis = run_yield_preview(db, project.project_id, YieldPreviewRequest(
        selected_layout_calculation_run_id=layout.calculation_run_id,
        model_tier=YieldModelTier.t1_pvgis_monthly_cached,
        use_pvgis_monthly=True,
    ))
    assert pvgis.pvgis_cache is not None
    assert pvgis.pvgis_cache.status == "succeeded"
    assert pvgis.annual_kwh_preview == 1200
    assert pvgis.output_hash_sha256 != t0.output_hash_sha256
    assert pvgis.pvgis_comparison["source"] == "pvgis_cache"


def test_yield_preview_warns_and_falls_back_when_pvgis_unavailable():
    db = session()
    project, layout = make_layout(db)
    result = run_yield_preview(db, project.project_id, YieldPreviewRequest(
        selected_layout_calculation_run_id=layout.calculation_run_id,
        model_tier=YieldModelTier.t1_pvgis_monthly_cached,
        use_pvgis_monthly=True,
        allow_pvgis_network_fetch=False,
    ))
    assert result.status == "warnings"
    assert any(issue.code == "PVGIS_UNAVAILABLE_FALLBACK_TO_T0" for issue in result.validation_report.issues)
    assert result.pvgis_cache is not None
    assert result.pvgis_cache.status == "not_fetched_network_disabled"


def test_yield_preview_self_check_includes_pvgis_cache_behaviour():
    db = session()
    check = yield_preview_self_check(db)
    assert check.status == "ok"
    assert check.pvgis_monthly_cache_hit
    assert check.pvgis_monthly_values_used
    assert check.pvgis_unavailable_fallback_warns

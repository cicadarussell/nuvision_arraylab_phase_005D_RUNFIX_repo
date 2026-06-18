from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db_models import Base
from app.schemas.geometry import PanelPackingRequest
from app.schemas.geometry import ProjectCreate, RoofPlaneCreate, SiteCreate
from app.schemas.project import RoofType
from app.schemas.solar_geometry import SolarGeometryDebugRequest
from app.services.panel_packing import run_panel_packing
from app.services.project_geometry import add_roof_plane, create_project, upsert_site
from app.services.solar_geometry import build_solar_geometry_debug, solar_geometry_self_check


def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def make_project(db, azimuth=180, pitch=35):
    project = create_project(db, ProjectCreate(title="Solar geometry test"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, timezone="Europe/London", source_type="postcode_lookup"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=pitch,
        azimuth_deg=azimuth,
        height_m=6,
        polygon_local_m=[[0, 0], [11, 0], [11, 6], [0, 6]],
        source_confidence=0.7,
    ))
    layout = run_panel_packing(db, project.project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    return project, layout


def test_solar_geometry_debug_returns_bounded_samples_and_contract():
    db = session()
    project, layout = make_project(db)
    result = build_solar_geometry_debug(db, project.project_id, SolarGeometryDebugRequest(selected_layout_calculation_run_id=layout.calculation_run_id))
    assert result.status == "ok"
    assert result.roof_plane_results[0].sample_count == 36
    assert result.roof_plane_results[0].panel_count_hint > 0
    assert result.shade_engine_input_contract["contract_version"] == "SHADE_INPUT_CONTRACT_V0_1"
    for sample in result.roof_plane_results[0].samples:
        assert -90 <= sample.solar_elevation_deg <= 90
        assert 0 <= sample.solar_azimuth_deg < 360
        assert 0 <= sample.plane_of_array_cosine <= 1
        assert 0 <= sample.horizontal_cosine <= 1
        assert 0 <= sample.beam_plane_factor_vs_horizontal <= 5


def test_south_facing_roof_has_better_noon_geometry_than_north_facing_roof():
    db = session()
    south, south_layout = make_project(db, azimuth=180, pitch=35)
    north, north_layout = make_project(db, azimuth=0, pitch=35)
    south_result = build_solar_geometry_debug(db, south.project_id, SolarGeometryDebugRequest(selected_layout_calculation_run_id=south_layout.calculation_run_id))
    north_result = build_solar_geometry_debug(db, north.project_id, SolarGeometryDebugRequest(selected_layout_calculation_run_id=north_layout.calculation_run_id))
    assert south_result.roof_plane_results[0].noon_mean_beam_plane_factor_vs_horizontal > north_result.roof_plane_results[0].noon_mean_beam_plane_factor_vs_horizontal
    assert south_result.output_hash_sha256 != north_result.output_hash_sha256


def test_solar_geometry_hash_changes_when_tilt_changes():
    db = session()
    normal, normal_layout = make_project(db, azimuth=180, pitch=35)
    steep, steep_layout = make_project(db, azimuth=180, pitch=55)
    normal_result = build_solar_geometry_debug(db, normal.project_id, SolarGeometryDebugRequest(selected_layout_calculation_run_id=normal_layout.calculation_run_id))
    steep_result = build_solar_geometry_debug(db, steep.project_id, SolarGeometryDebugRequest(selected_layout_calculation_run_id=steep_layout.calculation_run_id))
    assert normal_result.output_hash_sha256 != steep_result.output_hash_sha256


def test_solar_geometry_self_check():
    db = session()
    check = solar_geometry_self_check(db)
    assert check.status == "ok"
    assert check.south_35_beats_north_35
    assert check.sample_count_ok
    assert check.incidence_math_bounds_ok
    assert check.calculation_hash_changes_with_geometry

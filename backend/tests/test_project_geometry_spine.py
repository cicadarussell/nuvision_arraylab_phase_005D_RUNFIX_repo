from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db_models import Base, CalculationRun
from app.schemas.geometry import ObstructionCreate, ProjectCreate, RoofPlaneCreate, SiteCreate
from app.services.project_geometry import (
    add_obstruction,
    add_roof_plane,
    create_project,
    create_project_snapshot,
    get_project_geometry,
    polygon_area_m2,
    project_geometry_self_check,
    run_project_mounting_precheck,
    upsert_site,
    validate_project_geometry,
)


def make_db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def test_polygon_area_m2_is_stable():
    assert polygon_area_m2([[0, 0], [10, 0], [10, 5], [0, 5]]) == 50
    assert polygon_area_m2([[0, 0], [0, 5], [10, 5], [10, 0]]) == 50


def test_project_site_roof_obstruction_round_trip():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_test", title="Test Project"))
    site = upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, source_confidence=0.8))
    roof = add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type="tiled_pitched", pitch_deg=35, azimuth_deg=180, height_m=6.0,
        polygon_local_m=[[0, 0], [10, 0], [10, 5], [0, 5]],
    ))
    obstruction = add_obstruction(db, project.project_id, ObstructionCreate(
        roof_plane_id=roof.roof_plane_id, obstruction_type="chimney", height_m=1.2,
        polygon_local_m=[[2, 2], [3, 2], [3, 3], [2, 3]], label="chimney",
    ))
    geometry = get_project_geometry(db, project.project_id)
    assert geometry.project.project_id == "prj_test"
    assert geometry.site.site_id == site.site_id
    assert geometry.roof_planes[0].area_m2 == 50
    assert geometry.roof_planes[0].edge_zone_depth_m == 1.2
    assert geometry.obstructions[0].obstruction_id == obstruction.obstruction_id


def test_missing_height_and_unknown_roof_type_blocks_mounting_readiness():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_blocked", title="Blocked Project"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type="unknown", pitch_deg=35, azimuth_deg=180, height_m=None,
        polygon_local_m=[[0, 0], [6, 0], [6, 4], [0, 4]],
    ))
    report = validate_project_geometry(db, project.project_id)
    codes = {issue.code for issue in report.issues}
    assert report.status == "blocked"
    assert "ROOF_HEIGHT_MISSING" in codes
    assert "ROOF_TYPE_UNKNOWN" in codes
    precheck = run_project_mounting_precheck(db, project.project_id, created_by="tester")
    assert precheck.structural_truth_state == "S0_unknown"
    assert precheck.calculation_status == "survey_required"


def test_known_roof_precheck_requires_manufacturer_calculation_not_final_approval():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_known", title="Known Roof Project"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type="tiled_pitched", pitch_deg=35, azimuth_deg=180, height_m=6.0,
        polygon_local_m=[[0, 0], [10, 0], [10, 5], [0, 5]],
    ))
    precheck = run_project_mounting_precheck(db, project.project_id, created_by="tester")
    assert precheck.structural_truth_state == "S2_manufacturer_calc_required"
    assert precheck.calculation_status == "manufacturer_calc_required"
    assert db.query(CalculationRun).filter(CalculationRun.project_id == project.project_id).count() == 1


def test_project_snapshot_hash_is_stable_when_geometry_does_not_change():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_snap", title="Snapshot Project"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type="tiled_pitched", pitch_deg=35, azimuth_deg=180, height_m=6.0,
        polygon_local_m=[[0, 0], [10, 0], [10, 5], [0, 5]],
    ))
    s1 = create_project_snapshot(db, project.project_id, created_by="tester")
    s2 = create_project_snapshot(db, project.project_id, created_by="tester")
    assert s1.snapshot_hash_sha256 == s2.snapshot_hash_sha256
    assert s1.project_snapshot_id != s2.project_snapshot_id


def test_geometry_self_check_passes():
    db = make_db()
    result = project_geometry_self_check(db)
    assert result["status"] == "ok"
    assert result["blocked_missing_height"] is True
    assert result["ok_known_roof_state"] == "S2_manufacturer_calc_required"
    assert result["snapshot_hash_stable"] is True

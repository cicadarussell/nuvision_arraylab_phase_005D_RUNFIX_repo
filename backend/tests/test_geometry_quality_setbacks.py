from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.db_models import Base
from app.schemas.geometry import ObstructionCreate, ProjectCreate, RoofPlaneCreate, SiteCreate
from app.services.geometry_quality import (
    build_geometry_quality_report,
    create_geometry_quality_snapshot,
    export_packer_allowed_area,
    geometry_quality_self_check,
)
from app.services.project_geometry import add_obstruction, add_roof_plane, create_project, upsert_site


def make_db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def make_project_with_roof_and_obstruction():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_quality", title="Quality Test"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, source_type="postcode_lookup"))
    roof = add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type="tiled_pitched",
        pitch_deg=35,
        azimuth_deg=180,
        height_m=6.0,
        polygon_local_m=[[0, 0], [12, 0], [12, 6], [0, 6]],
        source_confidence=0.65,
    ))
    add_obstruction(db, project.project_id, ObstructionCreate(
        roof_plane_id=roof.roof_plane_id,
        obstruction_type="chimney",
        label="chimney",
        height_m=1.2,
        polygon_local_m=[[4, 2], [5, 2], [5, 3], [4, 3]],
    ))
    return db, project.project_id


def test_geometry_quality_report_reduces_usable_area_for_setbacks_and_obstructions():
    db, project_id = make_project_with_roof_and_obstruction()
    report = build_geometry_quality_report(db, project_id)
    assert report.status == "warnings"
    assert report.report_hash_sha256
    row = report.roof_planes[0]
    assert row.original_area_m2 == 72.0
    assert row.usable_area_m2 and row.usable_area_m2 < row.original_area_m2
    assert row.usable_polygon_local_m
    assert "OBSTRUCTION_OVERLAP_CUTOUT" in row.issue_codes
    assert report.summary["total_usable_area_m2"] == row.usable_area_m2


def test_invalid_self_crossing_polygon_blocks_quality_and_packer_export():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_bad_quality", title="Bad Quality"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type="tiled_pitched",
        pitch_deg=35,
        azimuth_deg=180,
        height_m=6.0,
        polygon_local_m=[[0, 0], [6, 0], [0, 6], [6, 6]],
    ))
    report = build_geometry_quality_report(db, project.project_id)
    codes = {issue.code for issue in report.validation_report.issues}
    assert report.status == "blocked"
    assert "ROOF_POLYGON_INVALID" in codes
    export = export_packer_allowed_area(db, project.project_id)
    assert export.payload["validation_status"] == "blocked"
    assert "ROOF_POLYGON_INVALID" in export.payload["issue_codes"]


def test_geometry_quality_snapshot_is_hash_stable_and_stored_as_evidence():
    db, project_id = make_project_with_roof_and_obstruction()
    snap = create_geometry_quality_snapshot(db, project_id, created_by="tester")
    assert snap.snapshot_hash_sha256 == snap.quality_report_hash_sha256
    second = build_geometry_quality_report(db, project_id)
    assert snap.quality_report_hash_sha256 == second.report_hash_sha256


def test_geometry_quality_self_check_passes():
    db = make_db()
    check = geometry_quality_self_check(db)
    assert check.status == "ok"
    assert check.bad_polygon_blocked is True
    assert check.obstruction_reduces_usable_area is True
    assert check.quality_hash_stable is True


def test_routes_include_geometry_quality_endpoints():
    client = TestClient(app)
    restore = client.get("/api/debug/restore-check")
    assert restore.status_code == 200
    assert restore.json()["current_phase"] == "NVA_005D_RUNFIX"
    assert restore.json()["checks"]["has_geometry_quality_reports"] is True
    route_map = client.get("/api/debug/route-map").json()
    routes = {item["path"] for item in route_map["routes"]}
    assert "/api/debug/geometry-quality-self-check" in routes
    assert "/api/projects/{project_id}/geometry/quality" in routes
    assert "/api/projects/{project_id}/geometry/packer-allowed-area" in routes

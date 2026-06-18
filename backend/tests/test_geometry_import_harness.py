from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db_models import Base
from app.schemas.geometry import CicadaPlannerImportRequest, ProjectCreate
from app.schemas.project import RoofType
from app.services.geometry_import import (
    cicada_array_outline_local_m,
    export_project_geometry,
    geometry_import_self_check,
    import_cicada_planner_geometry,
)
from app.services.project_geometry import create_project, get_project_geometry, run_project_mounting_precheck


def make_db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def sample_planner_payload() -> dict:
    return {
        "app": "CICADA Solar Field Planner V2",
        "postcode_centre": {"lat": 50.815584, "lng": -3.280844},
        "arrays": [
            {"id": 1, "name": "Pilot Array", "lat": 50.815584, "lng": -3.280844, "widthM": 11, "slopeDepthM": 6, "tiltDeg": 40, "azimuthDeg": 180, "heightLimit": 4, "gapSide": "right"},
            {"id": 2, "name": "Scale Array", "lat": 50.81575, "lng": -3.28055, "widthM": 46, "slopeDepthM": 8, "tiltDeg": 35, "azimuthDeg": 180, "heightLimit": 4, "gapSide": "right"},
        ],
        "boundary": [],
        "boundary_closed": False,
    }


def test_cicada_array_outline_is_four_points_and_positive_area():
    outline = cicada_array_outline_local_m(50.815584, -3.280844, sample_planner_payload()["arrays"][0])
    assert len(outline) == 4
    # Local import should not collapse all points to the centre.
    assert len({tuple(p) for p in outline}) == 4


def test_import_cicada_planner_creates_site_roof_planes_and_snapshot():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_import", title="Import Test"))
    result = import_cicada_planner_geometry(db, project.project_id, CicadaPlannerImportRequest(
        planner_payload=sample_planner_payload(),
        created_by="tester",
        default_roof_type=RoofType.ground_mount,
        default_height_m=4.0,
        source_confidence=0.7,
    ))
    geometry = get_project_geometry(db, project.project_id)
    assert result.import_status == "imported"
    assert result.arrays_seen == 2
    assert result.roof_planes_created == 2
    assert result.geometry_snapshot_hash_sha256
    assert geometry.site is not None
    assert geometry.site.lat == 50.815584
    assert len(geometry.roof_planes) == 2
    assert all(r.area_m2 and r.area_m2 > 0 for r in geometry.roof_planes)


def test_imported_known_geometry_precheck_is_manufacturer_calc_required_not_approved():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_import_precheck", title="Import Precheck Test"))
    import_cicada_planner_geometry(db, project.project_id, CicadaPlannerImportRequest(
        planner_payload=sample_planner_payload(),
        default_roof_type=RoofType.ground_mount,
        default_height_m=4.0,
        source_confidence=0.7,
    ))
    precheck = run_project_mounting_precheck(db, project.project_id, created_by="tester")
    assert precheck.structural_truth_state == "S2_manufacturer_calc_required"
    assert precheck.calculation_status == "manufacturer_calc_required"


def test_import_rejects_empty_array_payload_for_array_import():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_bad_import", title="Bad Import"))
    try:
        import_cicada_planner_geometry(db, project.project_id, CicadaPlannerImportRequest(
            planner_payload={"app": "CICADA Solar Field Planner V2", "postcode_centre": {"lat": 50.8, "lng": -3.2}, "arrays": []},
            default_roof_type=RoofType.ground_mount,
        ))
    except Exception as exc:
        assert "no arrays" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("empty planner import should fail")


def test_export_project_geometry_creates_snapshot_and_truth_boundary():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_export", title="Export Test"))
    import_cicada_planner_geometry(db, project.project_id, CicadaPlannerImportRequest(
        planner_payload=sample_planner_payload(),
        default_roof_type=RoofType.ground_mount,
        default_height_m=4.0,
        source_confidence=0.7,
    ))
    export = export_project_geometry(db, project.project_id)
    assert export.export_kind == "arraylab_local_geometry_v0"
    assert export.geometry_snapshot_hash_sha256
    assert export.payload["truth_boundary"].startswith("local geometry export only")
    assert len(export.payload["roof_planes"]) == 2


def test_geometry_import_self_check_passes():
    db = make_db()
    result = geometry_import_self_check(db)
    assert result["status"] == "ok"
    assert result["roof_planes_created"] == 2
    assert result["precheck_state"] == "S2_manufacturer_calc_required"

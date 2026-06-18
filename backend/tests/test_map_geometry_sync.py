from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.db_models import Base
from app.schemas.geometry import GeoJsonRoofImportRequest, ProjectCreate, SiteCreate
from app.services.map_geometry_sync import export_project_geojson, import_geojson_roof, map_sync_self_check
from app.services.project_geometry import create_project, upsert_site, validate_project_geometry


def make_db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def sample_geojson():
    return {
        "type": "Feature",
        "properties": {"label": "roof"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [-3.280844, 50.815584],
                [-3.280744, 50.815584],
                [-3.280744, 50.815654],
                [-3.280844, 50.815654],
                [-3.280844, 50.815584],
            ]],
        },
    }


def test_geojson_roof_import_creates_local_roof_snapshot_and_warnings():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_geojson", title="GeoJSON Import"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.815584, lon=-3.280844, source_type="postcode_lookup"))
    result = import_geojson_roof(db, project.project_id, GeoJsonRoofImportRequest(
        geojson=sample_geojson(), roof_type="tiled_pitched", pitch_deg=35, azimuth_deg=180, height_m=6.0, created_by="tester"
    ))
    assert result.import_status == "imported"
    assert result.points_imported == 4
    assert result.area_m2 and result.area_m2 > 0
    assert result.geometry_snapshot_hash_sha256
    validation = validate_project_geometry(db, project.project_id)
    assert validation.status in {"ok", "warnings"}


def test_geojson_roof_import_rejects_unclosed_polygon_ring():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_bad_geojson", title="Bad GeoJSON"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.815584, lon=-3.280844, source_type="postcode_lookup"))
    bad = sample_geojson()
    bad["geometry"]["coordinates"][0] = bad["geometry"]["coordinates"][0][:-1]
    try:
        import_geojson_roof(db, project.project_id, GeoJsonRoofImportRequest(geojson=bad))
    except Exception as exc:
        assert "closed" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("unclosed GeoJSON ring should fail")


def test_geojson_export_returns_feature_collection_with_closed_ring():
    db = make_db()
    project = create_project(db, ProjectCreate(project_id="prj_export_geojson", title="Export GeoJSON"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.815584, lon=-3.280844, source_type="postcode_lookup"))
    import_geojson_roof(db, project.project_id, GeoJsonRoofImportRequest(geojson=sample_geojson(), height_m=6.0, roof_type="tiled_pitched"))
    exported = export_project_geojson(db, project.project_id)
    assert exported.export_kind == "arraylab_project_geojson_v0"
    features = exported.feature_collection["features"]
    assert len(features) == 1
    ring = features[0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    assert features[0]["properties"]["truth_boundary"] == "planning geometry only; not structural approval"


def test_map_sync_self_check_passes():
    db = make_db()
    result = map_sync_self_check(db)
    assert result["status"] == "ok"
    assert result["closed_ring_exported"] is True
    assert result["exported_feature_count"] == 1


def test_routes_include_map_sync_and_geojson_endpoints():
    client = TestClient(app)
    restore = client.get("/api/debug/restore-check")
    assert restore.status_code == 200
    assert restore.json()["current_phase"] == "NVA_005D_RUNFIX"
    assert restore.json()["checks"]["has_maplibre_draft_ui"] is True
    route_map = client.get("/api/debug/route-map").json()
    routes = {item["path"] for item in route_map["routes"]}
    assert "/api/debug/map-sync-self-check" in routes
    assert "/api/projects/{project_id}/geometry/import-geojson-roof" in routes
    assert "/api/projects/{project_id}/geometry/export-geojson" in routes

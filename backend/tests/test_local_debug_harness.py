from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_restore_check_endpoint_reports_current_phase():
    client = TestClient(app)
    response = client.get("/api/debug/restore-check")
    assert response.status_code == 200
    payload = response.json()
    assert payload["current_phase"] == "NVA_005D_RUNFIX"
    assert payload["checks"]["has_datasheet_downloader_worker"] is True
    assert payload["checks"]["has_cicada_planner_import"] is True
    assert payload["checks"]["has_roof_drawing_test_harness"] is True
    assert payload["checks"]["has_maplibre_draft_ui"] is True
    assert payload["checks"]["has_geojson_roof_sync"] is True


def test_route_map_includes_datasheet_worker_and_debug_routes():
    client = TestClient(app)
    response = client.get("/api/debug/route-map")
    assert response.status_code == 200
    routes = {item["path"] for item in response.json()["routes"]}
    assert "/api/debug/restore-check" in routes
    assert "/api/datasheet-download-jobs/{job_id}/run" in routes
    assert "/api/datasheet-download-jobs/debug" in routes
    assert "/api/debug/geometry-import-self-check" in routes
    assert "/api/projects/{project_id}/geometry/import-cicada-planner" in routes
    assert "/api/projects/{project_id}/geometry/export" in routes
    assert "/api/debug/map-sync-self-check" in routes
    assert "/api/debug/geometry-quality-self-check" in routes
    assert "/api/projects/{project_id}/geometry/import-geojson-roof" in routes
    assert "/api/projects/{project_id}/geometry/export-geojson" in routes

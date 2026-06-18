from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_shade_preview_self_check_endpoint():
    with TestClient(app) as client:
        restore = client.get("/api/debug/restore-check")
        assert restore.status_code == 200
        assert restore.json()["current_phase"] == "NVA_005D_RUNFIX"

        response = client.get("/api/debug/shade-preview-self-check")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["shade_changes_with_obstruction_height"] is True
        assert payload["shade_changes_with_obstruction_position"] is True
        assert payload["missing_obstruction_height_blocks"] is True
        assert payload["worst_panel_list_present"] is True
        assert payload["sample_bounds_ok"] is True


def test_route_map_includes_shade_preview_route():
    with TestClient(app) as client:
        paths = {route["path"] for route in client.get("/api/debug/route-map").json()["routes"]}
    assert "/api/debug/shade-preview-self-check" in paths
    assert "/api/projects/{project_id}/shade/preview" in paths

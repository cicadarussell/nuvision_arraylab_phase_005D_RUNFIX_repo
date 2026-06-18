from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient
from app.main import app


REQUIRED_ENDPOINTS = [
    "/api/debug/restore-check",
    "/api/debug/route-map",
    "/api/debug/db-self-check",
    "/api/debug/datasheet-self-check",
    "/api/datasheet-download-jobs/debug",
    "/api/datasheet-ocr-jobs",
    "/api/debug/geometry-self-check",
    "/api/debug/geometry-import-self-check",
    "/api/debug/map-sync-self-check",
    "/api/debug/geometry-quality-self-check",
    "/api/debug/panel-packing-self-check",
    "/api/debug/panel-packing-governance-self-check",
    "/api/debug/yield-preview-self-check",
    "/api/debug/pvgis-cache",
    "/api/debug/solar-geometry-self-check",
    "/api/debug/shade-preview-self-check",
    "/api/catalogue/reviewed-panel-models",
    "/api/yield/assumption-sets",
    "/api/projects",
]


def main() -> int:
    failures: list[dict] = []
    with TestClient(app) as client:
        for path in REQUIRED_ENDPOINTS:
            response = client.get(path)
            if response.status_code >= 400:
                failures.append({"path": path, "status_code": response.status_code, "body": response.text[:300]})
        route_map = client.get("/api/debug/route-map").json()

    route_paths = {route["path"] for route in route_map.get("routes", [])}
    for path in ["/api/datasheet-download-jobs/{job_id}/run", "/api/debug/restore-check", "/api/projects/{project_id}/mounting-precheck", "/api/projects/{project_id}/geometry/import-cicada-planner", "/api/projects/{project_id}/geometry/export", "/api/projects/{project_id}/geometry/import-geojson-roof", "/api/projects/{project_id}/geometry/export-geojson", "/api/projects/{project_id}/geometry/quality", "/api/projects/{project_id}/geometry/packer-allowed-area", "/api/projects/{project_id}/panel-packing/run", "/api/projects/{project_id}/panel-packing/runs/{calculation_run_id}/candidate-export", "/api/projects/{project_id}/panel-packing/runs/{calculation_run_id}/selected-layout-export", "/api/projects/{project_id}/panel-packing/runs/{calculation_run_id}/overrides", "/api/projects/{project_id}/panel-packing/overrides", "/api/projects/{project_id}/panel-packing/layout-edit-contract", "/api/catalogue/reviewed-panel-models", "/api/debug/yield-preview-self-check", "/api/debug/pvgis-cache", "/api/yield/assumption-sets", "/api/projects/{project_id}/yield/preview", "/api/projects/{project_id}/yield/runs/{calculation_run_id}", "/api/projects/{project_id}/yield/solar-geometry-debug", "/api/debug/shade-preview-self-check", "/api/projects/{project_id}/shade/preview"]:
        if path not in route_paths:
            failures.append({"path": path, "error": "missing from route map"})
    result = {
        "status": "failed" if failures else "ok",
        "checked_endpoints": REQUIRED_ENDPOINTS,
        "route_count": route_map.get("route_count"),
        "failures": failures,
        "truth_boundary": "Smoke checks prove routes exist; they do not prove final solar engineering correctness.",
    }
    print(json.dumps(result, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

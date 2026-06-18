from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from app.schemas.geometry import (
    GeoJsonRoofImportRead,
    GeoJsonRoofImportRequest,
    ProjectGeoJsonExportRead,
    RoofPlaneCreate,
    SourceConfidenceKind,
    SiteCreate,
)
from app.services.project_geometry import (
    ProjectGeometryError,
    add_roof_plane,
    create_project,
    create_project_snapshot,
    get_project_geometry,
    validate_project_geometry,
    upsert_site,
)
from app.schemas.geometry import ProjectCreate

METRES_PER_DEG_LAT = 111_320.0


def _require_site_origin(db: Session, project_id: str) -> tuple[float, float]:
    geometry = get_project_geometry(db, project_id)
    if geometry.site is None or geometry.site.lat is None or geometry.site.lon is None:
        raise ProjectGeometryError("Project site needs lat/lon before GeoJSON roof sync")
    return float(geometry.site.lat), float(geometry.site.lon)


def lonlat_to_local_m(lon: float, lat: float, origin_lat: float, origin_lon: float) -> list[float]:
    x = (lon - origin_lon) * METRES_PER_DEG_LAT * math.cos(math.radians(origin_lat))
    y = (lat - origin_lat) * METRES_PER_DEG_LAT
    return [round(x, 3), round(y, 3)]


def local_m_to_lonlat(x: float, y: float, origin_lat: float, origin_lon: float) -> list[float]:
    lon = origin_lon + x / (METRES_PER_DEG_LAT * math.cos(math.radians(origin_lat)))
    lat = origin_lat + y / METRES_PER_DEG_LAT
    return [round(lon, 8), round(lat, 8)]


def _polygon_coords_from_geojson(geojson: dict[str, Any]) -> list[list[float]]:
    obj = geojson
    if obj.get("type") == "FeatureCollection":
        features = obj.get("features") or []
        polys = [f for f in features if (f.get("geometry") or {}).get("type") == "Polygon"]
        if not polys:
            raise ProjectGeometryError("GeoJSON FeatureCollection contains no Polygon feature")
        if len(polys) > 1:
            raise ProjectGeometryError("GeoJSON roof import accepts exactly one Polygon at a time")
        obj = polys[0]
    if obj.get("type") == "Feature":
        obj = obj.get("geometry") or {}
    if obj.get("type") != "Polygon":
        raise ProjectGeometryError("GeoJSON roof import expects a Polygon geometry")
    coordinates = obj.get("coordinates") or []
    if not coordinates or not isinstance(coordinates[0], list):
        raise ProjectGeometryError("GeoJSON Polygon needs an exterior ring")
    ring = coordinates[0]
    if len(ring) < 4:
        raise ProjectGeometryError("GeoJSON Polygon ring must have at least four coordinates including closure")
    if ring[0] != ring[-1]:
        raise ProjectGeometryError("GeoJSON Polygon ring must be closed: first coordinate must equal last coordinate")
    # Remove duplicate closing point for the local geometry table.
    open_ring = ring[:-1]
    for coord in open_ring:
        if not isinstance(coord, list) or len(coord) < 2:
            raise ProjectGeometryError("GeoJSON coordinates must be [longitude, latitude]")
        lon, lat = float(coord[0]), float(coord[1])
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ProjectGeometryError("GeoJSON coordinates must be longitude/latitude decimal degrees")
    return [[float(c[0]), float(c[1])] for c in open_ring]


def import_geojson_roof(db: Session, project_id: str, payload: GeoJsonRoofImportRequest) -> GeoJsonRoofImportRead:
    origin_lat, origin_lon = _require_site_origin(db, project_id)
    lonlat_ring = _polygon_coords_from_geojson(payload.geojson)
    local_polygon = [lonlat_to_local_m(lon, lat, origin_lat, origin_lon) for lon, lat in lonlat_ring]
    roof = add_roof_plane(db, project_id, RoofPlaneCreate(
        label=payload.label or "MapLibre drawn roof",
        roof_type=payload.roof_type,
        pitch_deg=payload.pitch_deg,
        azimuth_deg=payload.azimuth_deg,
        height_m=payload.height_m,
        polygon_local_m=local_polygon,
        source_type=SourceConfidenceKind.map_drawn,
        source_confidence=payload.source_confidence,
    ))
    validation = validate_project_geometry(db, project_id)
    snapshot = create_project_snapshot(db, project_id, snapshot_kind="maplibre_roof_sync", created_by=payload.created_by)
    warnings = [f"{item.code}: {item.message}" for item in validation.issues]
    return GeoJsonRoofImportRead(
        project_id=project_id,
        roof_plane_id=roof.roof_plane_id,
        import_status="imported",
        points_imported=len(local_polygon),
        area_m2=roof.area_m2,
        edge_zone_depth_m=roof.edge_zone_depth_m,
        validation_status=validation.status,
        geometry_snapshot_id=snapshot.project_snapshot_id,
        geometry_snapshot_hash_sha256=snapshot.snapshot_hash_sha256,
        warnings=warnings,
    )


def export_project_geojson(db: Session, project_id: str) -> ProjectGeoJsonExportRead:
    geometry = get_project_geometry(db, project_id)
    if geometry.site is None or geometry.site.lat is None or geometry.site.lon is None:
        raise ProjectGeometryError("Project site needs lat/lon before GeoJSON export")
    origin_lat, origin_lon = float(geometry.site.lat), float(geometry.site.lon)
    features: list[dict[str, Any]] = []
    for roof in geometry.roof_planes:
        if not roof.polygon_local_m:
            continue
        ring = [local_m_to_lonlat(float(x), float(y), origin_lat, origin_lon) for x, y in roof.polygon_local_m]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        features.append({
            "type": "Feature",
            "properties": {
                "kind": "roof_plane",
                "roof_plane_id": roof.roof_plane_id,
                "label": roof.label,
                "roof_type": roof.roof_type,
                "pitch_deg": roof.pitch_deg,
                "azimuth_deg": roof.azimuth_deg,
                "height_m": roof.height_m,
                "area_m2": roof.area_m2,
                "edge_zone_depth_m": roof.edge_zone_depth_m,
                "truth_boundary": "planning geometry only; not structural approval",
            },
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    snapshot = create_project_snapshot(db, project_id, snapshot_kind="geojson_export", created_by="system")
    return ProjectGeoJsonExportRead(
        project_id=project_id,
        geometry_snapshot_id=snapshot.project_snapshot_id,
        geometry_snapshot_hash_sha256=snapshot.snapshot_hash_sha256,
        feature_collection={"type": "FeatureCollection", "features": features},
        truth_boundary="GeoJSON export is roof planning evidence only; manufacturer/engineer approval is still required for structural claims.",
    )


def map_sync_self_check(db: Session) -> dict[str, Any]:
    project = create_project(db, ProjectCreate(title="Map sync self-check", created_by="self_check"))
    upsert_site(db, project.project_id, SiteCreate(
        postcode="EX14 3JF", lat=50.815584, lon=-3.280844, source_type=SourceConfidenceKind.postcode_lookup, source_confidence=0.75,
    ))
    # Tiny approx rectangle near origin. Coordinates are lon/lat and ring is closed per RFC 7946.
    geojson = {
        "type": "Feature",
        "properties": {"label": "self-check roof"},
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
    imported = import_geojson_roof(db, project.project_id, GeoJsonRoofImportRequest(
        geojson=geojson,
        label="self-check roof",
        roof_type="tiled_pitched",
        pitch_deg=35,
        azimuth_deg=180,
        height_m=6.0,
        source_confidence=0.65,
        created_by="self_check",
    ))
    exported = export_project_geojson(db, project.project_id)
    features = exported.feature_collection.get("features", [])
    first_ring = features[0]["geometry"]["coordinates"][0] if features else []
    closed = bool(first_ring and first_ring[0] == first_ring[-1])
    ok = imported.import_status == "imported" and imported.area_m2 and imported.area_m2 > 0 and exported.geometry_snapshot_hash_sha256 and closed
    return {
        "status": "ok" if ok else "failed",
        "project_id": project.project_id,
        "roof_plane_id": imported.roof_plane_id,
        "imported_area_m2": imported.area_m2,
        "exported_feature_count": len(features),
        "validation_status": imported.validation_status,
        "closed_ring_exported": closed,
        "truth_boundary": "Map sync proves frontend/backend geometry flow, not structural approval.",
    }

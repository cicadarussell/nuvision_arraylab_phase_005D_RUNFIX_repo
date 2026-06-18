from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from app.schemas.geometry import (
    CicadaPlannerImportRead,
    CicadaPlannerImportRequest,
    ProjectGeometryExportRead,
    RoofPlaneCreate,
    SiteCreate,
)
from app.schemas.project import RoofType
from app.services.hash_utils import stable_json_hash
from app.services.project_geometry import (
    ProjectGeometryError,
    add_obstruction,
    add_roof_plane,
    create_project_snapshot,
    get_project,
    project_geometry_payload,
    upsert_site,
)
from app.schemas.geometry import ObstructionCreate, ObstructionType, SourceConfidenceKind


EARTH_METRES_PER_DEGREE = 111_320.0


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except (TypeError, ValueError):
        return default


def _clamp_angle(value: float | None, default: float = 180.0) -> float:
    if value is None or not math.isfinite(value):
        value = default
    return value % 360.0


def lat_lng_to_local_m(origin_lat: float, origin_lng: float, lat: float, lng: float) -> tuple[float, float]:
    """Approximate local east/north conversion for small solar layouts.

    This mirrors the older CICADA planner. It is acceptable for local drawing/import
    debug over field/roof scales, but is not a cadastral survey method. Humans did in
    fact invent total stations for a reason.
    """

    north = (lat - origin_lat) * EARTH_METRES_PER_DEGREE
    east = (lng - origin_lng) * EARTH_METRES_PER_DEGREE * math.cos(math.radians(origin_lat))
    return round(east, 3), round(north, 3)


def local_to_east_north(x_local: float, y_local: float, face_azimuth_deg: float) -> tuple[float, float]:
    """Same orientation convention as CICADA Solar Field Planner V2.

    x_local moves across array width. y_local moves up/down the projected ground-depth
    direction. face_azimuth_deg is where the panel face points.
    """

    rear_bearing = math.radians((face_azimuth_deg + 180.0) % 360.0)
    width_bearing = rear_bearing + math.pi / 2.0
    east = x_local * math.sin(width_bearing) + y_local * math.sin(rear_bearing)
    north = x_local * math.cos(width_bearing) + y_local * math.cos(rear_bearing)
    return east, north


def cicada_array_outline_local_m(origin_lat: float, origin_lng: float, arr: dict) -> list[list[float]]:
    lat = _num(arr.get("lat"))
    lng = _num(arr.get("lng"))
    if lat is None or lng is None:
        raise ProjectGeometryError("CICADA planner array is missing valid lat/lng")
    centre_east, centre_north = lat_lng_to_local_m(origin_lat, origin_lng, lat, lng)
    width_m = max(0.1, _num(arr.get("widthM"), 0.0) or 0.0)
    slope_depth_m = max(0.1, _num(arr.get("slopeDepthM"), 0.0) or 0.0)
    tilt_deg = max(0.0, min(75.0, _num(arr.get("tiltDeg"), 0.0) or 0.0))
    azimuth_deg = _clamp_angle(_num(arr.get("azimuthDeg"), 180.0), 180.0)
    ground_depth_m = slope_depth_m * math.cos(math.radians(tilt_deg))
    corners = [
        (-width_m / 2.0, -ground_depth_m / 2.0),
        (width_m / 2.0, -ground_depth_m / 2.0),
        (width_m / 2.0, ground_depth_m / 2.0),
        (-width_m / 2.0, ground_depth_m / 2.0),
    ]
    out: list[list[float]] = []
    for x, y in corners:
        east, north = local_to_east_north(x, y, azimuth_deg)
        out.append([round(centre_east + east, 3), round(centre_north + north, 3)])
    return out


def _planner_origin(planner_payload: dict) -> tuple[float, float, str | None]:
    centre = planner_payload.get("postcode_centre") or {}
    lat = _num(centre.get("lat"))
    lng = _num(centre.get("lng"))
    postcode = centre.get("postcode") or "EX14 3JF" if planner_payload.get("app") else None
    arrays = planner_payload.get("arrays") or []
    if (lat is None or lng is None) and arrays:
        lat = _num(arrays[0].get("lat"))
        lng = _num(arrays[0].get("lng"))
    if lat is None or lng is None:
        raise ProjectGeometryError("CICADA planner import needs postcode_centre lat/lng or at least one array with lat/lng")
    return lat, lng, postcode


def import_cicada_planner_geometry(db: Session, project_id: str, request: CicadaPlannerImportRequest) -> CicadaPlannerImportRead:
    if get_project(db, project_id) is None:
        raise ProjectGeometryError("Project not found")
    data = request.planner_payload
    if not isinstance(data, dict):
        raise ProjectGeometryError("Planner payload must be a JSON object")
    arrays = data.get("arrays") or []
    if not isinstance(arrays, list):
        raise ProjectGeometryError("Planner payload arrays must be a list")
    if request.import_arrays_as_roof_planes and not arrays:
        raise ProjectGeometryError("Planner payload has no arrays to import")

    origin_lat, origin_lng, postcode = _planner_origin(data)
    warnings: list[str] = []
    site = upsert_site(db, project_id, SiteCreate(
        postcode=postcode,
        lat=origin_lat,
        lon=origin_lng,
        timezone="Europe/London",
        source_type=SourceConfidenceKind.imported,
        source_confidence=request.source_confidence,
        notes="Imported from CICADA Solar Field Planner V2 export. Geometry evidence only; survey still required.",
    ))
    if request.source_confidence < 0.6:
        warnings.append("Imported planner geometry has low/medium source confidence; survey before final design.")

    roof_planes_created = 0
    if request.import_arrays_as_roof_planes:
        for idx, arr in enumerate(arrays, start=1):
            polygon = cicada_array_outline_local_m(origin_lat, origin_lng, arr)
            roof_type = request.default_roof_type
            if roof_type == RoofType.unknown and str(data.get("app", "")).startswith("CICADA Solar Field Planner"):
                roof_type = RoofType.ground_mount
            height_m = request.default_height_m
            if height_m is None:
                height_m = _num(arr.get("heightLimit"), None)
                warnings.append(f"Array {idx} used heightLimit as precheck height; verify real structural height.")
            label = arr.get("name") or f"Imported array {idx}"
            add_roof_plane(db, project_id, RoofPlaneCreate(
                roof_plane_id=f"roof_cicada_{arr.get('id', idx)}",
                label=str(label),
                roof_type=roof_type,
                pitch_deg=max(0.0, min(75.0, _num(arr.get("tiltDeg"), 0.0) or 0.0)),
                azimuth_deg=_clamp_angle(_num(arr.get("azimuthDeg"), 180.0), 180.0),
                height_m=height_m,
                polygon_local_m=polygon,
                source_type=SourceConfidenceKind.imported,
                source_confidence=request.source_confidence,
            ))
            roof_planes_created += 1

    obstructions_created = 0
    boundary = data.get("boundary") or []
    if request.import_boundary_as_obstruction and isinstance(boundary, list) and len(boundary) >= 3:
        polygon = []
        for point in boundary:
            lat = _num(point.get("lat"))
            lng = _num(point.get("lng"))
            if lat is None or lng is None:
                continue
            east, north = lat_lng_to_local_m(origin_lat, origin_lng, lat, lng)
            polygon.append([east, north])
        if len(polygon) >= 3:
            add_obstruction(db, project_id, ObstructionCreate(
                obstruction_type=ObstructionType.manual_block,
                label="Imported field boundary reference",
                height_m=0.0,
                polygon_local_m=polygon,
                source_type=SourceConfidenceKind.imported,
                source_confidence=request.source_confidence,
                notes="Boundary reference imported as non-shading obstruction/debug geometry. Do not use as roof structure.",
            ))
            obstructions_created += 1

    snapshot = create_project_snapshot(db, project_id, snapshot_kind="cicada_planner_import_geometry", created_by=request.created_by)
    return CicadaPlannerImportRead(
        project_id=project_id,
        import_status="imported",
        planner_app=data.get("app"),
        arrays_seen=len(arrays),
        roof_planes_created=roof_planes_created,
        obstructions_created=obstructions_created,
        site_created_or_updated=bool(site.site_id),
        geometry_snapshot_id=snapshot.project_snapshot_id,
        geometry_snapshot_hash_sha256=snapshot.snapshot_hash_sha256,
        warnings=warnings,
    )


def export_project_geometry(db: Session, project_id: str) -> ProjectGeometryExportRead:
    payload = project_geometry_payload(db, project_id)
    snapshot = create_project_snapshot(db, project_id, snapshot_kind="geometry_export", created_by="system_export")
    export_payload = {
        "project": payload["project"],
        "site": payload["site"],
        "roof_planes": payload["roof_planes"],
        "obstructions": payload["obstructions"],
        "truth_boundary": "local geometry export only; not structural approval, not final survey.",
        "payload_hash_sha256": stable_json_hash(payload),
    }
    return ProjectGeometryExportRead(
        project_id=project_id,
        geometry_snapshot_id=snapshot.project_snapshot_id,
        geometry_snapshot_hash_sha256=snapshot.snapshot_hash_sha256,
        payload=export_payload,
    )


def geometry_import_self_check(db: Session) -> dict:
    from app.schemas.geometry import ProjectCreate
    from app.services.project_geometry import create_project, run_project_mounting_precheck

    project = create_project(db, ProjectCreate(title="Geometry import self-check", created_by="self_check"))
    sample = {
        "app": "CICADA Solar Field Planner V2",
        "postcode_centre": {"lat": 50.815584, "lng": -3.280844},
        "arrays": [
            {"id": 1, "name": "Pilot Array", "lat": 50.815584, "lng": -3.280844, "widthM": 11, "slopeDepthM": 6, "tiltDeg": 40, "azimuthDeg": 180, "heightLimit": 4, "gapSide": "right"},
            {"id": 2, "name": "Scale Array", "lat": 50.815700, "lng": -3.280600, "widthM": 46, "slopeDepthM": 8, "tiltDeg": 35, "azimuthDeg": 180, "heightLimit": 4, "gapSide": "right"},
        ],
        "boundary": [],
        "boundary_closed": False,
    }
    result = import_cicada_planner_geometry(db, project.project_id, CicadaPlannerImportRequest(
        planner_payload=sample,
        created_by="self_check",
        default_roof_type=RoofType.ground_mount,
        default_height_m=4.0,
        source_confidence=0.7,
    ))
    precheck = run_project_mounting_precheck(db, project.project_id, created_by="self_check")
    export = export_project_geometry(db, project.project_id)
    return {
        "status": "ok" if result.roof_planes_created == 2 and precheck.structural_truth_state.value == "S2_manufacturer_calc_required" else "failed",
        "project_id": project.project_id,
        "arrays_seen": result.arrays_seen,
        "roof_planes_created": result.roof_planes_created,
        "snapshot_hash": result.geometry_snapshot_hash_sha256,
        "precheck_state": precheck.structural_truth_state.value,
        "export_hash": export.payload["payload_hash_sha256"],
    }

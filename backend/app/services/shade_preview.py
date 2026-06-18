from __future__ import annotations

from datetime import datetime
from math import cos, radians, sin, tan
from uuid import uuid4

from shapely.affinity import translate
from shapely.geometry import MultiPoint, Point, Polygon
from sqlalchemy.orm import Session

from app.models.db_models import CalculationRun
from app.schemas.calculation import CalculationRunCreate, CalculationRunType
from app.schemas.geometry import ObstructionCreate, PanelPackingRequest, ProjectCreate, RoofPlaneCreate, SiteCreate
from app.schemas.project import RoofType
from app.schemas.shade import PanelShadeSummaryRead, ShadePreviewRequest, ShadePreviewResultRead, ShadePreviewSelfCheckRead
from app.schemas.validation import Severity, ValidationArea, ValidationIssue, ValidationReport
from app.services.calculation_run import build_calculation_run
from app.services.hash_utils import stable_json_hash
from app.services.panel_packing import build_selected_layout_export_payload, run_panel_packing
from app.services.project_geometry import add_obstruction, add_roof_plane, create_project, get_project_geometry, upsert_site
from app.services.solar_geometry import _project_tz, _sample_dates, _solar_position


class ShadePreviewError(Exception):
    pass


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _issue(code: str, severity: Severity, message: str, path: str, fix: str, blocks: bool = False) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        area=ValidationArea.calculation,
        message=message,
        path=path,
        suggested_fix=fix,
        blocks_status=blocks,
    )


def _poly(points: list[list[float]] | None) -> Polygon | None:
    if not points or len(points) < 3:
        return None
    try:
        p = Polygon([(float(x), float(y)) for x, y in points])
        return p if p.is_valid and not p.is_empty and p.area > 0 else None
    except Exception:
        return None


def _shadow_polygon(obstruction_poly: Polygon, height_m: float, solar_azimuth_deg: float, solar_elevation_deg: float) -> Polygon | None:
    if solar_elevation_deg <= 0.5 or height_m <= 0:
        return None
    # In local coordinates: x=east, y=north. Solar azimuth is degrees clockwise
    # from true north. Shadow goes away from the sun, i.e. azimuth + 180.
    shadow_len = min(float(height_m) / max(0.05, tan(radians(solar_elevation_deg))), 80.0)
    shadow_az = radians((float(solar_azimuth_deg) + 180.0) % 360.0)
    dx = sin(shadow_az) * shadow_len
    dy = cos(shadow_az) * shadow_len
    shifted = translate(obstruction_poly, xoff=dx, yoff=dy)
    pts = list(obstruction_poly.exterior.coords) + list(shifted.exterior.coords)
    hull = MultiPoint(pts).convex_hull
    return hull if isinstance(hull, Polygon) and hull.is_valid and hull.area > 0 else None


def _panel_sample_points(panel_poly: Polygon, nx: int, ny: int) -> list[Point]:
    minx, miny, maxx, maxy = panel_poly.bounds
    samples: list[Point] = []
    for ix in range(nx):
        x = minx + (ix + 0.5) * (maxx - minx) / nx
        for iy in range(ny):
            y = miny + (iy + 0.5) * (maxy - miny) / ny
            pt = Point(x, y)
            if panel_poly.contains(pt) or panel_poly.touches(pt):
                samples.append(pt)
    if not samples:
        samples.append(panel_poly.representative_point())
    return samples


def _valid_hours(hours: list[int]) -> list[int]:
    out = sorted({int(h) for h in hours if 0 <= int(h) <= 23})
    return out or [12]


def run_shade_preview(db: Session, project_id: str, request: ShadePreviewRequest) -> ShadePreviewResultRead:
    geometry = get_project_geometry(db, project_id)
    issues: list[ValidationIssue] = []
    if geometry.site is None or geometry.site.lat is None or geometry.site.lon is None:
        issues.append(_issue(
            "SHADE_PREVIEW_REQUIRES_SITE_COORDINATES",
            Severity.blocker,
            "Shade preview requires site latitude and longitude for solar direction samples.",
            "site.lat/site.lon",
            "Add site coordinates before running shade preview.",
            blocks=True,
        ))
    try:
        selected_layout = build_selected_layout_export_payload(db, project_id, request.selected_layout_calculation_run_id)
    except Exception as exc:
        raise ShadePreviewError(f"Selected panel layout export is required before shade preview: {exc}") from exc
    if not selected_layout.placements:
        issues.append(_issue(
            "SHADE_PREVIEW_REQUIRES_PANEL_PLACEMENTS",
            Severity.blocker,
            "Shade preview needs a selected panel layout with panel placements.",
            "selected_layout.placements",
            "Run panel packing and export selected layout before shade preview.",
            blocks=True,
        ))
    obstruction_polys: list[tuple[object, Polygon, float]] = []
    for obs in geometry.obstructions:
        poly = _poly(obs.polygon_local_m)
        if poly is None:
            continue
        if obs.height_m is None:
            issues.append(_issue(
                "OBSTRUCTION_HEIGHT_MISSING_BLOCKS_SHADE",
                Severity.blocker,
                f"Obstruction {obs.obstruction_id} has polygon geometry but no height.",
                f"obstructions.{obs.obstruction_id}.height_m",
                "Enter surveyed/estimated obstruction height or remove it from shade preview.",
                blocks=True,
            ))
            continue
        obstruction_polys.append((obs, poly, float(obs.height_m)))
    if not obstruction_polys:
        issues.append(_issue(
            "NO_OBSTRUCTIONS_FOR_SHADE_PREVIEW",
            Severity.info,
            "No obstruction polygons with heights were found; shade preview will return zero obstruction shade.",
            "obstructions",
            "Add chimneys, trees, dormers, neighbouring buildings, or other height blocks to test shade.",
        ))

    valid_hours = _valid_hours(request.sample_hours_local)
    input_payload = {
        "project_id": project_id,
        "site": geometry.site.model_dump(mode="json") if geometry.site else None,
        "roof_planes": [r.model_dump(mode="json") for r in geometry.roof_planes],
        "obstructions": [o.model_dump(mode="json") for o in geometry.obstructions],
        "selected_layout_hash": selected_layout.selected_layout_export_hash_sha256,
        "request": request.model_dump(mode="json"),
        "engine": "arraylab_2d_obstruction_shadow_v0_1",
    }
    input_hash = stable_json_hash(input_payload)

    if any(i.blocks_status for i in issues):
        validation = ValidationReport(status="blocked", issues=issues, summary={"project_id": project_id})
        output_payload = {"status": "blocked", "issues": [i.model_dump(mode="json") for i in issues]}
        output_hash = stable_json_hash(output_payload)
        return ShadePreviewResultRead(
            project_id=project_id,
            status="blocked",
            selected_layout_calculation_run_id=request.selected_layout_calculation_run_id,
            selected_layout_export_hash_sha256=selected_layout.selected_layout_export_hash_sha256,
            input_hash_sha256=input_hash,
            output_hash_sha256=output_hash,
            validation_report=validation,
            sample_count_total=0,
            shaded_sample_count_total=0,
            shaded_fraction_preview=0.0,
            worst_panels=[],
            sample_debug=[],
            obstruction_shadow_debug=[],
            shade_result_hash_sha256=output_hash,
        )

    tz = _project_tz(geometry.site.timezone if geometry.site else "Europe/London")
    lat = float(geometry.site.lat)
    lon = float(geometry.site.lon)
    sample_days = _sample_dates(2026, request.sample_day_mode)

    panel_stats: dict[str, dict] = {}
    sample_debug: list[dict] = []
    shadow_debug: list[dict] = []
    total_samples = 0
    shaded_samples = 0
    shadow_polys_by_time: list[tuple[str, str, Polygon, object]] = []

    for month, day in sample_days:
        for hour in valid_hours:
            when_local = datetime(2026, month, day, hour, 0, 0, tzinfo=tz)
            pos = _solar_position(lat, lon, when_local)
            if pos.elevation_deg <= 0:
                continue
            for obs, obs_poly, height_m in obstruction_polys:
                sp = _shadow_polygon(obs_poly, height_m, pos.azimuth_deg, pos.elevation_deg)
                if sp is None:
                    continue
                time_key = when_local.isoformat()
                shadow_polys_by_time.append((time_key, getattr(obs, "obstruction_id"), sp, obs))
                shadow_debug.append({
                    "timestamp_local": time_key,
                    "obstruction_id": getattr(obs, "obstruction_id"),
                    "height_m": round(height_m, 3),
                    "solar_azimuth_deg": pos.azimuth_deg,
                    "solar_elevation_deg": pos.elevation_deg,
                    "shadow_area_m2": round(sp.area, 4),
                    "shadow_length_basis_m": round(height_m / max(0.05, tan(radians(pos.elevation_deg))), 4),
                })

    for placement in selected_layout.placements:
        panel_poly = _poly(placement.get("polygon_local_m"))
        if panel_poly is None:
            continue
        placement_id = str(placement.get("placement_id"))
        stats = panel_stats.setdefault(placement_id, {
            "placement": placement,
            "sample_count": 0,
            "shaded_sample_count": 0,
            "blockers": {},
        })
        panel_points = _panel_sample_points(panel_poly, request.sample_grid_x, request.sample_grid_y)
        for time_key, blocker_id, shadow_poly, obs in shadow_polys_by_time:
            for sample_idx, pt in enumerate(panel_points):
                total_samples += 1
                stats["sample_count"] += 1
                hit = shadow_poly.contains(pt) or shadow_poly.touches(pt)
                if hit:
                    shaded_samples += 1
                    stats["shaded_sample_count"] += 1
                    stats["blockers"][blocker_id] = stats["blockers"].get(blocker_id, 0) + 1
                if hit or request.include_unshaded_samples:
                    sample_debug.append({
                        "timestamp_local": time_key,
                        "placement_id": placement_id,
                        "sample_index": sample_idx,
                        "sample_point_local_m": [round(pt.x, 4), round(pt.y, 4)],
                        "ray_blocked": bool(hit),
                        "blocker_id": blocker_id if hit else None,
                    })

    worst: list[PanelShadeSummaryRead] = []
    for stats in panel_stats.values():
        placement = stats["placement"]
        count = int(stats["sample_count"])
        shaded = int(stats["shaded_sample_count"])
        blockers = sorted(stats["blockers"].items(), key=lambda item: item[1], reverse=True)
        worst.append(PanelShadeSummaryRead(
            placement_id=str(placement.get("placement_id")),
            roof_plane_id=str(placement.get("roof_plane_id")),
            panel_model_id=str(placement.get("panel_model_id")),
            sample_count=count,
            shaded_sample_count=shaded,
            shaded_fraction=round(shaded / count, 6) if count else 0.0,
            worst_blocker_ids=[bid for bid, _ in blockers[:3]],
        ))
    worst.sort(key=lambda row: (row.shaded_fraction, row.shaded_sample_count), reverse=True)

    shade_fraction = round(shaded_samples / total_samples, 6) if total_samples else 0.0
    status = "warnings" if any(i.severity in {Severity.warning, Severity.error} for i in issues) else "ok"
    validation = ValidationReport(status=status, issues=issues, summary={
        "project_id": project_id,
        "sample_count_total": total_samples,
        "shaded_sample_count_total": shaded_samples,
        "obstruction_count_with_height": len(obstruction_polys),
        "shadow_time_polygons": len(shadow_polys_by_time),
    })
    output_payload = {
        "status": status,
        "validation": validation.model_dump(mode="json"),
        "sample_count_total": total_samples,
        "shaded_sample_count_total": shaded_samples,
        "shaded_fraction_preview": shade_fraction,
        "worst_panels": [w.model_dump(mode="json") for w in worst[:10]],
        "sample_debug": sample_debug[:500],
        "obstruction_shadow_debug": shadow_debug[:250],
        "truth_boundary": "preview obstruction-shadow debug only",
    }
    output_hash = stable_json_hash(output_payload)
    built = build_calculation_run(CalculationRunCreate(
        project_id=project_id,
        run_type=CalculationRunType.shade,
        engine_version="0.5.3-shade-preview-contract",
        input_snapshot=input_payload,
        assumption_set_id="SHADE_PREVIEW_2D_CONTRACT_V0_1",
        warnings=[f"{i.code}: {i.message}" for i in issues],
    ))
    row = CalculationRun(
        run_id=built.run_id,
        project_id=project_id,
        run_type=built.run_type.value,
        status=status,
        software_version=built.software_version,
        engine_version=built.engine_version,
        input_snapshot_hash_sha256=input_hash,
        output_hash_sha256=output_hash,
        product_data_snapshot_id=None,
        assumption_set_id="SHADE_PREVIEW_2D_CONTRACT_V0_1",
        warnings=built.warnings,
        input_snapshot=input_payload,
        output_snapshot=output_payload,
    )
    db.add(row)
    db.commit()
    return ShadePreviewResultRead(
        project_id=project_id,
        status=status,
        calculation_run_id=row.run_id,
        selected_layout_calculation_run_id=request.selected_layout_calculation_run_id,
        selected_layout_export_hash_sha256=selected_layout.selected_layout_export_hash_sha256,
        input_hash_sha256=input_hash,
        output_hash_sha256=output_hash,
        validation_report=validation,
        sample_count_total=total_samples,
        shaded_sample_count_total=shaded_samples,
        shaded_fraction_preview=shade_fraction,
        worst_panels=worst[:10],
        sample_debug=sample_debug[:500],
        obstruction_shadow_debug=shadow_debug[:250],
        shade_result_hash_sha256=output_hash,
    )


def _make_shade_project(db: Session, project_id: str, obstruction_y: float, height_m: float | None = 8.0):
    project = create_project(db, ProjectCreate(project_id=project_id, title=f"Shade preview check {project_id}"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, timezone="Europe/London", source_type="postcode_lookup"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35.0,
        azimuth_deg=180.0,
        height_m=6.0,
        polygon_local_m=[[0, 0], [12, 0], [12, 8], [0, 8]],
        source_confidence=0.75,
    ))
    add_obstruction(db, project.project_id, ObstructionCreate(
        obstruction_type="chimney",
        label="test obstruction",
        height_m=height_m,
        polygon_local_m=[[5, obstruction_y], [7, obstruction_y], [7, obstruction_y + 0.5], [5, obstruction_y + 0.5]],
        source_confidence=0.65,
    ))
    layout = run_panel_packing(db, project.project_id, PanelPackingRequest(allow_dev_fallback_panels=True, max_panels_per_roof=40))
    return project, layout


def shade_preview_self_check(db: Session) -> ShadePreviewSelfCheckRead:
    # Use unique project IDs so repeated debug calls in the same local database do not collide.
    # Debug endpoints must be re-runnable, because humans will click buttons twice and then blame physics.
    suffix = uuid4().hex[:8]
    near, near_layout = _make_shade_project(db, f"prj_shade_near_{suffix}", obstruction_y=-1.0, height_m=8.0)
    low, low_layout = _make_shade_project(db, f"prj_shade_low_{suffix}", obstruction_y=-1.0, height_m=0.5)
    far, far_layout = _make_shade_project(db, f"prj_shade_far_{suffix}", obstruction_y=-20.0, height_m=8.0)
    missing, missing_layout = _make_shade_project(db, f"prj_shade_missing_height_{suffix}", obstruction_y=-1.0, height_m=None)

    req_near = ShadePreviewRequest(selected_layout_calculation_run_id=near_layout.calculation_run_id, sample_day_mode="seasonal_key_days", sample_hours_local=[12], sample_grid_x=2, sample_grid_y=2)
    req_low = ShadePreviewRequest(selected_layout_calculation_run_id=low_layout.calculation_run_id, sample_day_mode="seasonal_key_days", sample_hours_local=[12], sample_grid_x=2, sample_grid_y=2)
    req_far = ShadePreviewRequest(selected_layout_calculation_run_id=far_layout.calculation_run_id, sample_day_mode="seasonal_key_days", sample_hours_local=[12], sample_grid_x=2, sample_grid_y=2)
    req_missing = ShadePreviewRequest(selected_layout_calculation_run_id=missing_layout.calculation_run_id, sample_day_mode="seasonal_key_days", sample_hours_local=[12], sample_grid_x=2, sample_grid_y=2)

    near_result = run_shade_preview(db, near.project_id, req_near)
    low_result = run_shade_preview(db, low.project_id, req_low)
    far_result = run_shade_preview(db, far.project_id, req_far)
    missing_result = run_shade_preview(db, missing.project_id, req_missing)

    height_changes = near_result.shaded_fraction_preview > low_result.shaded_fraction_preview
    position_changes = near_result.shaded_fraction_preview > far_result.shaded_fraction_preview
    missing_blocks = missing_result.status == "blocked" and any(i.code == "OBSTRUCTION_HEIGHT_MISSING_BLOCKS_SHADE" for i in missing_result.validation_report.issues)
    worst_present = bool(near_result.worst_panels) and near_result.worst_panels[0].sample_count > 0
    hash_changes = near_result.output_hash_sha256 != far_result.output_hash_sha256
    bounds_ok = 0.0 <= near_result.shaded_fraction_preview <= 1.0 and near_result.sample_count_total >= near_result.shaded_sample_count_total >= 0
    ok = height_changes and position_changes and missing_blocks and worst_present and hash_changes and bounds_ok
    return ShadePreviewSelfCheckRead(
        status="ok" if ok else "failed",
        project_id=near.project_id,
        shade_changes_with_obstruction_height=height_changes,
        shade_changes_with_obstruction_position=position_changes,
        missing_obstruction_height_blocks=missing_blocks,
        worst_panel_list_present=worst_present,
        shade_hash_changes_with_geometry=hash_changes,
        sample_bounds_ok=bounds_ok,
        calculation_run_id=near_result.calculation_run_id,
        output_hash_sha256=near_result.output_hash_sha256,
    )

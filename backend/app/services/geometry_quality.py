from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Iterable

from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity
from sqlalchemy.orm import Session

from app.models.db_models import ObstructionRecord, ProjectVersionSnapshot, RoofPlaneRecord
from app.schemas.geometry import (
    GeometryQualityReportRead,
    GeometryQualitySelfCheckRead,
    GeometryQualitySnapshotRead,
    PackerAllowedAreaExportRead,
    ProjectCreate,
    RoofPlaneCreate,
    RoofPlaneQualityRead,
    SetbackRuleRead,
)
from app.schemas.project import RoofType
from app.schemas.validation import Severity, ValidationArea, ValidationIssue, ValidationReport
from app.services.hash_utils import stable_json_hash
from app.services.project_geometry import (
    ProjectGeometryError,
    add_obstruction,
    add_roof_plane,
    create_project,
    create_project_snapshot,
    project_geometry_payload,
    upsert_site,
)
from app.schemas.geometry import ObstructionCreate, ObstructionType, SiteCreate


@dataclass(frozen=True)
class SetbackRule:
    rule_id: str
    roof_type: str
    edge_margin_m: float
    obstruction_clearance_m: float
    access_margin_m: float
    rule_source: str
    confidence: float


# These are deliberately conservative design-assist defaults, not MCS or manufacturer approval.
# The point is to keep panel packing away from edges/obstructions until NuVision loads formal rules.
DEFAULT_SETBACK_RULES: dict[str, SetbackRule] = {
    RoofType.tiled_pitched.value: SetbackRule("SETBACK_TILED_PITCHED_V0", RoofType.tiled_pitched.value, 0.45, 0.30, 0.45, "ArrayLab pre-design default; replace with NuVision/manufacturer rule", 0.35),
    RoofType.slate_pitched.value: SetbackRule("SETBACK_SLATE_PITCHED_V0", RoofType.slate_pitched.value, 0.55, 0.35, 0.55, "ArrayLab pre-design default; slate survey required", 0.30),
    RoofType.trapezoidal_sheet.value: SetbackRule("SETBACK_TRAPEZOIDAL_V0", RoofType.trapezoidal_sheet.value, 0.40, 0.30, 0.40, "ArrayLab pre-design default; manufacturer fixing rules required", 0.35),
    RoofType.corrugated_sheet.value: SetbackRule("SETBACK_CORRUGATED_V0", RoofType.corrugated_sheet.value, 0.45, 0.35, 0.45, "ArrayLab pre-design default; manufacturer fixing rules required", 0.30),
    RoofType.standing_seam.value: SetbackRule("SETBACK_STANDING_SEAM_V0", RoofType.standing_seam.value, 0.40, 0.30, 0.40, "ArrayLab pre-design default; seam clamp approval required", 0.35),
    RoofType.flat_roof.value: SetbackRule("SETBACK_FLAT_ROOF_V0", RoofType.flat_roof.value, 1.00, 0.50, 0.60, "ArrayLab pre-design default; ballast/manufacturer calc required", 0.30),
    RoofType.ground_mount.value: SetbackRule("SETBACK_GROUND_MOUNT_V0", RoofType.ground_mount.value, 0.60, 0.40, 0.60, "ArrayLab pre-design default; planning/access check required", 0.35),
    RoofType.unknown.value: SetbackRule("SETBACK_UNKNOWN_V0", RoofType.unknown.value, 0.75, 0.50, 0.75, "ArrayLab safety fallback; select roof type", 0.15),
}


def _issue(code: str, severity: Severity, message: str, path: str, fix: str, blocks: bool = False) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        area=ValidationArea.roof_geometry,
        message=message,
        path=path,
        suggested_fix=fix,
        blocks_status=blocks,
    )


def _rule_for(roof_type: str | None) -> SetbackRule:
    return DEFAULT_SETBACK_RULES.get(str(roof_type or RoofType.unknown.value), DEFAULT_SETBACK_RULES[RoofType.unknown.value])


def _rule_read(rule: SetbackRule) -> SetbackRuleRead:
    return SetbackRuleRead(
        rule_id=rule.rule_id,
        roof_type=RoofType(rule.roof_type) if rule.roof_type in RoofType._value2member_map_ else RoofType.unknown,
        edge_margin_m=rule.edge_margin_m,
        obstruction_clearance_m=rule.obstruction_clearance_m,
        access_margin_m=rule.access_margin_m,
        rule_source=rule.rule_source,
        confidence=rule.confidence,
    )


def _polygon_from_points(points: list[list[float]] | None) -> Polygon | None:
    if not points or len(points) < 3:
        return None
    return Polygon([(float(x), float(y)) for x, y in points])


def _polygon_to_points(geom: BaseGeometry) -> list[list[float]] | None:
    poly: Polygon | None = None
    if geom.is_empty:
        return None
    if isinstance(geom, Polygon):
        poly = geom
    elif isinstance(geom, MultiPolygon):
        poly = max(list(geom.geoms), key=lambda item: item.area, default=None)
    elif isinstance(geom, GeometryCollection):
        polys = [g for g in geom.geoms if isinstance(g, Polygon)]
        poly = max(polys, key=lambda item: item.area, default=None) if polys else None
    if poly is None or poly.is_empty:
        return None
    coords = list(poly.exterior.coords)
    # remove closing coordinate for internal ArrayLab local polygon format
    return [[round(float(x), 4), round(float(y), 4)] for x, y in coords[:-1]]


def _iter_obstruction_polygons(obstructions: Iterable[ObstructionRecord]) -> Iterable[tuple[ObstructionRecord, Polygon]]:
    for row in obstructions:
        poly = _polygon_from_points(row.polygon_local_m)
        if poly is not None and not poly.is_empty:
            yield row, poly


def _edge_lengths(points: list[list[float]]) -> list[float]:
    if len(points) < 2:
        return []
    lengths = []
    for idx, (x1, y1) in enumerate(points):
        x2, y2 = points[(idx + 1) % len(points)]
        lengths.append(hypot(x2 - x1, y2 - y1))
    return lengths


def _aspect_ratio(poly: Polygon) -> float | None:
    try:
        rect = poly.minimum_rotated_rectangle
        pts = list(rect.exterior.coords)[:-1]
        if len(pts) != 4:
            return None
        edges = sorted([hypot(pts[(i + 1) % 4][0] - pts[i][0], pts[(i + 1) % 4][1] - pts[i][1]) for i in range(4)])
        short = max(edges[0], 1e-9)
        long = max(edges[-1], short)
        return long / short
    except Exception:
        return None


def _score_from_issues(issues: list[ValidationIssue]) -> float:
    score = 100.0
    for item in issues:
        if item.severity == Severity.blocker:
            score -= 45
        elif item.severity == Severity.error:
            score -= 25
        elif item.severity == Severity.warning:
            score -= 10
        else:
            score -= 2
    return max(0.0, min(100.0, round(score, 1)))


def _quality_for_roof(roof: RoofPlaneRecord, obstructions: list[ObstructionRecord]) -> tuple[RoofPlaneQualityRead, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    rule = _rule_for(roof.roof_type)
    poly = _polygon_from_points(roof.polygon_local_m)

    if poly is None:
        issues.append(_issue("ROOF_POLYGON_MISSING", Severity.blocker, "Roof plane has no polygon.", "roof_plane.polygon_local_m", "Draw or import a roof polygon before panel packing.", True))
        return RoofPlaneQualityRead(
            roof_plane_id=roof.roof_plane_id,
            label=roof.label,
            roof_type=RoofType(roof.roof_type) if roof.roof_type in RoofType._value2member_map_ else RoofType.unknown,
            geometry_quality_score=_score_from_issues(issues),
            original_area_m2=roof.area_m2,
            usable_area_m2=None,
            blocked_area_m2=None,
            edge_margin_m=rule.edge_margin_m,
            obstruction_clearance_m=rule.obstruction_clearance_m,
            edge_zone_depth_m=roof.edge_zone_depth_m,
            polygon_valid=False,
            usable_polygon_local_m=None,
            packer_allowed_area=None,
            issue_codes=[i.code for i in issues],
        ), issues

    if not poly.is_valid:
        issues.append(_issue("ROOF_POLYGON_INVALID", Severity.blocker, f"Roof polygon is invalid: {explain_validity(poly)}", "roof_plane.polygon_local_m", "Redraw the polygon without crossing edges or duplicate loops.", True))
    if poly.area < 1.0:
        issues.append(_issue("ROOF_AREA_TOO_SMALL", Severity.blocker, "Roof area is below 1 m², too small for any useful PV layout.", "roof_plane.area_m2", "Redraw using real roof geometry.", True))
    elif poly.area < 5.0:
        issues.append(_issue("ROOF_AREA_LOW", Severity.warning, "Roof area is very small, panel packing may not be useful.", "roof_plane.area_m2", "Check scale and map units before trusting this roof plane."))

    lengths = _edge_lengths(roof.polygon_local_m or [])
    if lengths and min(lengths) < 0.25:
        issues.append(_issue("ROOF_EDGE_TOO_SHORT", Severity.warning, "Roof polygon contains an edge under 0.25 m, probably a click/drawing artefact.", "roof_plane.polygon_local_m", "Clean up or redraw tiny edges before panel packing."))

    aspect = _aspect_ratio(poly) if poly.is_valid and poly.area > 0 else None
    if aspect and aspect > 12:
        issues.append(_issue("ROOF_SHAPE_EXTREME_ASPECT_RATIO", Severity.warning, f"Roof plane has extreme aspect ratio {aspect:.1f}:1.", "roof_plane.polygon_local_m", "Check this is a real long roof/ground array, not a bad polygon."))

    if roof.roof_type == RoofType.unknown.value:
        issues.append(_issue("ROOF_TYPE_UNKNOWN_SETBACK_FALLBACK", Severity.error, "Roof type is unknown, so conservative setback fallback is being used.", "roof_plane.roof_type", "Select tile/slate/flat/ground/trapezoidal/etc before panel packing.", True))

    usable: BaseGeometry = poly
    if poly.is_valid and poly.area > 0:
        try:
            usable = poly.buffer(-rule.edge_margin_m)
        except Exception as exc:
            usable = GeometryCollection()
            issues.append(_issue("SETBACK_BUFFER_FAILED", Severity.blocker, f"Setback buffer failed: {exc}", "roof_plane.polygon_local_m", "Redraw polygon or reduce invalid geometry before panel packing.", True))

    if usable.is_empty or usable.area <= 0:
        issues.append(_issue("SETBACK_REMOVES_USABLE_AREA", Severity.blocker, "Required edge setback removes all usable roof area.", "roof_plane.setback", "Use a larger/cleaner roof plane, reduce only after formal rule review, or mark as no-pack area.", True))

    obstruction_hits = 0
    for obs, obs_poly in _iter_obstruction_polygons(obstructions):
        if not obs_poly.is_valid:
            issues.append(_issue("OBSTRUCTION_POLYGON_INVALID", Severity.warning, f"Obstruction {obs.obstruction_id} polygon is invalid.", f"obstructions.{obs.obstruction_id}", "Redraw obstruction polygon."))
            continue
        if poly.intersects(obs_poly):
            obstruction_hits += 1
            try:
                usable = usable.difference(obs_poly.buffer(rule.obstruction_clearance_m))
            except Exception as exc:
                issues.append(_issue("OBSTRUCTION_CUT_FAILED", Severity.warning, f"Could not subtract obstruction {obs.obstruction_id}: {exc}", f"obstructions.{obs.obstruction_id}", "Redraw obstruction or review manually."))
    if obstruction_hits:
        issues.append(_issue("OBSTRUCTION_OVERLAP_CUTOUT", Severity.warning, f"{obstruction_hits} obstruction polygon(s) overlap this roof and reduce allowed panel area.", "obstructions", "Review obstruction positions and clearances before panel packing."))

    usable_area = max(0.0, float(usable.area)) if usable and not usable.is_empty else 0.0
    blocked_area = max(0.0, float(poly.area) - usable_area)
    usable_points = _polygon_to_points(usable)
    if usable_area < 1.0 and poly.area >= 1.0:
        issues.append(_issue("USABLE_AREA_TOO_SMALL", Severity.blocker, "Usable area after setbacks/obstructions is below 1 m².", "roof_plane.usable_area", "Review setbacks, obstructions, or roof polygon scale.", True))

    packer_payload = {
        "roof_plane_id": roof.roof_plane_id,
        "allowed_polygon_local_m": usable_points,
        "allowed_area_m2": round(usable_area, 3),
        "setback_rule_id": rule.rule_id,
        "pre_design_only": True,
        "not_structural_approval": True,
    }

    return RoofPlaneQualityRead(
        roof_plane_id=roof.roof_plane_id,
        label=roof.label,
        roof_type=RoofType(roof.roof_type) if roof.roof_type in RoofType._value2member_map_ else RoofType.unknown,
        geometry_quality_score=_score_from_issues(issues),
        original_area_m2=round(float(poly.area), 3),
        usable_area_m2=round(usable_area, 3),
        blocked_area_m2=round(blocked_area, 3),
        edge_margin_m=rule.edge_margin_m,
        obstruction_clearance_m=rule.obstruction_clearance_m,
        edge_zone_depth_m=roof.edge_zone_depth_m,
        polygon_valid=poly.is_valid,
        usable_polygon_local_m=usable_points,
        packer_allowed_area=packer_payload,
        issue_codes=[i.code for i in issues],
    ), issues


def build_geometry_quality_report(db: Session, project_id: str) -> GeometryQualityReportRead:
    try:
        payload = project_geometry_payload(db, project_id)
    except Exception as exc:
        raise ProjectGeometryError(str(exc)) from exc
    roof_rows = db.query(RoofPlaneRecord).filter(RoofPlaneRecord.project_id == project_id).order_by(RoofPlaneRecord.created_at).all()
    obs_rows = db.query(ObstructionRecord).filter(ObstructionRecord.project_id == project_id).order_by(ObstructionRecord.created_at).all()
    all_issues: list[ValidationIssue] = []
    quality_rows: list[RoofPlaneQualityRead] = []
    rules_seen: dict[str, SetbackRule] = {}

    if not roof_rows:
        all_issues.append(_issue("NO_ROOF_PLANES", Severity.blocker, "Project has no roof planes.", "roof_planes", "Draw or import at least one roof plane.", True))

    for roof in roof_rows:
        rule = _rule_for(roof.roof_type)
        rules_seen[rule.rule_id] = rule
        row, issues = _quality_for_roof(roof, obs_rows)
        quality_rows.append(row)
        all_issues.extend([issue.model_copy(update={"path": f"roof_planes.{roof.roof_plane_id}.{issue.path}" if issue.path else f"roof_planes.{roof.roof_plane_id}"}) for issue in issues])

    status = "blocked" if any(i.blocks_status or i.severity == Severity.blocker for i in all_issues) else ("warnings" if all_issues else "ok")
    summary = {
        "project_id": project_id,
        "roof_plane_count": len(roof_rows),
        "obstruction_count": len(obs_rows),
        "total_original_area_m2": round(sum((r.original_area_m2 or 0) for r in quality_rows), 3),
        "total_usable_area_m2": round(sum((r.usable_area_m2 or 0) for r in quality_rows), 3),
        "pre_design_only": True,
        "not_structural_approval": True,
        "source_payload_hash_sha256": stable_json_hash(payload),
    }
    validation = ValidationReport(status=status, issues=all_issues, summary=summary)
    report_body = {
        "project_id": project_id,
        "status": status,
        "validation_report": validation.model_dump(mode="json"),
        "setback_rules": [_rule_read(rule).model_dump(mode="json") for rule in sorted(rules_seen.values(), key=lambda item: item.rule_id)],
        "roof_planes": [row.model_dump(mode="json") for row in quality_rows],
        "summary": summary,
    }
    report_hash = stable_json_hash(report_body)
    return GeometryQualityReportRead(
        project_id=project_id,
        status=status,
        report_hash_sha256=report_hash,
        validation_report=validation,
        setback_rules=[_rule_read(rule) for rule in sorted(rules_seen.values(), key=lambda item: item.rule_id)],
        roof_planes=quality_rows,
        summary=summary,
    )


def create_geometry_quality_snapshot(db: Session, project_id: str, created_by: str | None = None) -> GeometryQualitySnapshotRead:
    report = build_geometry_quality_report(db, project_id)
    snapshot = create_project_snapshot(db, project_id, snapshot_kind="geometry_quality_input", created_by=created_by)
    # Store the report as a second immutable evidence packet so UI/export can prove exactly what was judged.
    row = ProjectVersionSnapshot(
        project_snapshot_id=f"gq_{snapshot.project_snapshot_id}",
        project_id=project_id,
        snapshot_kind="geometry_quality_report",
        snapshot_hash_sha256=report.report_hash_sha256,
        snapshot_payload=report.model_dump(mode="json"),
        created_by=created_by,
    )
    db.add(row)
    db.commit()
    return GeometryQualitySnapshotRead(
        project_id=project_id,
        project_snapshot_id=row.project_snapshot_id,
        snapshot_hash_sha256=row.snapshot_hash_sha256,
        quality_report_hash_sha256=report.report_hash_sha256,
        report=report,
    )


def export_packer_allowed_area(db: Session, project_id: str) -> PackerAllowedAreaExportRead:
    report = build_geometry_quality_report(db, project_id)
    payload = {
        "project_id": project_id,
        "pre_design_only": True,
        "not_structural_approval": True,
        "report_hash_sha256": report.report_hash_sha256,
        "roof_planes": [row.packer_allowed_area for row in report.roof_planes if row.packer_allowed_area],
        "validation_status": report.status,
        "issue_codes": [issue.code for issue in report.validation_report.issues],
    }
    return PackerAllowedAreaExportRead(
        project_id=project_id,
        report_hash_sha256=report.report_hash_sha256,
        payload=payload,
    )


def geometry_quality_self_check(db: Session) -> GeometryQualitySelfCheckRead:
    bad = create_project(db, ProjectCreate(title="Geometry quality self-check bad polygon", created_by="geometry_quality"))
    upsert_site(db, bad.project_id, SiteCreate(postcode="EX14 3JF", source_type="postcode_lookup", source_confidence=0.7))
    add_roof_plane(db, bad.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35,
        azimuth_deg=180,
        height_m=6,
        polygon_local_m=[[0, 0], [6, 0], [0, 6], [6, 6]],
        source_type="manual",
        source_confidence=0.5,
    ))
    bad_report = build_geometry_quality_report(db, bad.project_id)

    good = create_project(db, ProjectCreate(title="Geometry quality self-check obstruction", created_by="geometry_quality"))
    upsert_site(db, good.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, source_type="postcode_lookup", source_confidence=0.7))
    roof = add_roof_plane(db, good.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35,
        azimuth_deg=180,
        height_m=6,
        polygon_local_m=[[0, 0], [12, 0], [12, 6], [0, 6]],
        source_type="manual",
        source_confidence=0.65,
    ))
    add_obstruction(db, good.project_id, ObstructionCreate(
        roof_plane_id=roof.roof_plane_id,
        obstruction_type=ObstructionType.chimney,
        label="test chimney",
        height_m=1.2,
        polygon_local_m=[[4, 2], [5, 2], [5, 3], [4, 3]],
        source_type="manual",
        source_confidence=0.5,
    ))
    report1 = build_geometry_quality_report(db, good.project_id)
    report2 = build_geometry_quality_report(db, good.project_id)
    first = report1.roof_planes[0]
    original = first.original_area_m2 or 0
    usable = first.usable_area_m2 or 0
    return GeometryQualitySelfCheckRead(
        status="ok" if bad_report.status == "blocked" and usable > 0 and usable < original and report1.report_hash_sha256 == report2.report_hash_sha256 else "failed",
        bad_polygon_blocked=bad_report.status == "blocked",
        obstruction_reduces_usable_area=usable < original,
        usable_area_positive=usable > 0,
        quality_hash_stable=report1.report_hash_sha256 == report2.report_hash_sha256,
        report_hash_sha256=report1.report_hash_sha256,
    )

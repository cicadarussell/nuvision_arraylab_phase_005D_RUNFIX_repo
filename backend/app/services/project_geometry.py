from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.db_models import (
    CalculationRun,
    ObstructionRecord,
    ProjectRecord,
    ProjectVersionSnapshot,
    RoofPlaneRecord,
    SiteRecord,
)
from app.schemas.calculation import CalculationRunCreate, CalculationRunType
from app.schemas.geometry import (
    MountingPrecheckRead,
    ObstructionCreate,
    ObstructionRead,
    ProjectCreate,
    ProjectGeometryRead,
    ProjectRead,
    ProjectSnapshotRead,
    RoofPlaneCreate,
    RoofPlaneRead,
    SiteCreate,
    SiteRead,
)
from app.schemas.project import ProjectSnapshot, RoofPlane as ProjectRoofPlane, Site as ProjectSite, StructuralTruthState
from app.services.calculation_run import build_calculation_run
from app.services.hash_utils import stable_json_hash
from app.services.validation_engine import issue, validate_project_for_mounting_precheck
from app.schemas.validation import Severity, ValidationArea, ValidationReport


class ProjectGeometryError(Exception):
    pass


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def polygon_area_m2(points: list[list[float]] | None) -> float | None:
    if not points or len(points) < 3:
        return None
    area = 0.0
    for idx, (x1, y1) in enumerate(points):
        x2, y2 = points[(idx + 1) % len(points)]
        area += (x1 * y2) - (x2 * y1)
    return abs(area) / 2.0


def edge_zone_depth_from_height(height_m: float | None) -> float | None:
    if height_m is None:
        return None
    # Van der Valk guidance in the project plan uses 1/5 building height as edge-zone basis.
    # This is a precheck value only, not a structural approval.
    return round(height_m / 5.0, 3)


def _project_read(row: ProjectRecord) -> ProjectRead:
    return ProjectRead(
        project_id=row.project_id,
        title=row.title,
        customer_ref=row.customer_ref,
        status=row.status,
        created_by=row.created_by,
    )


def _site_read(row: SiteRecord) -> SiteRead:
    return SiteRead(
        site_id=row.site_id,
        project_id=row.project_id,
        postcode=row.postcode,
        lat=row.lat,
        lon=row.lon,
        timezone=row.timezone,
        source_type=row.source_type,
        source_confidence=row.source_confidence,
        notes=row.notes,
    )


def _roof_read(row: RoofPlaneRecord) -> RoofPlaneRead:
    return RoofPlaneRead(
        roof_plane_id=row.roof_plane_id,
        project_id=row.project_id,
        label=row.label,
        roof_type=row.roof_type,
        pitch_deg=row.pitch_deg,
        azimuth_deg=row.azimuth_deg,
        height_m=row.height_m,
        polygon_local_m=row.polygon_local_m,
        area_m2=row.area_m2,
        edge_zone_depth_m=row.edge_zone_depth_m,
        source_type=row.source_type,
        source_confidence=row.source_confidence,
    )


def _obstruction_read(row: ObstructionRecord) -> ObstructionRead:
    return ObstructionRead(
        obstruction_id=row.obstruction_id,
        project_id=row.project_id,
        roof_plane_id=row.roof_plane_id,
        obstruction_type=row.obstruction_type,
        label=row.label,
        height_m=row.height_m,
        polygon_local_m=row.polygon_local_m,
        centre_local_m=row.centre_local_m,
        source_type=row.source_type,
        source_confidence=row.source_confidence,
        notes=row.notes,
    )


def create_project(db: Session, payload: ProjectCreate) -> ProjectRecord:
    project_id = payload.project_id or _id("prj")
    if db.get(ProjectRecord, project_id):
        raise ProjectGeometryError("Project already exists")
    row = ProjectRecord(
        project_id=project_id,
        title=payload.title,
        customer_ref=payload.customer_ref,
        created_by=payload.created_by,
        status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_project(db: Session, project_id: str) -> ProjectRecord | None:
    return db.get(ProjectRecord, project_id)


def list_projects(db: Session) -> list[ProjectRecord]:
    return db.query(ProjectRecord).order_by(ProjectRecord.created_at.desc()).all()


def upsert_site(db: Session, project_id: str, payload: SiteCreate) -> SiteRecord:
    if not db.get(ProjectRecord, project_id):
        raise ProjectGeometryError("Project not found")
    existing = db.query(SiteRecord).filter(SiteRecord.project_id == project_id).one_or_none()
    if existing:
        row = existing
    else:
        row = SiteRecord(site_id=_id("site"), project_id=project_id)
        db.add(row)
    row.postcode = payload.postcode
    row.lat = payload.lat
    row.lon = payload.lon
    row.timezone = payload.timezone
    row.source_type = payload.source_type.value if hasattr(payload.source_type, "value") else str(payload.source_type)
    row.source_confidence = payload.source_confidence
    row.notes = payload.notes
    db.commit()
    db.refresh(row)
    return row


def add_roof_plane(db: Session, project_id: str, payload: RoofPlaneCreate) -> RoofPlaneRecord:
    if not db.get(ProjectRecord, project_id):
        raise ProjectGeometryError("Project not found")
    roof_plane_id = payload.roof_plane_id or _id("roof")
    if db.get(RoofPlaneRecord, roof_plane_id):
        raise ProjectGeometryError("Roof plane already exists")
    area = polygon_area_m2(payload.polygon_local_m)
    row = RoofPlaneRecord(
        roof_plane_id=roof_plane_id,
        project_id=project_id,
        label=payload.label,
        roof_type=payload.roof_type.value if hasattr(payload.roof_type, "value") else str(payload.roof_type),
        pitch_deg=payload.pitch_deg,
        azimuth_deg=payload.azimuth_deg,
        height_m=payload.height_m,
        polygon_local_m=payload.polygon_local_m,
        area_m2=area,
        edge_zone_depth_m=edge_zone_depth_from_height(payload.height_m),
        source_type=payload.source_type.value if hasattr(payload.source_type, "value") else str(payload.source_type),
        source_confidence=payload.source_confidence,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def add_obstruction(db: Session, project_id: str, payload: ObstructionCreate) -> ObstructionRecord:
    if not db.get(ProjectRecord, project_id):
        raise ProjectGeometryError("Project not found")
    if payload.roof_plane_id and not db.get(RoofPlaneRecord, payload.roof_plane_id):
        raise ProjectGeometryError("Roof plane not found")
    obstruction_id = payload.obstruction_id or _id("obs")
    if db.get(ObstructionRecord, obstruction_id):
        raise ProjectGeometryError("Obstruction already exists")
    row = ObstructionRecord(
        obstruction_id=obstruction_id,
        project_id=project_id,
        roof_plane_id=payload.roof_plane_id,
        obstruction_type=payload.obstruction_type.value if hasattr(payload.obstruction_type, "value") else str(payload.obstruction_type),
        label=payload.label,
        height_m=payload.height_m,
        polygon_local_m=payload.polygon_local_m,
        centre_local_m=payload.centre_local_m,
        source_type=payload.source_type.value if hasattr(payload.source_type, "value") else str(payload.source_type),
        source_confidence=payload.source_confidence,
        notes=payload.notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def project_geometry_payload(db: Session, project_id: str) -> dict:
    project = db.get(ProjectRecord, project_id)
    if project is None:
        raise ProjectGeometryError("Project not found")
    site = db.query(SiteRecord).filter(SiteRecord.project_id == project_id).one_or_none()
    roofs = db.query(RoofPlaneRecord).filter(RoofPlaneRecord.project_id == project_id).order_by(RoofPlaneRecord.created_at).all()
    obstructions = db.query(ObstructionRecord).filter(ObstructionRecord.project_id == project_id).order_by(ObstructionRecord.created_at).all()
    return {
        "project": _project_read(project).model_dump(mode="json"),
        "site": _site_read(site).model_dump(mode="json") if site else None,
        "roof_planes": [_roof_read(row).model_dump(mode="json") for row in roofs],
        "obstructions": [_obstruction_read(row).model_dump(mode="json") for row in obstructions],
    }


def get_project_geometry(db: Session, project_id: str) -> ProjectGeometryRead:
    payload = project_geometry_payload(db, project_id)
    latest = latest_project_snapshot(db, project_id)
    return ProjectGeometryRead(
        **payload,
        latest_snapshot_id=latest.project_snapshot_id if latest else None,
        latest_snapshot_hash_sha256=latest.snapshot_hash_sha256 if latest else None,
    )


def create_project_snapshot(db: Session, project_id: str, snapshot_kind: str = "geometry", created_by: str | None = None) -> ProjectVersionSnapshot:
    payload = project_geometry_payload(db, project_id)
    snapshot_hash = stable_json_hash(payload)
    row = ProjectVersionSnapshot(
        project_snapshot_id=_id("psnap"),
        project_id=project_id,
        snapshot_kind=snapshot_kind,
        snapshot_hash_sha256=snapshot_hash,
        snapshot_payload=payload,
        created_by=created_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def latest_project_snapshot(db: Session, project_id: str) -> ProjectVersionSnapshot | None:
    return (
        db.query(ProjectVersionSnapshot)
        .filter(ProjectVersionSnapshot.project_id == project_id)
        .order_by(ProjectVersionSnapshot.created_at.desc())
        .first()
    )


def list_project_snapshots(db: Session, project_id: str) -> list[ProjectVersionSnapshot]:
    return (
        db.query(ProjectVersionSnapshot)
        .filter(ProjectVersionSnapshot.project_id == project_id)
        .order_by(ProjectVersionSnapshot.created_at.desc())
        .all()
    )


def _project_snapshot_for_validation(db: Session, project_id: str) -> ProjectSnapshot:
    payload = project_geometry_payload(db, project_id)
    site_payload = payload["site"] or {"postcode": None, "lat": None, "lon": None, "timezone": "Europe/London"}
    roof_payloads = payload["roof_planes"]
    return ProjectSnapshot(
        project_id=project_id,
        site=ProjectSite(
            postcode=site_payload.get("postcode"),
            lat=site_payload.get("lat"),
            lon=site_payload.get("lon"),
            timezone=site_payload.get("timezone") or "Europe/London",
        ),
        roof_planes=[
            ProjectRoofPlane(
                roof_plane_id=roof["roof_plane_id"],
                pitch_deg=roof["pitch_deg"],
                azimuth_deg=roof["azimuth_deg"],
                height_m=roof.get("height_m"),
                roof_type=roof.get("roof_type", "unknown"),
                polygon_local_m=roof.get("polygon_local_m"),
            )
            for roof in roof_payloads
        ],
    )


def _extra_geometry_report(db: Session, project_id: str) -> ValidationReport:
    issues = []
    site = db.query(SiteRecord).filter(SiteRecord.project_id == project_id).one_or_none()
    if site is None:
        issues.append(issue(
            "SITE_MISSING",
            Severity.error,
            ValidationArea.roof_geometry,
            "Project has no site record. Solar/yield calculations need location evidence.",
            path="site",
            suggested_fix="Add postcode or lat/lon before yield/shade work.",
        ))
    roofs = db.query(RoofPlaneRecord).filter(RoofPlaneRecord.project_id == project_id).all()
    for idx, roof in enumerate(roofs):
        if roof.polygon_local_m and roof.area_m2 is not None and roof.area_m2 <= 0:
            issues.append(issue(
                "ROOF_AREA_ZERO",
                Severity.blocker,
                ValidationArea.roof_geometry,
                "Roof polygon area is zero. Panel packing cannot use this plane.",
                path=f"roof_planes[{idx}].polygon_local_m",
                suggested_fix="Redraw the roof polygon with real area.",
                blocks=True,
            ))
        if roof.height_m is not None and roof.edge_zone_depth_m is None:
            issues.append(issue(
                "EDGE_ZONE_NOT_CALCULATED",
                Severity.error,
                ValidationArea.mounting,
                "Roof height exists but edge-zone depth was not calculated.",
                path=f"roof_planes[{idx}].edge_zone_depth_m",
                suggested_fix="Re-save roof plane or rerun geometry precheck.",
            ))
    status = "blocked" if any(i.blocks_status for i in issues) else ("warnings" if issues else "ok")
    return ValidationReport(status=status, issues=issues, summary={"project_id": project_id})


def validate_project_geometry(db: Session, project_id: str) -> ValidationReport:
    validation_snapshot = _project_snapshot_for_validation(db, project_id)
    base = validate_project_for_mounting_precheck(validation_snapshot)
    extra = _extra_geometry_report(db, project_id)
    issues = list(base.issues) + list(extra.issues)
    status = "blocked" if any(i.blocks_status for i in issues) else ("warnings" if issues else "ok")
    return ValidationReport(status=status, issues=issues, summary={"project_id": project_id, "issue_count": len(issues)})


def run_project_mounting_precheck(db: Session, project_id: str, created_by: str | None = None) -> MountingPrecheckRead:
    snapshot_row = create_project_snapshot(db, project_id, snapshot_kind="mounting_precheck_input", created_by=created_by)
    validation = validate_project_geometry(db, project_id)
    output_payload = validation.model_dump(mode="json")
    calc_payload = CalculationRunCreate(
        project_id=project_id,
        run_type=CalculationRunType.mounting_precheck,
        engine_version="0.3.0-roof-geometry-precheck",
        input_snapshot=snapshot_row.snapshot_payload,
        warnings=[f"{item.code}: {item.message}" for item in validation.issues],
    )
    built = build_calculation_run(calc_payload)
    output_hash = stable_json_hash(output_payload)
    calculation_status = "survey_required" if validation.has_blockers else built.status.value
    row = CalculationRun(
        run_id=built.run_id,
        project_id=project_id,
        run_type=built.run_type.value,
        status=calculation_status,
        software_version=built.software_version,
        engine_version=built.engine_version,
        input_snapshot_hash_sha256=built.input_snapshot_hash_sha256,
        output_hash_sha256=output_hash,
        product_data_snapshot_id=built.product_data_snapshot_id,
        assumption_set_id=built.assumption_set_id,
        warnings=built.warnings,
        input_snapshot=built.input_snapshot,
        output_snapshot=output_payload,
    )
    db.add(row)
    db.commit()
    structural = StructuralTruthState.S0_unknown if validation.has_blockers else StructuralTruthState.S2_manufacturer_calc_required
    return MountingPrecheckRead(
        project_id=project_id,
        structural_truth_state=structural,
        calculation_run_id=row.run_id,
        calculation_status=row.status,
        validation_report=validation,
        input_snapshot_hash_sha256=row.input_snapshot_hash_sha256,
        output_hash_sha256=row.output_hash_sha256,
    )


def project_geometry_self_check(db: Session) -> dict:
    blocked_project = create_project(db, ProjectCreate(title="Geometry self-check missing height", created_by="self_check"))
    upsert_site(db, blocked_project.project_id, SiteCreate(postcode="EX14 3JF", source_type="postcode_lookup", source_confidence=0.7))
    add_roof_plane(db, blocked_project.project_id, RoofPlaneCreate(
        roof_type="unknown", pitch_deg=35, azimuth_deg=180, height_m=None,
        polygon_local_m=[[0, 0], [6, 0], [6, 4], [0, 4]], source_type="manual", source_confidence=0.5,
    ))
    blocked = run_project_mounting_precheck(db, blocked_project.project_id, created_by="self_check")

    ok_project = create_project(db, ProjectCreate(title="Geometry self-check known tiled roof", created_by="self_check"))
    upsert_site(db, ok_project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, source_type="postcode_lookup", source_confidence=0.7))
    roof = add_roof_plane(db, ok_project.project_id, RoofPlaneCreate(
        roof_type="tiled_pitched", pitch_deg=35, azimuth_deg=180, height_m=6.0,
        polygon_local_m=[[0, 0], [10, 0], [10, 5], [0, 5]], source_type="manual", source_confidence=0.6,
    ))
    add_obstruction(db, ok_project.project_id, ObstructionCreate(
        obstruction_type="chimney", roof_plane_id=roof.roof_plane_id, height_m=1.2,
        polygon_local_m=[[2, 2], [2.5, 2], [2.5, 2.5], [2, 2.5]], label="test chimney", source_type="manual",
    ))
    snap1 = create_project_snapshot(db, ok_project.project_id, created_by="self_check")
    snap2 = create_project_snapshot(db, ok_project.project_id, created_by="self_check")
    ok = run_project_mounting_precheck(db, ok_project.project_id, created_by="self_check")
    return {
        "status": "ok" if blocked.structural_truth_state == StructuralTruthState.S0_unknown and ok.structural_truth_state == StructuralTruthState.S2_manufacturer_calc_required and snap1.snapshot_hash_sha256 == snap2.snapshot_hash_sha256 else "failed",
        "project_id": ok_project.project_id,
        "blocked_missing_height": blocked.structural_truth_state == StructuralTruthState.S0_unknown,
        "ok_known_roof_state": ok.structural_truth_state.value,
        "snapshot_hash_stable": snap1.snapshot_hash_sha256 == snap2.snapshot_hash_sha256,
        "roof_plane_area_m2": roof.area_m2,
    }

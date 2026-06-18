from __future__ import annotations

from app.schemas.catalogue import ProductQualityLevel, ProductRead
from app.schemas.project import ProjectSnapshot, RoofType
from app.schemas.validation import Severity, ValidationArea, ValidationIssue, ValidationReport


def issue(code: str, severity: Severity, area: ValidationArea, message: str, path: str | None = None, suggested_fix: str | None = None, blocks: bool = False) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        area=area,
        message=message,
        path=path,
        suggested_fix=suggested_fix,
        blocks_status=blocks,
    )


def validate_product_for_design(product: ProductRead) -> ValidationReport:
    issues: list[ValidationIssue] = []
    if product.quality_level in {ProductQualityLevel.Q0, ProductQualityLevel.Q1}:
        issues.append(issue(
            "PRODUCT_DATA_NOT_REVIEWED", Severity.blocker, ValidationArea.product_data,
            "Product is not reviewed to Q3+ and cannot be used in final design/BOM.",
            path=f"products.{product.product_id}.quality_level",
            suggested_fix="Attach manufacturer datasheet, parse specs, and complete human review.",
            blocks=True,
        ))
    if product.nuvision_url is None:
        issues.append(issue(
            "PRODUCT_SOURCE_URL_MISSING", Severity.warning, ValidationArea.product_data,
            "Product has no NuVision/source URL. Search/display is allowed, but evidence chain is weaker.",
            path=f"products.{product.product_id}.nuvision_url",
            suggested_fix="Add NuVision product page or manufacturer product URL.",
        ))
    return ValidationReport(status="blocked" if any(i.blocks_status for i in issues) else "ok", issues=issues, summary={"product_id": product.product_id})


def validate_project_for_mounting_precheck(project: ProjectSnapshot) -> ValidationReport:
    issues: list[ValidationIssue] = []
    if not project.roof_planes:
        issues.append(issue(
            "NO_ROOF_PLANES", Severity.blocker, ValidationArea.roof_geometry,
            "Project has no roof planes. Cannot run mounting or layout checks.",
            path="roof_planes", suggested_fix="Draw or import at least one roof plane.", blocks=True,
        ))

    for idx, roof in enumerate(project.roof_planes):
        base = f"roof_planes[{idx}]"
        if roof.height_m is None:
            issues.append(issue(
                "ROOF_HEIGHT_MISSING", Severity.blocker, ValidationArea.mounting,
                "Roof height is missing. Wind/edge-zone status is blocked.",
                path=f"{base}.height_m", suggested_fix="Enter measured building/roof height from survey.", blocks=True,
            ))
        if roof.roof_type == RoofType.unknown:
            issues.append(issue(
                "ROOF_TYPE_UNKNOWN", Severity.blocker, ValidationArea.mounting,
                "Roof type is unknown. Mounting family cannot be recommended.",
                path=f"{base}.roof_type", suggested_fix="Select tiled, slate, trapezoidal, standing seam, flat, or ground mount.", blocks=True,
            ))
        if roof.polygon_local_m is None:
            issues.append(issue(
                "ROOF_POLYGON_MISSING", Severity.error, ValidationArea.roof_geometry,
                "Roof polygon is missing. Layout and edge-zone geometry cannot be checked.",
                path=f"{base}.polygon_local_m", suggested_fix="Draw roof plane polygon on map or import from survey.",
            ))
        if roof.pitch_deg > 60 and roof.roof_type != RoofType.ground_mount:
            issues.append(issue(
                "UNUSUAL_ROOF_PITCH", Severity.warning, ValidationArea.mounting,
                "Roof pitch is unusually steep for common PV mounting workflows.",
                path=f"{base}.pitch_deg", suggested_fix="Confirm pitch from survey and check manufacturer mounting limits.",
            ))

    status = "blocked" if any(i.blocks_status for i in issues) else ("warnings" if issues else "ok")
    return ValidationReport(status=status, issues=issues, summary={"project_id": project.project_id, "roof_plane_count": len(project.roof_planes)})


def validate_calculation_inputs(run_type: str, input_snapshot: dict) -> ValidationReport:
    issues: list[ValidationIssue] = []
    if not isinstance(input_snapshot, dict):
        issues.append(issue(
            "INPUT_NOT_OBJECT", Severity.blocker, ValidationArea.calculation,
            "Calculation input snapshot must be a JSON object.", path="input_snapshot", blocks=True,
        ))
    if run_type in {"mounting_precheck", "roof_geometry", "panel_packing"}:
        if "site" not in input_snapshot:
            issues.append(issue("SITE_MISSING", Severity.error, ValidationArea.calculation, "Site snapshot missing from calculation input.", path="input_snapshot.site"))
        if "roof" not in input_snapshot and "roof_planes" not in input_snapshot:
            issues.append(issue("ROOF_MISSING", Severity.error, ValidationArea.calculation, "Roof geometry missing from calculation input.", path="input_snapshot.roof"))
    status = "blocked" if any(i.blocks_status for i in issues) else ("warnings" if issues else "ok")
    return ValidationReport(status=status, issues=issues, summary={"run_type": run_type})

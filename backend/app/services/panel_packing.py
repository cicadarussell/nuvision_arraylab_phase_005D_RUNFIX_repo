from __future__ import annotations

from dataclasses import dataclass
from math import fmod
from uuid import uuid4

from shapely.affinity import rotate
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely.geometry.base import BaseGeometry
from sqlalchemy.orm import Session

from app.models.db_models import CalculationRun, PanelPackingOverrideRecord, Product, ProductSpec
from app.schemas.calculation import CalculationRunCreate, CalculationRunType
from app.schemas.geometry import (
    PanelModelRead,
    ReviewedPanelModelsRead,
    PanelPackingAlignment,
    PanelLayoutEditContractRead,
    PanelPackingCandidateExportRead,
    PanelPackingGovernanceSelfCheckRead,
    PanelPackingOverrideCreate,
    PanelPackingOverrideHistoryRead,
    PanelPackingOverrideRead,
    PanelPackingRequest,
    PanelPackingResultRead,
    PanelPackingScoreGoal,
    PanelPackingSelfCheckRead,
    PanelPlacementRead,
    SelectedPanelLayoutExportRead,
)
from app.schemas.validation import Severity, ValidationArea, ValidationIssue, ValidationReport
from app.services.calculation_run import build_calculation_run
from app.services.geometry_quality import build_geometry_quality_report
from app.services.hash_utils import stable_json_hash
from app.services.map_geometry_sync import local_m_to_lonlat
from app.services.project_geometry import (
    ProjectGeometryError,
    add_roof_plane,
    create_project,
    get_project_geometry,
    project_geometry_payload,
    upsert_site,
)
from app.schemas.geometry import ProjectCreate, RoofPlaneCreate, SiteCreate
from app.schemas.project import RoofType


class PanelPackingError(Exception):
    pass


@dataclass(frozen=True)
class PanelModel:
    panel_model_id: str
    product_id: str | None
    title: str
    manufacturer: str | None
    length_m: float
    width_m: float
    power_stc_w: float
    thickness_mm: float | None = None
    weight_kg: float | None = None
    source_quality: str = "dev_fallback"
    design_ready: bool = False
    source: str = "development fixture"


DEV_FALLBACK_PANELS: tuple[PanelModel, ...] = (
    PanelModel(
        panel_model_id="DEV_430W_1722x1134",
        product_id=None,
        title="Development fallback 430 W module 1722 x 1134 mm",
        manufacturer="ArrayLab fixture",
        length_m=1.722,
        width_m=1.134,
        power_stc_w=430,
        source_quality="dev_fallback",
        design_ready=False,
        source="development fixture; never final design truth",
    ),
    PanelModel(
        panel_model_id="DEV_600W_2333x1134",
        product_id=None,
        title="Development fallback 600 W module 2333 x 1134 mm",
        manufacturer="ArrayLab fixture",
        length_m=2.333,
        width_m=1.134,
        power_stc_w=600,
        source_quality="dev_fallback",
        design_ready=False,
        source="development fixture; never final design truth",
    ),
)

CRITICAL_PANEL_FIELDS = {"power_stc_w", "length_mm", "width_mm"}
DESIGN_READY_SPEC_QUALITY = {"Q3_reviewed", "Q4_manufacturer_confirmed"}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _issue(code: str, severity: Severity, message: str, path: str, fix: str, blocks: bool = False) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        area=ValidationArea.panel_packing,
        message=message,
        path=path,
        suggested_fix=fix,
        blocks_status=blocks,
    )


def _product_specs_by_field(product: Product) -> dict[str, ProductSpec]:
    specs: dict[str, ProductSpec] = {}
    for spec in product.specs:
        if spec.field_name not in CRITICAL_PANEL_FIELDS:
            continue
        if spec.quality_level not in DESIGN_READY_SPEC_QUALITY:
            continue
        if spec.review_status not in {"reviewed", "approved"}:
            continue
        if spec.value_number is None:
            continue
        specs[spec.field_name] = spec
    return specs


def _panel_from_product(product: Product) -> tuple[PanelModel | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    if product.category not in {"panel", "solar_panel", "solar_pv_panel", "solar_pv_panels"}:
        issues.append(_issue(
            "PRODUCT_NOT_PANEL",
            Severity.warning,
            f"Product {product.product_id} is category {product.category}, not a PV panel.",
            f"products.{product.product_id}.category",
            "Use only reviewed PV panel products for panel packing.",
        ))
        return None, issues
    specs = _product_specs_by_field(product)
    missing = sorted(CRITICAL_PANEL_FIELDS - set(specs.keys()))
    if missing:
        issues.append(_issue(
            "PANEL_Q3_SPECS_MISSING",
            Severity.error,
            f"Product {product.product_id} is missing reviewed panel specs: {', '.join(missing)}.",
            f"products.{product.product_id}.specs",
            "Promote manufacturer datasheet candidates to Q3 before using this product for design.",
            blocks=True,
        ))
        return None, issues
    length_m = float(specs["length_mm"].value_number) / 1000.0
    width_m = float(specs["width_mm"].value_number) / 1000.0
    power = float(specs["power_stc_w"].value_number)
    if length_m <= 0 or width_m <= 0 or power <= 0:
        issues.append(_issue(
            "PANEL_SPEC_IMPOSSIBLE",
            Severity.blocker,
            f"Product {product.product_id} has impossible length/width/power specs.",
            f"products.{product.product_id}.specs",
            "Review datasheet values and units.",
            blocks=True,
        ))
        return None, issues
    return PanelModel(
        panel_model_id=product.product_id,
        product_id=product.product_id,
        title=product.title,
        manufacturer=product.manufacturer,
        length_m=length_m,
        width_m=width_m,
        power_stc_w=power,
        source_quality="Q3_or_Q4_reviewed",
        design_ready=product.design_ready and product.quality_level in DESIGN_READY_SPEC_QUALITY,
        source="reviewed_product_specs",
    ), issues


def list_reviewed_panel_model_options(db: Session, include_dev_fallback: bool = False) -> ReviewedPanelModelsRead:
    """Return reviewed panel choices for the frontend picker.

    This endpoint deliberately exposes only Q3/Q4-reviewed product-spec panel models as
    selectable design inputs. Development fallbacks can be shown only when explicitly
    requested, and are marked non-design-ready so the UI cannot confuse test geometry
    with NuVision engineering truth. Humanity should not need this much hand-holding,
    and yet here we are.
    """
    issues: list[ValidationIssue] = []
    models: list[PanelModel] = []
    products = (
        db.query(Product)
        .filter(Product.category.in_(["panel", "solar_panel", "solar_pv_panel", "solar_pv_panels"]))
        .order_by(Product.manufacturer, Product.title, Product.product_id)
        .all()
    )
    excluded_count = 0
    for product in products:
        model, product_issues = _panel_from_product(product)
        if model and model.design_ready:
            models.append(model)
        else:
            excluded_count += 1
            issues.extend(product_issues)
            if model and not model.design_ready:
                issues.append(_issue(
                    "PANEL_MODEL_NOT_DESIGN_READY",
                    Severity.warning,
                    f"Product {product.product_id} has reviewed dimensions/power but is not marked design-ready.",
                    f"products.{product.product_id}.design_ready",
                    "Recalculate product design readiness after approving all critical datasheet specs.",
                ))

    if include_dev_fallback:
        models.extend(DEV_FALLBACK_PANELS)
        issues.append(_issue(
            "DEV_FALLBACK_PANEL_LISTED",
            Severity.warning,
            "Development fallback panels are listed for local preview only and cannot be used for final design.",
            "include_dev_fallback",
            "Use reviewed Q3/Q4 NuVision product models before final mode.",
        ))

    status = "ok" if models and not issues else "warnings" if models else "empty"
    report = ValidationReport(status=status if status != "empty" else "blocked", issues=issues)
    return ReviewedPanelModelsRead(
        status=status,
        models=[_panel_read(model) for model in models],
        excluded_count=excluded_count,
        validation_report=report,
        summary={
            "reviewed_models": sum(1 for model in models if model.source_quality != "dev_fallback"),
            "dev_fallback_models": sum(1 for model in models if model.source_quality == "dev_fallback"),
            "excluded_products": excluded_count,
            "final_mode_requires_product_ids": True,
            "truth_boundary": "Q3/Q4 reviewed ProductSpec rows only; fallback panels are preview-only",
        },
    )


def load_panel_models(db: Session, request: PanelPackingRequest) -> tuple[list[PanelModel], list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    models: list[PanelModel] = []
    products: list[Product] = []
    if request.panel_product_ids:
        for product_id in request.panel_product_ids:
            product = db.get(Product, product_id)
            if not product:
                issues.append(_issue(
                    "PANEL_PRODUCT_NOT_FOUND",
                    Severity.error,
                    f"Panel product {product_id} was not found.",
                    f"panel_product_ids.{product_id}",
                    "Import/apply the reviewed product first or remove it from the request.",
                    blocks=True,
                ))
                continue
            products.append(product)
    else:
        products = (
            db.query(Product)
            .filter(Product.category.in_(["panel", "solar_panel", "solar_pv_panel", "solar_pv_panels"]))
            .order_by(Product.product_id)
            .all()
        )
    for product in products:
        model, product_issues = _panel_from_product(product)
        issues.extend(product_issues)
        if model:
            models.append(model)

    if not models and request.allow_dev_fallback_panels:
        models.extend(DEV_FALLBACK_PANELS)
        issues.append(_issue(
            "DEV_FALLBACK_PANEL_USED",
            Severity.warning,
            "No reviewed Q3/Q4 panel models were available, so development fallback panels were used.",
            "panel_models",
            "Review manufacturer datasheet specs before issuing any real quote/design.",
        ))
    elif not models:
        issues.append(_issue(
            "NO_REVIEWED_PANEL_MODELS",
            Severity.blocker,
            "No reviewed panel models are available for packing.",
            "panel_models",
            "Review a manufacturer datasheet into Q3 specs or enable development fallback for preview-only testing.",
            blocks=True,
        ))
    return models, issues


def _to_poly(points: list[list[float]] | None) -> Polygon | None:
    if not points or len(points) < 3:
        return None
    return Polygon([(float(x), float(y)) for x, y in points])


def _rect_points(rect: BaseGeometry) -> list[list[float]]:
    coords = list(rect.exterior.coords)[:-1]
    return [[round(float(x), 4), round(float(y), 4)] for x, y in coords]


def _normalise_rotation_deg(angle: float) -> float:
    """Return a stable angle in [-90, 90) for evidence hashing and UI display."""
    normalised = fmod(angle + 90.0, 180.0)
    if normalised < 0:
        normalised += 180.0
    return round(normalised - 90.0, 4)


def _roof_row_rotation_deg(roof_azimuth_deg: float, alignment: PanelPackingAlignment) -> float:
    if alignment == PanelPackingAlignment.axis_aligned:
        return 0.0
    # Roof azimuth is compass bearing of the roof face. Panel rows are usually perpendicular
    # to that face direction. In ArrayLab's local frame x=east and y=north, south-facing
    # roofs (azimuth 180) produce an east-west row direction, i.e. 0 degrees from x-axis.
    return _normalise_rotation_deg(180.0 - float(roof_azimuth_deg))


def _candidate_score_explanation(goal: PanelPackingScoreGoal) -> str:
    if goal == PanelPackingScoreGoal.best_fit:
        return "area utilisation is ranked first, with total kWp as a tie-breaker"
    if goal == PanelPackingScoreGoal.fewer_panels:
        return "power per panel is ranked first, with a penalty for more modules"
    if goal == PanelPackingScoreGoal.aesthetic:
        return "row neatness, orphan-panel avoidance, and layout readability are ranked first"
    return "total DC power in kWp is ranked first"


def _candidate_goal_scores(panel_count: int, total_power_w: float, area_util: float, aesthetic_score: float) -> dict[str, float]:
    if panel_count <= 0:
        return {
            "max_kwp": -1.0,
            "best_fit": -1.0,
            "fewer_panels": -1.0,
            "aesthetic": -1.0,
        }
    return {
        "max_kwp": round(float(total_power_w), 4),
        "best_fit": round(float(area_util * 1_000_000 + total_power_w / 1000.0), 4),
        "fewer_panels": round(float((total_power_w / panel_count) * 1000 - panel_count * 20), 4),
        "aesthetic": round(float(aesthetic_score * 1_000_000 + total_power_w / 1000.0), 4),
    }


def _candidate_score(goal: PanelPackingScoreGoal, panel_count: int, total_power_w: float, area_util: float, aesthetic_score: float = 0.0) -> float:
    scores = _candidate_goal_scores(panel_count, total_power_w, area_util, aesthetic_score)
    return scores.get(goal.value if hasattr(goal, "value") else str(goal), scores["max_kwp"])


def _pack_one_candidate(
    allowed: BaseGeometry,
    roof_plane_id: str,
    model: PanelModel,
    orientation: str,
    row_gap_m: float,
    column_gap_m: float,
    max_panels: int,
    rotation_deg: float = 0.0,
) -> list[PanelPlacementRead]:
    if orientation == "portrait":
        panel_w = model.width_m
        panel_h = model.length_m
    elif orientation == "landscape":
        panel_w = model.length_m
        panel_h = model.width_m
    else:
        raise PanelPackingError(f"Unsupported orientation {orientation}")
    if panel_w <= 0 or panel_h <= 0:
        return []

    # Rotate the allowed polygon into a row-aligned frame, pack axis-aligned rectangles, then
    # rotate placed rectangles back to project coordinates. This is deliberately simple and
    # testable before OR-Tools or advanced aesthetic optimisation gets invited to make soup.
    working_allowed = rotate(allowed, -rotation_deg, origin=(0, 0), use_radians=False) if rotation_deg else allowed
    minx, miny, maxx, maxy = working_allowed.bounds
    placements: list[PanelPlacementRead] = []
    y = miny
    row_index = 0
    epsilon = 1e-9
    while y + panel_h <= maxy + epsilon and len(placements) < max_panels:
        x = minx
        col_index = 0
        while x + panel_w <= maxx + epsilon and len(placements) < max_panels:
            rect = box(x, y, x + panel_w, y + panel_h)
            if working_allowed.covers(rect):
                placed_rect = rotate(rect, rotation_deg, origin=(0, 0), use_radians=False) if rotation_deg else rect
                placements.append(PanelPlacementRead(
                    placement_id=f"plc_{roof_plane_id}_{model.panel_model_id}_{orientation}_{len(placements)+1:04d}",
                    roof_plane_id=roof_plane_id,
                    panel_model_id=model.panel_model_id,
                    product_id=model.product_id,
                    orientation=orientation,
                    power_stc_w=model.power_stc_w,
                    width_m=round(panel_w, 4),
                    height_m=round(panel_h, 4),
                    centre_local_m=[round(float(placed_rect.centroid.x), 4), round(float(placed_rect.centroid.y), 4)],
                    polygon_local_m=_rect_points(placed_rect),
                    rotation_deg=round(rotation_deg, 4),
                    row_index=row_index,
                    column_index=col_index,
                ))
            x += panel_w + column_gap_m
            col_index += 1
        y += panel_h + row_gap_m
        row_index += 1
    return placements



def _placement_polygon(placement: PanelPlacementRead) -> Polygon:
    return Polygon([(float(x), float(y)) for x, y in placement.polygon_local_m])


def _pack_mixed_candidate(
    allowed: BaseGeometry,
    roof_plane_id: str,
    model: PanelModel,
    row_gap_m: float,
    column_gap_m: float,
    max_panels: int,
    rotation_deg: float = 0.0,
) -> list[PanelPlacementRead]:
    """Pack a simple mixed portrait+landscape candidate.

    This is not yet a true optimiser. It is a transparent deterministic heuristic:
    place portrait modules first, subtract them from the allowed area, then try landscape
    modules in the remainder. It exists so the UI/debug system can compare mixed layouts
    before OR-Tools or a richer aesthetic solver arrives to make the room smell of maths.
    """
    portrait = _pack_one_candidate(
        allowed=allowed,
        roof_plane_id=roof_plane_id,
        model=model,
        orientation="portrait",
        row_gap_m=row_gap_m,
        column_gap_m=column_gap_m,
        max_panels=max_panels,
        rotation_deg=rotation_deg,
    )
    if not portrait:
        return _pack_one_candidate(
            allowed=allowed,
            roof_plane_id=roof_plane_id,
            model=model,
            orientation="landscape",
            row_gap_m=row_gap_m,
            column_gap_m=column_gap_m,
            max_panels=max_panels,
            rotation_deg=rotation_deg,
        )
    used_polys = [_placement_polygon(p) for p in portrait]
    remaining = allowed.difference(unary_union(used_polys))
    landscape = _pack_one_candidate(
        allowed=remaining,
        roof_plane_id=roof_plane_id,
        model=model,
        orientation="landscape",
        row_gap_m=row_gap_m,
        column_gap_m=column_gap_m,
        max_panels=max(0, max_panels - len(portrait)),
        rotation_deg=rotation_deg,
    )
    if not landscape:
        return portrait
    # Offset row indexes for the second pass so candidate summaries remain understandable.
    max_row = max((p.row_index or 0) for p in portrait) + 1
    adjusted_landscape = []
    for placement in landscape:
        data = placement.model_dump()
        data["row_index"] = (data.get("row_index") or 0) + max_row
        adjusted_landscape.append(PanelPlacementRead(**data))
    return portrait + adjusted_landscape


def _candidate_aesthetic_metrics(placements: list[PanelPlacementRead]) -> dict:
    if not placements:
        return {
            "row_count": 0,
            "orphan_panel_count": 0,
            "row_straightness_score": 0.0,
            "aesthetic_score": 0.0,
            "orientation_mix_penalty": 0.0,
        }
    rows: dict[tuple[str, int], int] = {}
    for placement in placements:
        key = (placement.orientation.value if hasattr(placement.orientation, "value") else str(placement.orientation), placement.row_index or 0)
        rows[key] = rows.get(key, 0) + 1
    row_counts = list(rows.values())
    row_count = len(row_counts)
    max_row = max(row_counts) if row_counts else 0
    modal_count = max(set(row_counts), key=row_counts.count) if row_counts else 0
    consistent_rows = sum(1 for count in row_counts if count == modal_count)
    row_straightness = consistent_rows / row_count if row_count else 0.0
    orphan_count = sum(1 for count in row_counts if count == 1 and len(placements) > 1)
    orientation_count = len({p.orientation.value if hasattr(p.orientation, "value") else str(p.orientation) for p in placements})
    orientation_mix_penalty = 6.0 if orientation_count > 1 else 0.0
    # Transparent heuristic, not an aesthetic deity. Penalise ragged/orphan rows.
    aesthetic_score = 100.0
    aesthetic_score -= (1.0 - row_straightness) * 28.0
    aesthetic_score -= orphan_count * 12.0
    aesthetic_score -= orientation_mix_penalty
    if max_row <= 2 and len(placements) > 4:
        aesthetic_score -= 8.0
    return {
        "row_count": row_count,
        "orphan_panel_count": orphan_count,
        "row_straightness_score": round(row_straightness, 4),
        "aesthetic_score": round(max(0.0, min(100.0, aesthetic_score)), 4),
        "orientation_mix_penalty": orientation_mix_penalty,
    }


def _rankings_by_goal(candidate_summaries: list[dict]) -> dict[str, list[dict]]:
    goals = ["max_kwp", "best_fit", "fewer_panels", "aesthetic"]
    rankings: dict[str, list[dict]] = {}
    for goal in goals:
        ranked = sorted(
            candidate_summaries,
            key=lambda c: c.get("goal_scores", {}).get(goal, -1),
            reverse=True,
        )
        rankings[goal] = [
            {
                "rank": idx + 1,
                "candidate_id": item.get("candidate_id"),
                "roof_plane_id": item.get("roof_plane_id"),
                "score": item.get("goal_scores", {}).get(goal, -1),
                "panel_count": item.get("panel_count"),
                "total_kwp": item.get("total_kwp"),
                "aesthetic_score": item.get("aesthetic_score"),
                "layout_style": item.get("layout_style"),
                "orientation": item.get("orientation"),
            }
            for idx, item in enumerate(ranked[:10])
        ]
    return rankings


def _panel_read(model: PanelModel) -> PanelModelRead:
    return PanelModelRead(
        panel_model_id=model.panel_model_id,
        product_id=model.product_id,
        title=model.title,
        manufacturer=model.manufacturer,
        length_m=round(model.length_m, 4),
        width_m=round(model.width_m, 4),
        power_stc_w=round(model.power_stc_w, 3),
        source_quality=model.source_quality,
        design_ready=model.design_ready,
        source=model.source,
    )


def _placements_to_geojson(db: Session, project_id: str, placements: list[PanelPlacementRead]) -> dict | None:
    try:
        geometry = get_project_geometry(db, project_id)
    except Exception:
        return None
    if geometry.site is None or geometry.site.lat is None or geometry.site.lon is None:
        return None
    origin_lat, origin_lon = float(geometry.site.lat), float(geometry.site.lon)
    features = []
    for placement in placements:
        ring = [local_m_to_lonlat(float(x), float(y), origin_lat, origin_lon) for x, y in placement.polygon_local_m]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        features.append({
            "type": "Feature",
            "properties": {
                "kind": "panel_placement",
                "placement_id": placement.placement_id,
                "roof_plane_id": placement.roof_plane_id,
                "panel_model_id": placement.panel_model_id,
                "product_id": placement.product_id,
                "orientation": placement.orientation.value if hasattr(placement.orientation, "value") else str(placement.orientation),
                "power_stc_w": placement.power_stc_w,
                "rotation_deg": placement.rotation_deg,
                "pre_design_only": True,
            },
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    return {"type": "FeatureCollection", "features": features}


def _blocking(issues: list[ValidationIssue]) -> bool:
    return any(i.blocks_status or i.severity == Severity.blocker for i in issues)


def run_panel_packing(db: Session, project_id: str, request: PanelPackingRequest) -> PanelPackingResultRead:
    try:
        geometry_payload = project_geometry_payload(db, project_id)
    except Exception as exc:
        raise ProjectGeometryError(str(exc)) from exc

    quality_report = build_geometry_quality_report(db, project_id)
    issues: list[ValidationIssue] = list(quality_report.validation_report.issues)
    panel_models, panel_issues = load_panel_models(db, request)
    issues.extend(panel_issues)

    if request.design_mode == "final" and not request.panel_product_ids:
        issues.append(_issue(
            "FINAL_DESIGN_REQUIRES_EXPLICIT_PANEL_MODEL",
            Severity.blocker,
            "Final design mode requires explicit reviewed panel product IDs.",
            "panel_product_ids",
            "Select a Q3/Q4 reviewed product model before requesting final design mode.",
            blocks=True,
        ))
    if request.design_mode == "final" and any(model.source_quality == "dev_fallback" for model in panel_models):
        issues.append(_issue(
            "DEV_FALLBACK_BLOCKS_FINAL_DESIGN",
            Severity.blocker,
            "Development fallback panels cannot be used for final design mode.",
            "design_mode",
            "Use reviewed Q3/Q4 manufacturer datasheet panel specs.",
            blocks=True,
        ))
    if request.design_mode == "final" and any(not model.design_ready for model in panel_models):
        issues.append(_issue(
            "FINAL_PANEL_MODEL_NOT_DESIGN_READY",
            Severity.blocker,
            "All selected final-mode panel products must be marked design-ready from reviewed specs.",
            "panel_models",
            "Recalculate product design readiness after approving all critical datasheet specs.",
            blocks=True,
        ))

    all_placements: list[PanelPlacementRead] = []
    candidate_summaries: list[dict] = []
    selected_candidate_ids: list[str] = []
    manual_override_record: dict | None = None
    override_candidate_seen = False
    roof_geometry_by_id = {r.get("roof_plane_id"): r for r in geometry_payload.get("roof_planes", [])}

    if not _blocking(issues):
        for roof in quality_report.roof_planes:
            if request.roof_plane_ids and roof.roof_plane_id not in request.roof_plane_ids:
                continue
            allowed_payload = roof.packer_allowed_area or {}
            allowed = _to_poly(allowed_payload.get("allowed_polygon_local_m"))
            if allowed is None or allowed.is_empty or not allowed.is_valid:
                issues.append(_issue(
                    "PACKER_ALLOWED_AREA_INVALID",
                    Severity.blocker,
                    f"Roof {roof.roof_plane_id} has no valid packer allowed area.",
                    f"roof_planes.{roof.roof_plane_id}.packer_allowed_area",
                    "Run/fix geometry quality before panel packing.",
                    blocks=True,
                ))
                continue
            roof_geometry = roof_geometry_by_id.get(roof.roof_plane_id, {})
            roof_rotation_deg = _roof_row_rotation_deg(float(roof_geometry.get("azimuth_deg") or 180), request.packing_alignment)
            roof_candidates: list[dict] = []
            for model in panel_models:
                layout_jobs: list[tuple[str, str, str | None]] = []
                if request.candidate_layout_mode in {"single_orientation", "all"}:
                    for orientation in request.candidate_orientations:
                        orientation_value = orientation.value if hasattr(orientation, "value") else str(orientation)
                        layout_jobs.append((f"single_{orientation_value}", orientation_value, orientation_value))
                if request.candidate_layout_mode in {"mixed_portrait_landscape", "all"} and {"portrait", "landscape"}.issubset({o.value if hasattr(o, "value") else str(o) for o in request.candidate_orientations}):
                    layout_jobs.append(("mixed_portrait_landscape", "mixed", None))

                for layout_style, orientation_label, orientation_value in layout_jobs:
                    candidate_id = f"cand_{roof.roof_plane_id}_{model.panel_model_id}_{layout_style}_{request.packing_alignment.value if hasattr(request.packing_alignment, 'value') else request.packing_alignment}"
                    if layout_style == "mixed_portrait_landscape":
                        candidate = _pack_mixed_candidate(
                            allowed=allowed,
                            roof_plane_id=roof.roof_plane_id,
                            model=model,
                            row_gap_m=request.row_gap_m,
                            column_gap_m=request.column_gap_m,
                            max_panels=request.max_panels_per_roof,
                            rotation_deg=roof_rotation_deg,
                        )
                    else:
                        candidate = _pack_one_candidate(
                            allowed=allowed,
                            roof_plane_id=roof.roof_plane_id,
                            model=model,
                            orientation=orientation_value or "portrait",
                            row_gap_m=request.row_gap_m,
                            column_gap_m=request.column_gap_m,
                            max_panels=request.max_panels_per_roof,
                            rotation_deg=roof_rotation_deg,
                        )
                    panel_area = sum(p.width_m * p.height_m for p in candidate)
                    total_power = sum(p.power_stc_w for p in candidate)
                    area_util = panel_area / allowed.area if allowed.area > 0 else 0
                    aesthetic_metrics = _candidate_aesthetic_metrics(candidate)
                    goal_scores = _candidate_goal_scores(len(candidate), total_power, area_util, aesthetic_metrics["aesthetic_score"])
                    score = _candidate_score(request.score_goal, len(candidate), total_power, area_util, aesthetic_metrics["aesthetic_score"])
                    reason_codes = []
                    if len(candidate) <= 0:
                        reason_codes.append("NO_PANELS_FIT")
                    if request.score_goal == PanelPackingScoreGoal.max_kwp:
                        reason_codes.append("RANKED_BY_TOTAL_KWP")
                    elif request.score_goal == PanelPackingScoreGoal.best_fit:
                        reason_codes.append("RANKED_BY_AREA_UTILISATION_THEN_POWER")
                    elif request.score_goal == PanelPackingScoreGoal.fewer_panels:
                        reason_codes.append("RANKED_BY_POWER_PER_PANEL_WITH_PANEL_COUNT_PENALTY")
                    elif request.score_goal == PanelPackingScoreGoal.aesthetic:
                        reason_codes.append("RANKED_BY_AESTHETIC_ROW_SCORE")
                    if layout_style == "mixed_portrait_landscape":
                        reason_codes.append("MIXED_PORTRAIT_LANDSCAPE_CANDIDATE")
                    else:
                        reason_codes.append("SINGLE_ORIENTATION_CANDIDATE")
                    if aesthetic_metrics["orphan_panel_count"]:
                        reason_codes.append("ORPHAN_PANEL_PENALTY_APPLIED")
                    if aesthetic_metrics["row_straightness_score"] < 1.0 and len(candidate) > 0:
                        reason_codes.append("RAGGED_ROW_PENALTY_APPLIED")
                    if model.source_quality == "dev_fallback":
                        reason_codes.append("PREVIEW_ONLY_DEV_FALLBACK")
                    else:
                        reason_codes.append("REVIEWED_PRODUCT_SPEC_MODEL")
                    summary = {
                        "candidate_id": candidate_id,
                        "roof_plane_id": roof.roof_plane_id,
                        "panel_model_id": model.panel_model_id,
                        "orientation": orientation_label,
                        "layout_style": layout_style,
                        "packing_alignment": request.packing_alignment.value if hasattr(request.packing_alignment, "value") else str(request.packing_alignment),
                        "score_goal": request.score_goal.value if hasattr(request.score_goal, "value") else str(request.score_goal),
                        "score": round(score, 4),
                        "goal_scores": goal_scores,
                        "rotation_deg": roof_rotation_deg,
                        "panel_count": len(candidate),
                        "total_power_w": round(total_power, 3),
                        "total_kwp": round(total_power / 1000.0, 4),
                        "usable_area_m2": round(float(allowed.area), 3),
                        "panel_area_m2": round(panel_area, 3),
                        "area_utilisation_ratio": round(area_util, 4),
                        "area_utilisation_pct": round(area_util * 100.0, 2),
                        "row_count": aesthetic_metrics["row_count"],
                        "orphan_panel_count": aesthetic_metrics["orphan_panel_count"],
                        "row_straightness_score": aesthetic_metrics["row_straightness_score"],
                        "aesthetic_score": aesthetic_metrics["aesthetic_score"],
                        "orientation_mix_penalty": aesthetic_metrics["orientation_mix_penalty"],
                        "reason_codes": reason_codes,
                        "score_explanation": _candidate_score_explanation(request.score_goal),
                        "selected": False,
                        "manual_override_selected": False,
                    }
                    if request.selected_candidate_override_id and candidate_id == request.selected_candidate_override_id:
                        override_candidate_seen = True
                    roof_candidates.append({"summary": summary, "placements": candidate})
            roof_candidates.sort(key=lambda item: item["summary"]["score"], reverse=True)
            chosen_item = None
            if request.selected_candidate_override_id:
                chosen_item = next((item for item in roof_candidates if item["summary"]["candidate_id"] == request.selected_candidate_override_id), None)
                if chosen_item is not None and not chosen_item["placements"]:
                    issues.append(_issue(
                        "CANDIDATE_OVERRIDE_HAS_NO_PANELS",
                        Severity.blocker,
                        "The selected candidate override has no panel placements.",
                        "selected_candidate_override_id",
                        "Choose a candidate that actually fits panels or change geometry/model/score settings.",
                        blocks=True,
                    ))
                    chosen_item = None
            if chosen_item is None:
                chosen_item = next((item for item in roof_candidates if item["placements"]), None)
            if chosen_item is not None and chosen_item["placements"] and not _blocking(issues):
                chosen_item["summary"]["selected"] = True
                if request.selected_candidate_override_id == chosen_item["summary"]["candidate_id"]:
                    chosen_item["summary"]["manual_override_selected"] = True
                    manual_override_record = {
                        "candidate_id": chosen_item["summary"]["candidate_id"],
                        "roof_plane_id": roof.roof_plane_id,
                        "override_reason": request.override_reason,
                        "selected_by": "request.selected_candidate_override_id",
                        "evidence_required": True,
                        "truth_boundary": "manual override changes preview selection only; it does not bypass Q3/final-mode/structural gates",
                    }
                selected_candidate_ids.append(chosen_item["summary"]["candidate_id"])
                all_placements.extend(chosen_item["placements"])
            candidate_summaries.extend(item["summary"] for item in roof_candidates)

    if request.selected_candidate_override_id and not override_candidate_seen:
        issues.append(_issue(
            "CANDIDATE_OVERRIDE_NOT_FOUND",
            Severity.blocker,
            "The requested candidate override was not generated for this roof/model/settings combination.",
            "selected_candidate_override_id",
            "Use one of the candidate IDs returned by the previous candidate comparison run.",
            blocks=True,
        ))
        all_placements = []
        selected_candidate_ids = []
    if not all_placements and not _blocking(issues):
        issues.append(_issue(
            "NO_PANELS_FIT_USABLE_AREA",
            Severity.warning,
            "No candidate panels fit inside the usable roof area.",
            "placements",
            "Review roof scale, setbacks, obstructions, or try smaller panels.",
        ))

    total_power_w = round(sum(p.power_stc_w for p in all_placements), 3)
    total_kwp = round(total_power_w / 1000.0, 4)
    panel_area_m2 = round(sum(p.width_m * p.height_m for p in all_placements), 3)
    status = "blocked" if _blocking(issues) else ("warnings" if issues else "ok")
    design_status = "preview_only"
    if status == "blocked":
        design_status = "blocked"
    elif request.design_mode == "final" and all(model.source_quality != "dev_fallback" and model.design_ready for model in panel_models):
        design_status = "design_draft_requires_electrical_and_mounting_review"

    panel_geojson = _placements_to_geojson(db, project_id, all_placements)
    candidate_goal_rankings = _rankings_by_goal(candidate_summaries)
    candidate_comparison_payload = {
        "score_goal": request.score_goal.value if hasattr(request.score_goal, "value") else str(request.score_goal),
        "packing_alignment": request.packing_alignment.value if hasattr(request.packing_alignment, "value") else str(request.packing_alignment),
        "candidate_layout_mode": request.candidate_layout_mode.value if hasattr(request.candidate_layout_mode, "value") else str(request.candidate_layout_mode),
        "candidate_summaries": candidate_summaries,
        "candidate_goal_rankings": candidate_goal_rankings,
        "selected_candidate_ids": selected_candidate_ids,
        "manual_override_record": manual_override_record,
    }
    candidate_comparison_hash = stable_json_hash(candidate_comparison_payload)
    output_payload = {
        "project_id": project_id,
        "status": status,
        "design_status": design_status,
        "panel_count": len(all_placements),
        "total_power_w": total_power_w,
        "total_kwp": total_kwp,
        "panel_area_m2": panel_area_m2,
        "placements": [p.model_dump(mode="json") for p in all_placements],
        "candidate_summaries": candidate_summaries,
        "selected_candidate_ids": selected_candidate_ids,
        "candidate_comparison_hash_sha256": candidate_comparison_hash,
        "candidate_compare_count": len(candidate_summaries),
        "candidate_goal_rankings": candidate_goal_rankings,
        "manual_override_record": manual_override_record,
        "candidate_layout_mode": request.candidate_layout_mode.value if hasattr(request.candidate_layout_mode, "value") else str(request.candidate_layout_mode),
        "panel_models": [_panel_read(model).model_dump(mode="json") for model in panel_models],
        "panel_placements_geojson": panel_geojson,
        "packing_alignment": request.packing_alignment.value if hasattr(request.packing_alignment, "value") else str(request.packing_alignment),
        "score_goal": request.score_goal.value if hasattr(request.score_goal, "value") else str(request.score_goal),
        "candidate_goal_rankings": candidate_goal_rankings,
        "manual_override_record": manual_override_record,
        "geometry_quality_report_hash_sha256": quality_report.report_hash_sha256,
        "pre_design_only": True,
        "not_structural_approval": True,
    }
    input_payload = {
        "project_geometry": geometry_payload,
        "geometry_quality_report_hash_sha256": quality_report.report_hash_sha256,
        "panel_models": [_panel_read(model).model_dump(mode="json") for model in panel_models],
        "request": request.model_dump(mode="json"),
    }
    output_hash = stable_json_hash(output_payload)
    input_hash = stable_json_hash(input_payload)
    validation = ValidationReport(
        status=status,
        issues=issues,
        summary={
            "project_id": project_id,
            "panel_count": len(all_placements),
            "total_kwp": total_kwp,
            "design_status": design_status,
            "selected_candidate_ids": selected_candidate_ids,
            "candidate_comparison_hash_sha256": candidate_comparison_hash,
            "candidate_compare_count": len(candidate_summaries),
            "manual_override_recorded": manual_override_record is not None,
            "mixed_candidates": sum(1 for c in candidate_summaries if c.get("layout_style") == "mixed_portrait_landscape"),
            "pre_design_only": True,
            "not_structural_approval": True,
        },
    )

    built = build_calculation_run(CalculationRunCreate(
        project_id=project_id,
        run_type=CalculationRunType.panel_packing,
        engine_version="0.4.3-mixed-aesthetic-candidate-optimizer",
        input_snapshot=input_payload,
        warnings=[f"{i.code}: {i.message}" for i in issues],
    ))
    calc_status = "failed" if status == "blocked" else "preview"
    row = CalculationRun(
        run_id=built.run_id,
        project_id=project_id,
        run_type=built.run_type.value,
        status=calc_status,
        software_version=built.software_version,
        engine_version=built.engine_version,
        input_snapshot_hash_sha256=input_hash,
        output_hash_sha256=output_hash,
        product_data_snapshot_id=built.product_data_snapshot_id,
        assumption_set_id=built.assumption_set_id,
        warnings=built.warnings,
        input_snapshot=input_payload,
        output_snapshot=output_payload,
    )
    db.add(row)
    db.commit()

    return PanelPackingResultRead(
        project_id=project_id,
        status=status,
        design_status=design_status,
        calculation_run_id=row.run_id,
        input_snapshot_hash_sha256=input_hash,
        output_hash_sha256=output_hash,
        geometry_quality_report_hash_sha256=quality_report.report_hash_sha256,
        validation_report=validation,
        panel_models=[_panel_read(model) for model in panel_models],
        placements=all_placements,
        candidate_summaries=candidate_summaries,
        selected_candidate_ids=selected_candidate_ids,
        panel_placements_geojson=panel_geojson,
        summary=output_payload | {"calculation_run_id": row.run_id},
    )



def export_panel_packing_candidate_run(db: Session, project_id: str, calculation_run_id: str) -> PanelPackingCandidateExportRead:
    row = db.get(CalculationRun, calculation_run_id)
    if row is None or row.project_id != project_id or row.run_type != "panel_packing":
        raise PanelPackingError("Panel packing calculation run was not found for this project.")
    output = row.output_snapshot or {}
    return PanelPackingCandidateExportRead(
        project_id=project_id,
        calculation_run_id=calculation_run_id,
        input_snapshot_hash_sha256=row.input_snapshot_hash_sha256,
        output_hash_sha256=row.output_hash_sha256 or stable_json_hash(output),
        candidate_comparison_hash_sha256=output.get("candidate_comparison_hash_sha256"),
        selected_candidate_ids=output.get("selected_candidate_ids") or [],
        candidate_summaries=output.get("candidate_summaries") or [],
        placements=output.get("placements") or [],
        manual_override_record=output.get("manual_override_record"),
        truth_boundary="panel packing export is pre-design evidence; it is not structural, electrical, or mounting approval",
    )

def panel_packing_self_check(db: Session) -> PanelPackingSelfCheckRead:
    good = create_project(db, ProjectCreate(title="Panel packing self-check", created_by="panel_packing"))
    upsert_site(db, good.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, source_type="postcode_lookup", source_confidence=0.7))
    add_roof_plane(db, good.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35,
        azimuth_deg=135,
        height_m=6,
        polygon_local_m=[[0, 0], [8, 0], [8, 5], [0, 5]],
        source_type="manual",
        source_confidence=0.65,
    ))
    preview = run_panel_packing(db, good.project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    axis = run_panel_packing(db, good.project_id, PanelPackingRequest(allow_dev_fallback_panels=True, packing_alignment=PanelPackingAlignment.axis_aligned))
    fewer = run_panel_packing(db, good.project_id, PanelPackingRequest(allow_dev_fallback_panels=True, score_goal=PanelPackingScoreGoal.fewer_panels))
    aesthetic = run_panel_packing(db, good.project_id, PanelPackingRequest(allow_dev_fallback_panels=True, score_goal=PanelPackingScoreGoal.aesthetic))
    final_blocked = run_panel_packing(db, good.project_id, PanelPackingRequest(allow_dev_fallback_panels=True, design_mode="final"))
    override_candidate_id = None
    for candidate in preview.candidate_summaries:
        if candidate.get("candidate_id") not in preview.selected_candidate_ids and candidate.get("panel_count", 0) > 0:
            override_candidate_id = candidate.get("candidate_id")
            break
    override_result = run_panel_packing(db, good.project_id, PanelPackingRequest(
        allow_dev_fallback_panels=True,
        selected_candidate_override_id=override_candidate_id or preview.selected_candidate_ids[0],
        override_reason="self-check deliberate candidate override evidence",
    ))
    impossible_override = run_panel_packing(db, good.project_id, PanelPackingRequest(
        allow_dev_fallback_panels=True,
        selected_candidate_override_id="cand_not_generated",
        override_reason="self-check impossible override should block",
    ))

    bad = create_project(db, ProjectCreate(title="Panel packing self-check bad polygon", created_by="panel_packing"))
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
    bad_result = run_panel_packing(db, bad.project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    roof_aligned_differs = bool(preview.placements and axis.placements and preview.placements[0].rotation_deg != axis.placements[0].rotation_deg)
    score_changes = preview.selected_candidate_ids != fewer.selected_candidate_ids or preview.summary.get("panel_count") != fewer.summary.get("panel_count")
    mixed_generated = any(c.get("layout_style") == "mixed_portrait_landscape" for c in preview.candidate_summaries)
    aesthetic_score_present = any("aesthetic_score" in c and "goal_scores" in c for c in aesthetic.candidate_summaries)
    manual_override_recorded = bool(override_result.summary.get("manual_override_record"))
    impossible_override_blocked = impossible_override.status == "blocked" and any(i.code == "CANDIDATE_OVERRIDE_NOT_FOUND" for i in impossible_override.validation_report.issues)
    ok = bool(preview.placements) and final_blocked.status == "blocked" and bad_result.status == "blocked" and roof_aligned_differs and mixed_generated and manual_override_recorded and impossible_override_blocked and aesthetic_score_present
    return PanelPackingSelfCheckRead(
        status="ok" if ok else "failed",
        project_id=good.project_id,
        preview_panels_fit=len(preview.placements) > 0,
        final_blocks_dev_fallback=final_blocked.status == "blocked",
        invalid_geometry_blocked=bad_result.status == "blocked",
        roof_aligned_candidate_differs=roof_aligned_differs,
        score_mode_changes_selection=score_changes,
        mixed_candidate_generated=mixed_generated,
        manual_override_recorded=manual_override_recorded,
        impossible_override_blocked=impossible_override_blocked,
        aesthetic_score_present=aesthetic_score_present,
        calculation_run_id=preview.calculation_run_id,
        output_hash_sha256=preview.output_hash_sha256,
    )


def _override_read(row: PanelPackingOverrideRecord) -> PanelPackingOverrideRead:
    return PanelPackingOverrideRead(
        override_id=row.override_id,
        project_id=row.project_id,
        calculation_run_id=row.calculation_run_id,
        selected_candidate_id=row.selected_candidate_id,
        selected_candidate_hash_sha256=row.selected_candidate_hash_sha256,
        selected_layout_export_hash_sha256=row.selected_layout_export_hash_sha256,
        intended_use=row.intended_use,
        reviewer=row.reviewer,
        reviewer_role=row.reviewer_role,
        override_reason=row.override_reason,
        override_payload=row.override_payload or {},
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


def _selected_candidate_summary(output: dict, candidate_id: str) -> dict | None:
    for candidate in output.get("candidate_summaries") or []:
        if candidate.get("candidate_id") == candidate_id:
            return candidate
    return None


def _row_annotations_from_placements(placements: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, int, str], list[dict]] = {}
    for placement in placements:
        key = (
            str(placement.get("roof_plane_id") or "unknown"),
            int(placement.get("row_index") or 0),
            str(placement.get("orientation") or "unknown"),
        )
        grouped.setdefault(key, []).append(placement)
    rows: list[dict] = []
    for (roof_plane_id, row_index, orientation), items in sorted(grouped.items()):
        centres = [item.get("centre_local_m") or [0, 0] for item in items]
        xs = [float(c[0]) for c in centres if len(c) == 2]
        ys = [float(c[1]) for c in centres if len(c) == 2]
        rows.append({
            "roof_plane_id": roof_plane_id,
            "row_index": row_index,
            "orientation": orientation,
            "panel_count": len(items),
            "centre_line_start_local_m": [round(min(xs), 4), round(sum(ys) / len(ys), 4)] if xs and ys else None,
            "centre_line_end_local_m": [round(max(xs), 4), round(sum(ys) / len(ys), 4)] if xs and ys else None,
            "annotation_status": "generated_from_panel_centres",
        })
    return rows


def build_selected_layout_export_payload(db: Session, project_id: str, calculation_run_id: str) -> SelectedPanelLayoutExportRead:
    row = db.get(CalculationRun, calculation_run_id)
    if row is None or row.project_id != project_id or row.run_type != "panel_packing":
        raise PanelPackingError("Panel packing calculation run was not found for this project.")
    output = row.output_snapshot or {}
    placements = output.get("placements") or []
    selected_candidate_ids = output.get("selected_candidate_ids") or []
    latest_override_row = (
        db.query(PanelPackingOverrideRecord)
        .filter(PanelPackingOverrideRecord.project_id == project_id)
        .filter(PanelPackingOverrideRecord.calculation_run_id == calculation_run_id)
        .order_by(PanelPackingOverrideRecord.created_at.desc(), PanelPackingOverrideRecord.override_id.desc())
        .first()
    )
    row_annotations = _row_annotations_from_placements(placements)
    access_corridors = [
        {
            "corridor_id": "access_corridor_placeholder_001",
            "status": "placeholder_not_calculated",
            "reason": "access and fire margins need NuVision/manufacturer/site-survey rules before final routing",
            "blocks_final_pack": True,
        }
    ]
    downstream_contracts = {
        "yield_input": {
            "requires": ["site.lat_lon", "roof_plane.pitch_azimuth", "panel_placements", "panel_model.power_stc_w"],
            "status": "ready_for_preview_yield_only" if placements else "blocked_no_selected_panels",
        },
        "stringing_input": {
            "requires": ["Q3 voc_v", "Q3 vmp_v", "Q3 isc_a", "Q3 imp_a", "inverter candidate"],
            "status": "blocked_until_electrical_specs_and_inverter_phase",
        },
        "bom_input": {
            "requires": ["selected_panel_layout", "price_snapshot", "stock_snapshot", "mounting_family"],
            "status": "partial_layout_ready_mounting_not_ready",
        },
    }
    payload_for_hash = {
        "project_id": project_id,
        "calculation_run_id": calculation_run_id,
        "source_output_hash_sha256": row.output_hash_sha256,
        "selected_candidate_ids": selected_candidate_ids,
        "placements": placements,
        "row_annotations": row_annotations,
        "access_corridor_placeholders": access_corridors,
        "latest_override_id": latest_override_row.override_id if latest_override_row else None,
        "downstream_contracts": downstream_contracts,
        "truth_boundary": "pre-design layout export only",
    }
    export_hash = stable_json_hash(payload_for_hash)
    return SelectedPanelLayoutExportRead(
        project_id=project_id,
        calculation_run_id=calculation_run_id,
        input_snapshot_hash_sha256=row.input_snapshot_hash_sha256,
        output_hash_sha256=row.output_hash_sha256 or stable_json_hash(output),
        selected_candidate_ids=selected_candidate_ids,
        latest_override=_override_read(latest_override_row) if latest_override_row else None,
        placements=placements,
        panel_placements_geojson=output.get("panel_placements_geojson"),
        row_annotations=row_annotations,
        access_corridor_placeholders=access_corridors,
        downstream_contracts=downstream_contracts,
        selected_layout_export_hash_sha256=export_hash,
    )


def create_panel_packing_override(
    db: Session,
    project_id: str,
    calculation_run_id: str,
    payload: PanelPackingOverrideCreate,
) -> PanelPackingOverrideRead:
    row = db.get(CalculationRun, calculation_run_id)
    if row is None or row.project_id != project_id or row.run_type != "panel_packing":
        raise PanelPackingError("Panel packing calculation run was not found for this project.")
    output = row.output_snapshot or {}
    if output.get("status") == "blocked" or output.get("design_status") == "blocked":
        raise PanelPackingError("Blocked panel-packing runs cannot receive override records.")
    candidate = _selected_candidate_summary(output, payload.selected_candidate_id)
    if candidate is None:
        raise PanelPackingError("Override candidate was not generated by this panel-packing run.")
    if payload.selected_candidate_id not in (output.get("selected_candidate_ids") or []):
        raise PanelPackingError("Persistent overrides must reference the selected candidate from this calculation run. Rerun packing with the chosen candidate override first.")
    reason_codes = set(candidate.get("reason_codes") or [])
    if payload.intended_use == "final":
        if output.get("design_status") == "preview_only" or "PREVIEW_ONLY_DEV_FALLBACK" in reason_codes:
            raise PanelPackingError("Final-use override cannot be created from preview/dev-fallback candidate evidence.")
        if output.get("pre_design_only") is True:
            # This project stage is still pre-design, but a final-mode product/layout selection may be recorded as a draft only.
            # Keep the wording strict in the payload rather than silently upgrading status.
            pass
    export = build_selected_layout_export_payload(db, project_id, calculation_run_id)
    candidate_hash = stable_json_hash(candidate)
    override_payload = {
        "candidate_summary": candidate,
        "source_calculation_run": {
            "run_id": row.run_id,
            "input_snapshot_hash_sha256": row.input_snapshot_hash_sha256,
            "output_hash_sha256": row.output_hash_sha256,
            "engine_version": row.engine_version,
        },
        "selected_layout_export_hash_sha256": export.selected_layout_export_hash_sha256,
        "intended_use": payload.intended_use.value if hasattr(payload.intended_use, "value") else str(payload.intended_use),
        "truth_boundary": "override records human selection only; it cannot bypass Q3/final-mode/structural/electrical gates",
    }
    record = PanelPackingOverrideRecord(
        override_id=_id("ppo"),
        project_id=project_id,
        calculation_run_id=calculation_run_id,
        selected_candidate_id=payload.selected_candidate_id,
        selected_candidate_hash_sha256=candidate_hash,
        selected_layout_export_hash_sha256=export.selected_layout_export_hash_sha256,
        intended_use=payload.intended_use.value if hasattr(payload.intended_use, "value") else str(payload.intended_use),
        reviewer=payload.reviewer,
        reviewer_role=payload.reviewer_role,
        override_reason=payload.override_reason,
        override_payload=override_payload,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _override_read(record)


def list_panel_packing_overrides(db: Session, project_id: str) -> PanelPackingOverrideHistoryRead:
    rows = (
        db.query(PanelPackingOverrideRecord)
        .filter(PanelPackingOverrideRecord.project_id == project_id)
        .order_by(PanelPackingOverrideRecord.created_at.asc(), PanelPackingOverrideRecord.override_id.asc())
        .all()
    )
    return PanelPackingOverrideHistoryRead(
        project_id=project_id,
        override_count=len(rows),
        overrides=[_override_read(row) for row in rows],
    )


def panel_layout_edit_contract(project_id: str) -> PanelLayoutEditContractRead:
    return PanelLayoutEditContractRead(
        project_id=project_id,
        allowed_actions=[
            "select_candidate",
            "annotate_access_corridor_placeholder",
            "request_panel_delete_draft",
            "request_panel_move_draft",
            "request_panel_lock_draft",
        ],
        required_fields_by_action={
            "select_candidate": ["calculation_run_id", "candidate_id", "reviewer", "reviewer_role", "reason"],
            "request_panel_delete_draft": ["source_calculation_run_id", "placement_id", "reviewer", "reason"],
            "request_panel_move_draft": ["source_calculation_run_id", "placement_id", "new_centre_local_m", "reviewer", "reason"],
            "annotate_access_corridor_placeholder": ["project_id", "roof_plane_id", "corridor_polygon_local_m", "source", "reason"],
        },
        blockers=[
            "layout edits are not applied in Phase 004E",
            "any future edit must create a new calculation/evidence run",
            "final mode still requires Q3/Q4 reviewed ProductSpec panel models",
            "structural/electrical/Van der Valk approval remains external",
        ],
    )


def panel_packing_governance_self_check(db: Session) -> PanelPackingGovernanceSelfCheckRead:
    project = create_project(db, ProjectCreate(project_id="prj_pack_governance", title="Panel packing governance self-check"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, source_type="postcode_lookup"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35,
        azimuth_deg=160,
        height_m=6.0,
        polygon_local_m=[[0, 0], [10, 0], [10, 6], [0, 6]],
        source_confidence=0.65,
    ))
    preview = run_panel_packing(db, project.project_id, PanelPackingRequest(allow_dev_fallback_panels=True, score_goal=PanelPackingScoreGoal.aesthetic))
    selected_id = preview.selected_candidate_ids[0]
    first = create_panel_packing_override(db, project.project_id, preview.calculation_run_id, PanelPackingOverrideCreate(
        selected_candidate_id=selected_id,
        override_reason="Self-check records the selected preview candidate for governance history.",
        reviewer="governance_self_check",
        reviewer_role="system_test",
        intended_use="preview",
    ))
    first_hash_before = first.selected_candidate_hash_sha256
    create_panel_packing_override(db, project.project_id, preview.calculation_run_id, PanelPackingOverrideCreate(
        selected_candidate_id=selected_id,
        override_reason="Second append-only record proves history is not overwritten.",
        reviewer="governance_self_check_2",
        reviewer_role="system_test",
        intended_use="preview",
    ))
    history = list_panel_packing_overrides(db, project.project_id)
    override_history_immutable = history.override_count == 2 and history.overrides[0].selected_candidate_hash_sha256 == first_hash_before
    export_one = build_selected_layout_export_payload(db, project.project_id, preview.calculation_run_id)
    export_two = build_selected_layout_export_payload(db, project.project_id, preview.calculation_run_id)
    selected_layout_export_hash_stable = export_one.selected_layout_export_hash_sha256 == export_two.selected_layout_export_hash_sha256
    final_override_blocked = False
    try:
        create_panel_packing_override(db, project.project_id, preview.calculation_run_id, PanelPackingOverrideCreate(
            selected_candidate_id=selected_id,
            override_reason="This should fail because preview fallback cannot become final.",
            reviewer="governance_self_check",
            reviewer_role="system_test",
            intended_use="final",
        ))
    except PanelPackingError:
        final_override_blocked = True
    contract = panel_layout_edit_contract(project.project_id)
    ok = override_history_immutable and final_override_blocked and selected_layout_export_hash_stable and bool(contract.allowed_actions)
    return PanelPackingGovernanceSelfCheckRead(
        status="ok" if ok else "failed",
        project_id=project.project_id,
        override_history_immutable=override_history_immutable,
        final_override_blocked_from_preview_fallback=final_override_blocked,
        selected_layout_export_hash_stable=selected_layout_export_hash_stable,
        layout_edit_contract_available=bool(contract.allowed_actions),
        override_count=history.override_count,
        selected_layout_export_hash_sha256=export_one.selected_layout_export_hash_sha256,
    )

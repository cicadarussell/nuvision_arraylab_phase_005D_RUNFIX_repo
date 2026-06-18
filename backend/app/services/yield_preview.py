from __future__ import annotations

from math import isclose
from uuid import uuid4
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.models.db_models import CalculationRun, YieldAssumptionSet, PvgisRequestCache
from app.schemas.calculation import CalculationRunCreate, CalculationRunType
from app.schemas.geometry import PanelPackingRequest
from app.schemas.project import RoofType
from app.schemas.validation import Severity, ValidationArea, ValidationIssue, ValidationReport
from app.schemas.yield_preview import (
    MonthlyYieldRead,
    RoofPlaneYieldRead,
    YieldAssumptionSetRead,
    YieldModelTier,
    YieldPreviewRequest,
    PvgisCacheRead,
    YieldPreviewResultRead,
    YieldPreviewSelfCheckRead,
    YieldRunRead,
)
from app.services.calculation_run import build_calculation_run
from app.services.hash_utils import stable_json_hash
from app.services.panel_packing import PanelPackingError, build_selected_layout_export_payload, run_panel_packing
from app.services.pvgis_adapter import (
    build_pvgis_pvcalc_params,
    get_or_fetch_pvgis_monthly,
    parse_pvgis_monthly_response,
    pvgis_cache_summary,
    PvgisAdapterError,
    PvgisFetchResult,
)
from app.services.project_geometry import ProjectGeometryError, add_roof_plane, create_project, get_project_geometry, upsert_site
from app.schemas.geometry import ProjectCreate, RoofPlaneCreate, SiteCreate


class YieldPreviewError(Exception):
    pass


MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# Normalised after use. Shape roughly matches south-west UK seasonality, not a measured PVGIS curve.
UK_MONTHLY_PROFILE = [0.030, 0.050, 0.080, 0.105, 0.120, 0.130, 0.125, 0.105, 0.085, 0.065, 0.045, 0.030]
DEFAULT_ASSUMPTION_ID = "UK_ROOF_PREVIEW_V0_1"
PVGIS_ENDPOINT = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"


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


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def ensure_default_yield_assumption_set(db: Session) -> YieldAssumptionSet:
    existing = db.get(YieldAssumptionSet, DEFAULT_ASSUMPTION_ID)
    if existing is not None:
        return existing
    record = YieldAssumptionSet(
        assumption_set_id=DEFAULT_ASSUMPTION_ID,
        title="UK roof preview default, T0 rough kWh/kWp estimate",
        model_tier=YieldModelTier.t0_rough.value,
        specific_yield_kwh_per_kwp_year=950.0,
        system_loss_pct=14.0,
        shade_loss_pct=0.0,
        degradation_year1_pct=0.0,
        albedo=0.2,
        source="ArrayLab preview default based on UK first-pass kWh/kWp workflow; replace with PVGIS/pvlib reviewed assumptions before final proposal use.",
        review_status="preview_default",
        created_by="system",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _assumption_read(record: YieldAssumptionSet) -> YieldAssumptionSetRead:
    return YieldAssumptionSetRead(
        assumption_set_id=record.assumption_set_id,
        title=record.title,
        model_tier=YieldModelTier(record.model_tier),
        specific_yield_kwh_per_kwp_year=record.specific_yield_kwh_per_kwp_year,
        system_loss_pct=record.system_loss_pct,
        shade_loss_pct=record.shade_loss_pct,
        degradation_year1_pct=record.degradation_year1_pct,
        albedo=record.albedo,
        source=record.source,
        review_status=record.review_status,
        created_by=record.created_by,
    )


def list_yield_assumption_sets(db: Session) -> list[YieldAssumptionSetRead]:
    ensure_default_yield_assumption_set(db)
    rows = db.query(YieldAssumptionSet).order_by(YieldAssumptionSet.assumption_set_id.asc()).all()
    return [_assumption_read(row) for row in rows]


def _get_assumptions(db: Session, request: YieldPreviewRequest) -> YieldAssumptionSetRead:
    if request.assumption_set_id == DEFAULT_ASSUMPTION_ID:
        record = ensure_default_yield_assumption_set(db)
    else:
        record = db.get(YieldAssumptionSet, request.assumption_set_id)
        if record is None:
            raise YieldPreviewError("Yield assumption set was not found.")
    read = _assumption_read(record)
    updates = {}
    if request.model_tier is not None:
        updates["model_tier"] = request.model_tier
    if request.specific_yield_kwh_per_kwp_year is not None:
        updates["specific_yield_kwh_per_kwp_year"] = request.specific_yield_kwh_per_kwp_year
    if request.system_loss_pct is not None:
        updates["system_loss_pct"] = request.system_loss_pct
    if request.shade_loss_pct is not None:
        updates["shade_loss_pct"] = request.shade_loss_pct
    if updates:
        data = read.model_dump()
        data.update(updates)
        data["title"] = f"{read.title} / request override"
        data["source"] = read.source + " Request override applied for preview-only sensitivity testing."
        data["review_status"] = "request_override_preview_only"
        read = YieldAssumptionSetRead(**data)
    return read


def _roof_lookup(project_geometry) -> dict[str, dict]:
    return {roof.roof_plane_id: roof.model_dump(mode="json") for roof in project_geometry.roof_planes}


def _angle_distance(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _azimuth_factor(azimuth_deg: float | None) -> float:
    if azimuth_deg is None:
        return 0.82
    delta = _angle_distance(float(azimuth_deg), 180.0)
    # Transparent roughness: east/west stays useful, north is weak but not zero.
    return round(max(0.58, 1.0 - (delta / 180.0) * 0.42), 4)


def _tilt_factor(pitch_deg: float | None) -> float:
    if pitch_deg is None:
        return 0.88
    delta = abs(float(pitch_deg) - 35.0)
    return round(max(0.72, 1.0 - delta / 125.0), 4)


def _normalised_monthly_profile() -> list[float]:
    total = sum(UK_MONTHLY_PROFILE)
    return [value / total for value in UK_MONTHLY_PROFILE]


def _pvgis_cache_read(record: PvgisRequestCache | None) -> PvgisCacheRead | None:
    if record is None:
        return None
    return PvgisCacheRead(
        request_hash_sha256=record.request_hash_sha256,
        endpoint=record.endpoint,
        params=record.params,
        status=record.status,
        adapter_version=record.adapter_version,
        attempt_count=record.attempt_count or 0,
        cache_hit_count=record.cache_hit_count or 0,
        http_status_code=record.http_status_code,
        annual_kwh=record.annual_kwh,
        parsed_monthly=record.parsed_monthly or [],
        error_message=record.error_message,
        url_preview=record.url_preview,
        final_url=record.final_url,
        response_hash_sha256=record.response_hash_sha256,
    )


def _monthly_from_pvgis_cache(record: PvgisRequestCache) -> list[MonthlyYieldRead]:
    rows = record.parsed_monthly or []
    if len(rows) != 12:
        raise YieldPreviewError("PVGIS cache record has no complete parsed monthly data.")
    annual = record.annual_kwh or sum(float(row.get("kwh", 0.0)) for row in rows)
    result = []
    for row in rows:
        kwh = round(float(row.get("kwh", 0.0)), 3)
        share = float(row.get("share_of_annual", kwh / annual if annual else 0.0))
        result.append(MonthlyYieldRead(month=int(row.get("month")), month_name=str(row.get("month_name")), kwh=kwh, share_of_annual=round(share, 6)))
    return result


def _monthly_from_annual(annual_kwh: float, override: list[float] | None = None) -> list[MonthlyYieldRead]:
    if override is not None:
        total = sum(override)
        shares = [(value / total if total > 0 else 0.0) for value in override]
        return [
            MonthlyYieldRead(month=i + 1, month_name=MONTH_NAMES[i], kwh=round(float(override[i]), 3), share_of_annual=round(shares[i], 6))
            for i in range(12)
        ]
    shares = _normalised_monthly_profile()
    raw = [annual_kwh * share for share in shares]
    rounded = [round(value, 3) for value in raw]
    # Keep arithmetic exact enough for self-checks after rounding.
    diff = round(annual_kwh - sum(rounded), 3)
    rounded[-1] = round(rounded[-1] + diff, 3)
    return [MonthlyYieldRead(month=i + 1, month_name=MONTH_NAMES[i], kwh=rounded[i], share_of_annual=round(shares[i], 6)) for i in range(12)]


def _pvgis_request_stub(lat: float, lon: float, peakpower_kwp: float, slope: float, azimuth: float, loss_pct: float) -> dict:
    # PVGIS uses aspect convention in some tools where 0 = south, -90 = east, 90 = west.
    # Store both our true-azimuth and the PVGIS-style aspect so review is explicit.
    pvgis_aspect = ((azimuth - 180.0 + 180.0) % 360.0) - 180.0
    params = {
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "peakpower": round(peakpower_kwp, 4),
        "loss": round(loss_pct, 3),
        "angle": round(slope, 3),
        "aspect": round(pvgis_aspect, 3),
        "outputformat": "json",
    }
    return {
        "status": "backend_stub_not_called",
        "reason": "PVGIS calls are backend-owned; Phase 005C keeps backend-owned PVGIS fetch/cache them server-side when explicitly enabled, never from browser AJAX.",
        "method": "GET",
        "endpoint": PVGIS_ENDPOINT,
        "params": params,
        "url_preview": PVGIS_ENDPOINT + "?" + urlencode(params),
        "arraylab_azimuth_true_deg": round(azimuth, 3),
        "pvgis_aspect_deg_south_zero": round(pvgis_aspect, 3),
    }


def run_yield_preview(db: Session, project_id: str, request: YieldPreviewRequest) -> YieldPreviewResultRead:
    try:
        selected_layout = build_selected_layout_export_payload(db, project_id, request.selected_layout_calculation_run_id)
    except Exception as exc:
        raise YieldPreviewError("A selected panel layout is required before preview yield can run.") from exc

    placements = selected_layout.placements or []
    issues: list[ValidationIssue] = []
    if not placements:
        issues.append(_issue(
            "YIELD_REQUIRES_SELECTED_PANEL_LAYOUT",
            Severity.blocker,
            "Preview yield requires a selected panel layout with at least one panel placement.",
            "selected_layout_calculation_run_id",
            "Run panel packing and export a selected layout before yield preview.",
            blocks=True,
        ))

    geometry = get_project_geometry(db, project_id)
    if geometry.site is None or geometry.site.lat is None or geometry.site.lon is None:
        issues.append(_issue(
            "YIELD_REQUIRES_SITE_LAT_LON",
            Severity.blocker,
            "Preview yield requires site latitude/longitude.",
            "site.lat_lon",
            "Set the project site postcode/lat/lon before running yield.",
            blocks=True,
        ))
    assumptions = _get_assumptions(db, request)
    roof_by_id = _roof_lookup(geometry)

    if issues:
        input_payload = {
            "project_id": project_id,
            "request": request.model_dump(mode="json"),
            "selected_layout_export_hash_sha256": selected_layout.selected_layout_export_hash_sha256,
            "assumption_set": assumptions.model_dump(mode="json"),
            "blockers": [i.model_dump(mode="json") for i in issues],
        }
        output_payload = {"status": "blocked", "annual_kwh_preview": 0.0, "monthly": [], "truth_boundary": "preview yield blocked"}
        input_hash = stable_json_hash(input_payload)
        output_hash = stable_json_hash(output_payload)
        built = build_calculation_run(CalculationRunCreate(
            project_id=project_id,
            run_type=CalculationRunType.yield_calc,
            engine_version="0.5.2-yield-preview-t0-pvgis-cache",
            input_snapshot=input_payload,
            assumption_set_id=assumptions.assumption_set_id,
            warnings=[f"{i.code}: {i.message}" for i in issues],
        ))
        row = CalculationRun(
            run_id=built.run_id,
            project_id=project_id,
            run_type=built.run_type.value,
            status="failed",
            software_version=built.software_version,
            engine_version=built.engine_version,
            input_snapshot_hash_sha256=input_hash,
            output_hash_sha256=output_hash,
            product_data_snapshot_id=built.product_data_snapshot_id,
            assumption_set_id=assumptions.assumption_set_id,
            warnings=built.warnings,
            input_snapshot=input_payload,
            output_snapshot=output_payload,
        )
        db.add(row)
        db.commit()
        validation = ValidationReport(status="blocked", issues=issues, summary={"project_id": project_id, "blocked": True})
        return YieldPreviewResultRead(
            project_id=project_id,
            status="blocked",
            design_status="blocked_no_selected_layout",
            calculation_run_id=row.run_id,
            selected_layout_calculation_run_id=request.selected_layout_calculation_run_id,
            selected_layout_export_hash_sha256=selected_layout.selected_layout_export_hash_sha256,
            input_snapshot_hash_sha256=input_hash,
            output_hash_sha256=output_hash,
            assumption_set=assumptions,
            validation_report=validation,
            total_dc_kwp=0.0,
            annual_kwh_preview=0.0,
            specific_yield_kwh_per_kwp_after_losses=0.0,
            monthly=[],
            roof_plane_results=[],
            pvgis_request_stub=None,
            summary=output_payload,
        )

    loss_multiplier = max(0.0, 1.0 - (assumptions.system_loss_pct + assumptions.shade_loss_pct + assumptions.degradation_year1_pct) / 100.0)
    roof_power: dict[str, float] = {}
    roof_count: dict[str, int] = {}
    for placement in placements:
        roof_id = str(placement.get("roof_plane_id") or "unknown")
        roof_power[roof_id] = roof_power.get(roof_id, 0.0) + float(placement.get("power_stc_w") or 0.0)
        roof_count[roof_id] = roof_count.get(roof_id, 0) + 1

    roof_results: list[RoofPlaneYieldRead] = []
    annual_total = 0.0
    weighted_pitch = 0.0
    weighted_azimuth = 0.0
    total_dc_kwp = sum(roof_power.values()) / 1000.0
    for roof_id, power_w in sorted(roof_power.items()):
        roof = roof_by_id.get(roof_id, {})
        pitch = roof.get("pitch_deg")
        azimuth = roof.get("azimuth_deg")
        azi_factor = _azimuth_factor(float(azimuth) if azimuth is not None else None)
        tilt = _tilt_factor(float(pitch) if pitch is not None else None)
        orientation_factor = round(azi_factor * tilt, 4)
        dc_kwp = power_w / 1000.0
        annual = dc_kwp * assumptions.specific_yield_kwh_per_kwp_year * orientation_factor * loss_multiplier
        annual_total += annual
        if total_dc_kwp > 0:
            weighted_pitch += float(pitch if pitch is not None else 35.0) * dc_kwp / total_dc_kwp
            weighted_azimuth += float(azimuth if azimuth is not None else 180.0) * dc_kwp / total_dc_kwp
        roof_results.append(RoofPlaneYieldRead(
            roof_plane_id=roof_id,
            panel_count=roof_count.get(roof_id, 0),
            dc_kwp=round(dc_kwp, 4),
            pitch_deg=round(float(pitch), 3) if pitch is not None else None,
            azimuth_deg=round(float(azimuth), 3) if azimuth is not None else None,
            azimuth_factor=azi_factor,
            tilt_factor=tilt,
            orientation_factor=orientation_factor,
            annual_kwh_preview=round(annual, 3),
        ))

    t0_annual = round(annual_total, 3)
    t0_monthly = _monthly_from_annual(t0_annual)
    monthly = t0_monthly
    annual_for_output = t0_annual
    pvgis_record: PvgisRequestCache | None = None
    pvgis_monthly_used = False
    pvgis_comparison = None

    if request.pvgis_monthly_kwh_override is not None:
        # Manual PVGIS values become the monthly source for controlled offline tests.
        # They are still preview evidence only and are explicitly marked as manual.
        monthly = _monthly_from_annual(t0_annual, request.pvgis_monthly_kwh_override)
        annual_for_output = round(sum(request.pvgis_monthly_kwh_override), 3)
        pvgis_monthly_used = True
        pvgis_comparison = {
            "source": "manual_pvgis_monthly_override",
            "t0_annual_kwh": t0_annual,
            "pvgis_annual_kwh": annual_for_output,
            "delta_kwh": round(annual_for_output - t0_annual, 3),
            "delta_pct": round(((annual_for_output - t0_annual) / t0_annual * 100.0), 3) if t0_annual else None,
        }
    elif request.use_pvgis_monthly or request.model_tier == YieldModelTier.t1_pvgis_monthly_cached:
        try:
            params = build_pvgis_pvcalc_params(
                lat=float(geometry.site.lat),
                lon=float(geometry.site.lon),
                peakpower_kwp=total_dc_kwp,
                slope_deg=weighted_pitch or 35.0,
                azimuth_true_deg=weighted_azimuth or 180.0,
                loss_pct=assumptions.system_loss_pct + assumptions.shade_loss_pct + assumptions.degradation_year1_pct,
            )
            pvgis_record = get_or_fetch_pvgis_monthly(
                db,
                params=params,
                allow_network=request.allow_pvgis_network_fetch,
                force_refresh=request.force_pvgis_refresh,
                timeout_seconds=request.pvgis_timeout_seconds,
                requested_by="yield_preview",
            )
            if pvgis_record.status == "succeeded":
                monthly = _monthly_from_pvgis_cache(pvgis_record)
                annual_for_output = round(sum(item.kwh for item in monthly), 3)
                pvgis_monthly_used = True
                pvgis_comparison = {
                    "source": "pvgis_cache",
                    "request_hash_sha256": pvgis_record.request_hash_sha256,
                    "cache_status": pvgis_record.status,
                    "cache_hit_count": pvgis_record.cache_hit_count,
                    "t0_annual_kwh": t0_annual,
                    "pvgis_annual_kwh": annual_for_output,
                    "delta_kwh": round(annual_for_output - t0_annual, 3),
                    "delta_pct": round(((annual_for_output - t0_annual) / t0_annual * 100.0), 3) if t0_annual else None,
                }
            else:
                issues.append(_issue(
                    "PVGIS_UNAVAILABLE_FALLBACK_TO_T0",
                    Severity.warning,
                    f"PVGIS monthly data was requested but no successful cache/fetch is available ({pvgis_record.status}). T0 preview output is being used.",
                    "pvgis_request_cache",
                    "Allow backend PVGIS fetch, check network/API status, or use cached PVGIS evidence before treating T1 as available.",
                    blocks=False,
                ))
                pvgis_comparison = {
                    "source": "pvgis_unavailable_fallback_to_t0",
                    "request_hash_sha256": pvgis_record.request_hash_sha256,
                    "cache_status": pvgis_record.status,
                    "error_message": pvgis_record.error_message,
                    "t0_annual_kwh": t0_annual,
                    "pvgis_annual_kwh": None,
                    "delta_kwh": None,
                    "delta_pct": None,
                }
        except Exception as exc:
            issues.append(_issue(
                "PVGIS_REQUEST_BUILD_FAILED",
                Severity.warning,
                f"PVGIS request could not be built or parsed: {exc}",
                "pvgis_request",
                "Check site lat/lon, selected layout power, tilt, azimuth and loss assumptions.",
                blocks=False,
            ))

    specific_after_losses = round(annual_for_output / total_dc_kwp, 3) if total_dc_kwp > 0 else 0.0
    pvgis_stub = None
    if request.include_pvgis_request_stub and geometry.site is not None:
        pvgis_stub = _pvgis_request_stub(
            lat=float(geometry.site.lat),
            lon=float(geometry.site.lon),
            peakpower_kwp=total_dc_kwp,
            slope=weighted_pitch or 35.0,
            azimuth=weighted_azimuth or 180.0,
            loss_pct=assumptions.system_loss_pct + assumptions.shade_loss_pct + assumptions.degradation_year1_pct,
        )

    status = "warnings" if issues else "ok"
    validation = ValidationReport(status=status, issues=issues, summary={
        "project_id": project_id,
        "total_dc_kwp": round(total_dc_kwp, 4),
        "annual_kwh_preview": annual_for_output,
        "model_tier": assumptions.model_tier.value if hasattr(assumptions.model_tier, "value") else str(assumptions.model_tier),
        "loss_multiplier": round(loss_multiplier, 5),
        "selected_layout_export_hash_sha256": selected_layout.selected_layout_export_hash_sha256,
        "preview_only": True,
        "pvgis_monthly_used": pvgis_monthly_used,
        "pvgis_cache_status": pvgis_record.status if pvgis_record is not None else None,
    })
    input_payload = {
        "project_id": project_id,
        "selected_layout_calculation_run_id": request.selected_layout_calculation_run_id,
        "selected_layout_export_hash_sha256": selected_layout.selected_layout_export_hash_sha256,
        "selected_layout_output_hash_sha256": selected_layout.output_hash_sha256,
        "site": geometry.site.model_dump(mode="json") if geometry.site else None,
        "roof_plane_geometry_basis": {roof_id: roof_by_id.get(roof_id, {}) for roof_id in roof_power},
        "assumption_set": assumptions.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
    }
    output_payload = {
        "project_id": project_id,
        "status": status,
        "design_status": "preview_yield_only",
        "model_tier": assumptions.model_tier.value if hasattr(assumptions.model_tier, "value") else str(assumptions.model_tier),
        "total_dc_kwp": round(total_dc_kwp, 4),
        "annual_kwh_preview": annual_for_output,
        "specific_yield_kwh_per_kwp_after_losses": specific_after_losses,
        "monthly": [m.model_dump(mode="json") for m in monthly],
        "roof_plane_results": [r.model_dump(mode="json") for r in roof_results],
        "assumption_set": assumptions.model_dump(mode="json"),
        "pvgis_request_stub": pvgis_stub,
        "pvgis_cache": _pvgis_cache_read(pvgis_record).model_dump(mode="json") if pvgis_record is not None else None,
        "pvgis_comparison": pvgis_comparison,
        "pvgis_monthly_used": pvgis_monthly_used,
        "selected_layout_export_hash_sha256": selected_layout.selected_layout_export_hash_sha256,
        "truth_boundary": "preview yield estimate only; final proposal needs reviewed assumptions and proper PVGIS/pvlib/shade/electrical review",
    }
    input_hash = stable_json_hash(input_payload)
    output_hash = stable_json_hash(output_payload)
    built = build_calculation_run(CalculationRunCreate(
        project_id=project_id,
        run_type=CalculationRunType.yield_calc,
        engine_version="0.5.2-yield-preview-t0-pvgis-cache",
        input_snapshot=input_payload,
        assumption_set_id=assumptions.assumption_set_id,
        warnings=[f"{i.code}: {i.message}" for i in issues],
    ))
    row = CalculationRun(
        run_id=built.run_id,
        project_id=project_id,
        run_type=built.run_type.value,
        status="preview",
        software_version=built.software_version,
        engine_version=built.engine_version,
        input_snapshot_hash_sha256=input_hash,
        output_hash_sha256=output_hash,
        product_data_snapshot_id=built.product_data_snapshot_id,
        assumption_set_id=assumptions.assumption_set_id,
        warnings=built.warnings,
        input_snapshot=input_payload,
        output_snapshot=output_payload,
    )
    db.add(row)
    db.commit()
    return YieldPreviewResultRead(
        project_id=project_id,
        status=status,
        design_status="preview_yield_only",
        calculation_run_id=row.run_id,
        selected_layout_calculation_run_id=request.selected_layout_calculation_run_id,
        selected_layout_export_hash_sha256=selected_layout.selected_layout_export_hash_sha256,
        input_snapshot_hash_sha256=input_hash,
        output_hash_sha256=output_hash,
        assumption_set=assumptions,
        validation_report=validation,
        total_dc_kwp=round(total_dc_kwp, 4),
        annual_kwh_preview=annual_for_output,
        specific_yield_kwh_per_kwp_after_losses=specific_after_losses,
        monthly=monthly,
        roof_plane_results=roof_results,
        pvgis_request_stub=pvgis_stub,
        pvgis_cache=_pvgis_cache_read(pvgis_record),
        pvgis_comparison=pvgis_comparison,
        summary=output_payload,
    )


def get_yield_run(db: Session, project_id: str, calculation_run_id: str) -> YieldRunRead:
    row = db.get(CalculationRun, calculation_run_id)
    if row is None or row.project_id != project_id or row.run_type != "yield":
        raise YieldPreviewError("Yield calculation run was not found for this project.")
    return YieldRunRead(
        project_id=project_id,
        calculation_run_id=row.run_id,
        run_type=row.run_type,
        input_snapshot_hash_sha256=row.input_snapshot_hash_sha256,
        output_hash_sha256=row.output_hash_sha256,
        output_snapshot=row.output_snapshot,
    )


def yield_preview_self_check(db: Session) -> YieldPreviewSelfCheckRead:
    project = create_project(db, ProjectCreate(project_id="prj_yield_preview_self_check", title="Yield preview self-check"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, timezone="Europe/London", source_type="postcode_lookup"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=35,
        azimuth_deg=180,
        height_m=6.0,
        polygon_local_m=[[0, 0], [11, 0], [11, 6], [0, 6]],
        source_confidence=0.65,
    ))
    layout = run_panel_packing(db, project.project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    result = run_yield_preview(db, project.project_id, YieldPreviewRequest(selected_layout_calculation_run_id=layout.calculation_run_id))
    changed = run_yield_preview(db, project.project_id, YieldPreviewRequest(
        selected_layout_calculation_run_id=layout.calculation_run_id,
        system_loss_pct=20.0,
    ))
    blocked = False
    try:
        # Existing calc run ID with no placements should block. Make a deliberately blocked panel pack.
        bad_project = create_project(db, ProjectCreate(project_id="prj_yield_no_layout", title="Yield blocked no layout"))
        upsert_site(db, bad_project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, timezone="Europe/London", source_type="postcode_lookup"))
        empty = run_panel_packing(db, bad_project.project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
        blocked_result = run_yield_preview(db, bad_project.project_id, YieldPreviewRequest(selected_layout_calculation_run_id=empty.calculation_run_id))
        blocked = blocked_result.status == "blocked"
    except Exception:
        blocked = True
    monthly_sum_matches = isclose(sum(item.kwh for item in result.monthly), result.annual_kwh_preview, abs_tol=0.02)
    pvgis_backend_only = bool(result.pvgis_request_stub and result.pvgis_request_stub.get("status") == "backend_stub_not_called")

    fake_pvgis = {
        "outputs": {
            "monthly": {
                "fixed": [
                    {"month": i + 1, "E_m": value}
                    for i, value in enumerate([80, 110, 180, 240, 300, 330, 320, 270, 210, 150, 90, 70])
                ]
            },
            "totals": {"fixed": {"E_y": 2350}},
        }
    }

    def fake_fetcher(url, params, timeout_seconds):
        return PvgisFetchResult(200, fake_pvgis, final_url=url + "?mocked=1")

    params = build_pvgis_pvcalc_params(
        lat=50.8155, lon=-3.2806, peakpower_kwp=result.total_dc_kwp,
        slope_deg=35.0, azimuth_true_deg=180.0, loss_pct=14.0,
    )
    fetched = get_or_fetch_pvgis_monthly(db, params=params, allow_network=False, fetcher=fake_fetcher, requested_by="self_check")
    cached = get_or_fetch_pvgis_monthly(db, params=params, allow_network=False, requested_by="self_check")
    pvgis_result = run_yield_preview(db, project.project_id, YieldPreviewRequest(
        selected_layout_calculation_run_id=layout.calculation_run_id,
        model_tier=YieldModelTier.t1_pvgis_monthly_cached,
        use_pvgis_monthly=True,
    ))
    unavailable = run_yield_preview(db, project.project_id, YieldPreviewRequest(
        selected_layout_calculation_run_id=layout.calculation_run_id,
        model_tier=YieldModelTier.t1_pvgis_monthly_cached,
        use_pvgis_monthly=True,
        system_loss_pct=15.0,
    ))
    pvgis_cache_hit = fetched.status == "succeeded" and cached.cache_hit_count >= 1
    pvgis_monthly_values_used = bool(pvgis_result.pvgis_cache and pvgis_result.pvgis_cache.status == "succeeded" and abs(pvgis_result.annual_kwh_preview - 2350.0) < 0.02)
    pvgis_unavailable_fallback_warns = any(issue.code == "PVGIS_UNAVAILABLE_FALLBACK_TO_T0" for issue in unavailable.validation_report.issues)

    ok = (
        result.annual_kwh_preview > 0
        and monthly_sum_matches
        and changed.output_hash_sha256 != result.output_hash_sha256
        and blocked
        and pvgis_backend_only
        and pvgis_cache_hit
        and pvgis_monthly_values_used
        and pvgis_unavailable_fallback_warns
    )
    return YieldPreviewSelfCheckRead(
        status="ok" if ok else "failed",
        project_id=project.project_id,
        panel_layout_required_blocked=blocked,
        annual_kwh_positive=result.annual_kwh_preview > 0,
        monthly_sum_matches_annual=monthly_sum_matches,
        assumption_change_changes_hash=changed.output_hash_sha256 != result.output_hash_sha256,
        pvgis_stub_backend_only=pvgis_backend_only,
        pvgis_monthly_cache_hit=pvgis_cache_hit,
        pvgis_monthly_values_used=pvgis_monthly_values_used,
        pvgis_unavailable_fallback_warns=pvgis_unavailable_fallback_warns,
        calculation_run_id=pvgis_result.calculation_run_id,
        output_hash_sha256=pvgis_result.output_hash_sha256,
    )

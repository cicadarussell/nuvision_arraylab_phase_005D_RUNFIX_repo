from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import acos, asin, atan2, cos, degrees, isfinite, pi, radians, sin, tan
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.db_models import CalculationRun
from app.schemas.calculation import CalculationRunCreate, CalculationRunType
from app.schemas.geometry import PanelPackingRequest
from app.schemas.geometry import ProjectCreate, RoofPlaneCreate, SiteCreate
from app.schemas.project import RoofType
from app.schemas.solar_geometry import (
    RoofPlaneSolarGeometryRead,
    SolarGeometryDebugRead,
    SolarGeometryDebugRequest,
    SolarGeometrySelfCheckRead,
    SolarPositionSampleRead,
)
from app.schemas.validation import Severity, ValidationArea, ValidationIssue, ValidationReport
from app.services.calculation_run import build_calculation_run
from app.services.hash_utils import stable_json_hash
from app.services.panel_packing import build_selected_layout_export_payload, run_panel_packing
from app.services.project_geometry import add_roof_plane, create_project, get_project_geometry, upsert_site

try:  # Optional dependency. Phase 005C is pvlib-ready but tests must work without network installs.
    import pandas as _pd  # type: ignore
    import pvlib as _pvlib  # type: ignore
except Exception:  # pragma: no cover - exercised implicitly when pvlib is absent
    _pd = None
    _pvlib = None


class SolarGeometryError(Exception):
    pass


@dataclass(frozen=True)
class _SolarPos:
    elevation_deg: float
    azimuth_deg: float
    zenith_deg: float
    declination_deg: float | None
    source_engine: str


def pvlib_available() -> bool:
    return _pvlib is not None and _pd is not None


def _source_engine() -> str:
    return "pvlib.solarposition" if pvlib_available() else "arraylab_noaa_lite_fallback"


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


def _project_tz(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "Europe/London")
    except Exception:
        return ZoneInfo("Europe/London")


def _sample_dates(year: int, mode: str) -> list[tuple[int, int]]:
    if mode == "seasonal_key_days":
        return [(3, 21), (6, 21), (9, 21), (12, 21)]
    return [(month, 21) for month in range(1, 13)]


def _normalise_deg(value: float) -> float:
    return value % 360.0


def _solar_position_noaa_lite(lat: float, lon: float, when_local: datetime) -> _SolarPos:
    """Small deterministic solar-position approximation for debug/contract tests.

    This is not sold as engineering-grade pvlib. It gives sane elevation/azimuth samples
    and lets ArrayLab verify conventions and hashes even when pvlib is not installed. The
    proper route is to install the optional pvlib dependency and compare/upgrade later.
    """

    tz_offset_hours = (when_local.utcoffset().total_seconds() / 3600.0) if when_local.utcoffset() else 0.0
    n = when_local.timetuple().tm_yday
    hour_decimal = when_local.hour + when_local.minute / 60.0 + when_local.second / 3600.0
    gamma = 2.0 * pi / 365.0 * (n - 1 + (hour_decimal - 12.0) / 24.0)
    eqtime_min = 229.18 * (
        0.000075
        + 0.001868 * cos(gamma)
        - 0.032077 * sin(gamma)
        - 0.014615 * cos(2.0 * gamma)
        - 0.040849 * sin(2.0 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * cos(gamma)
        + 0.070257 * sin(gamma)
        - 0.006758 * cos(2.0 * gamma)
        + 0.000907 * sin(2.0 * gamma)
        - 0.002697 * cos(3.0 * gamma)
        + 0.00148 * sin(3.0 * gamma)
    )
    time_offset = eqtime_min + 4.0 * lon - 60.0 * tz_offset_hours
    true_solar_time = (hour_decimal * 60.0 + time_offset) % 1440.0
    hour_angle_deg = true_solar_time / 4.0 - 180.0
    if hour_angle_deg < -180.0:
        hour_angle_deg += 360.0
    ha = radians(hour_angle_deg)
    lat_rad = radians(lat)
    cos_zenith = sin(lat_rad) * sin(decl) + cos(lat_rad) * cos(decl) * cos(ha)
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = acos(cos_zenith)
    elevation = 90.0 - degrees(zenith)
    azimuth = degrees(atan2(sin(ha), cos(ha) * sin(lat_rad) - tan(decl) * cos(lat_rad))) + 180.0
    return _SolarPos(
        elevation_deg=round(elevation, 4),
        azimuth_deg=round(_normalise_deg(azimuth), 4),
        zenith_deg=round(degrees(zenith), 4),
        declination_deg=round(degrees(decl), 4),
        source_engine="arraylab_noaa_lite_fallback",
    )


def _solar_position(lat: float, lon: float, when_local: datetime) -> _SolarPos:
    if pvlib_available():  # pragma: no cover - depends on optional local install
        series = _pd.DatetimeIndex([when_local])
        pos = _pvlib.solarposition.get_solarposition(series, latitude=lat, longitude=lon).iloc[0]
        return _SolarPos(
            elevation_deg=round(float(pos["apparent_elevation"]), 4),
            azimuth_deg=round(float(pos["azimuth"]), 4),
            zenith_deg=round(float(pos["apparent_zenith"]), 4),
            declination_deg=None,
            source_engine="pvlib.solarposition",
        )
    return _solar_position_noaa_lite(lat, lon, when_local)


def _surface_cosines(solar: _SolarPos, pitch_deg: float, azimuth_deg: float) -> tuple[float, float, float, float]:
    zen = radians(solar.zenith_deg)
    tilt = radians(float(pitch_deg))
    delta_az = radians(float(solar.azimuth_deg) - float(azimuth_deg))
    cos_incidence = cos(zen) * cos(tilt) + sin(zen) * sin(tilt) * cos(delta_az)
    cos_incidence = max(-1.0, min(1.0, cos_incidence))
    horizontal = max(0.0, cos(zen))
    poa_cos = max(0.0, cos_incidence)
    factor = poa_cos / horizontal if horizontal > 1e-6 else 0.0
    incidence_angle = degrees(acos(max(-1.0, min(1.0, cos_incidence))))
    return round(poa_cos, 6), round(horizontal, 6), round(factor, 6), round(incidence_angle, 4)


def _selected_layout_hints(db: Session, project_id: str, calculation_run_id: str | None) -> dict[str, dict]:
    if not calculation_run_id:
        return {}
    try:
        selected = build_selected_layout_export_payload(db, project_id, calculation_run_id)
    except Exception:
        return {}
    hints: dict[str, dict] = {}
    for placement in selected.placements or []:
        roof_id = placement.get("roof_plane_id") or "unknown"
        panel_wp = float(placement.get("power_stc_w") or 0.0)
        hint = hints.setdefault(roof_id, {"panel_count": 0, "dc_kwp": 0.0})
        hint["panel_count"] += 1
        hint["dc_kwp"] += panel_wp / 1000.0
    for hint in hints.values():
        hint["dc_kwp"] = round(hint["dc_kwp"], 4)
    return hints


def build_solar_geometry_debug(db: Session, project_id: str, request: SolarGeometryDebugRequest | None = None) -> SolarGeometryDebugRead:
    request = request or SolarGeometryDebugRequest()
    geometry = get_project_geometry(db, project_id)
    issues: list[ValidationIssue] = []
    if geometry.site is None or geometry.site.lat is None or geometry.site.lon is None:
        issues.append(_issue(
            "SOLAR_GEOMETRY_REQUIRES_SITE_COORDINATES",
            Severity.blocker,
            "Solar-position debug requires site latitude and longitude.",
            "site.lat/site.lon",
            "Add site coordinates before solar geometry or yield prep.",
            blocks=True,
        ))
    if not geometry.roof_planes:
        issues.append(_issue(
            "SOLAR_GEOMETRY_REQUIRES_ROOF_PLANES",
            Severity.blocker,
            "Solar-position debug requires at least one roof plane.",
            "roof_planes",
            "Add or import a roof plane before solar geometry prep.",
            blocks=True,
        ))
    valid_hours = [int(h) for h in request.sample_hours_local if 0 <= int(h) <= 23]
    if not valid_hours:
        issues.append(_issue(
            "SOLAR_GEOMETRY_SAMPLE_HOURS_INVALID",
            Severity.blocker,
            "No valid sample hours were provided.",
            "sample_hours_local",
            "Use local integer hours between 0 and 23.",
            blocks=True,
        ))
        valid_hours = [12]

    if any(i.blocks_status for i in issues):
        validation = ValidationReport(status="blocked", issues=issues, summary={"project_id": project_id})
        input_payload = {"project_id": project_id, "request": request.model_dump(mode="json"), "blocked": True}
        output_payload = {"status": "blocked", "issues": [i.model_dump(mode="json") for i in issues]}
        return SolarGeometryDebugRead(
            project_id=project_id,
            status="blocked",
            source_engine=_source_engine(),
            pvlib_available=pvlib_available(),
            site=geometry.site.model_dump(mode="json") if geometry.site else None,
            sample_day_mode=request.sample_day_mode,
            sample_hours_local=valid_hours,
            validation_report=validation,
            roof_plane_results=[],
            pvgis_geometry_comparison_notes=["Solar geometry debug blocked before PVGIS comparison notes could be prepared."],
            shade_engine_input_contract=_shade_engine_contract(),
            input_hash_sha256=stable_json_hash(input_payload),
            output_hash_sha256=stable_json_hash(output_payload),
        )

    lat = float(geometry.site.lat)
    lon = float(geometry.site.lon)
    tz = _project_tz(geometry.site.timezone)
    sample_days = _sample_dates(2026, request.sample_day_mode)
    hints = _selected_layout_hints(db, project_id, request.selected_layout_calculation_run_id)
    roof_results: list[RoofPlaneSolarGeometryRead] = []

    for roof in geometry.roof_planes:
        samples: list[SolarPositionSampleRead] = []
        sanity_notes: list[str] = []
        for month, day in sample_days:
            for hour in valid_hours:
                when_local = datetime(2026, month, day, hour, 0, 0, tzinfo=tz)
                pos = _solar_position(lat, lon, when_local)
                poa_cos, horiz_cos, factor, incidence_angle = _surface_cosines(pos, float(roof.pitch_deg), float(roof.azimuth_deg))
                if not (0.0 <= poa_cos <= 1.0 and 0.0 <= horiz_cos <= 1.0 and factor >= 0.0 and isfinite(factor)):
                    sanity_notes.append("INCIDENCE_MATH_OUT_OF_BOUNDS")
                if not (-90.0 <= pos.elevation_deg <= 90.0 and 0.0 <= pos.azimuth_deg < 360.0):
                    sanity_notes.append("SOLAR_POSITION_OUT_OF_BOUNDS")
                samples.append(SolarPositionSampleRead(
                    timestamp_local=when_local.isoformat(),
                    month=month,
                    hour_local=hour,
                    solar_elevation_deg=pos.elevation_deg,
                    solar_azimuth_deg=pos.azimuth_deg,
                    solar_zenith_deg=pos.zenith_deg,
                    declination_deg=pos.declination_deg,
                    incidence_angle_deg=incidence_angle,
                    plane_of_array_cosine=poa_cos,
                    horizontal_cosine=horiz_cos,
                    beam_plane_factor_vs_horizontal=round(min(factor, 5.0), 6),
                    sun_up=pos.elevation_deg > 0,
                    source_engine=pos.source_engine,
                ))
        sun_up_samples = [s for s in samples if s.sun_up]
        factors = [s.beam_plane_factor_vs_horizontal for s in sun_up_samples] or [0.0]
        elevations = [s.solar_elevation_deg for s in sun_up_samples] or [0.0]
        noon_factors = [s.beam_plane_factor_vs_horizontal for s in sun_up_samples if s.hour_local == 12] or factors
        hint = hints.get(roof.roof_plane_id, {"panel_count": 0, "dc_kwp": 0.0})
        roof_results.append(RoofPlaneSolarGeometryRead(
            roof_plane_id=roof.roof_plane_id,
            roof_label=roof.label,
            pitch_deg=roof.pitch_deg,
            azimuth_deg=roof.azimuth_deg,
            panel_count_hint=int(hint.get("panel_count", 0)),
            dc_kwp_hint=float(hint.get("dc_kwp", 0.0)),
            sample_count=len(samples),
            mean_solar_elevation_deg=round(sum(elevations) / len(elevations), 4),
            mean_beam_plane_factor_vs_horizontal=round(sum(factors) / len(factors), 6),
            min_beam_plane_factor_vs_horizontal=round(min(factors), 6),
            max_beam_plane_factor_vs_horizontal=round(max(factors), 6),
            noon_mean_beam_plane_factor_vs_horizontal=round(sum(noon_factors) / len(noon_factors), 6),
            samples=samples,
            sanity_notes=sorted(set(sanity_notes)),
        ))

    status = "blocked" if any(i.blocks_status for i in issues) else ("warnings" if issues else "ok")
    validation = ValidationReport(status=status, issues=issues, summary={
        "project_id": project_id,
        "source_engine": _source_engine(),
        "pvlib_available": pvlib_available(),
        "roof_plane_count": len(roof_results),
        "sample_count_total": sum(r.sample_count for r in roof_results),
    })
    input_payload = {
        "project_id": project_id,
        "site": geometry.site.model_dump(mode="json"),
        "roof_planes": [roof.model_dump(mode="json") for roof in geometry.roof_planes],
        "request": request.model_dump(mode="json"),
        "source_engine": _source_engine(),
    }
    output_payload = {
        "status": status,
        "validation": validation.model_dump(mode="json"),
        "roof_plane_results": [r.model_dump(mode="json") for r in roof_results],
        "pvgis_geometry_comparison_notes": _pvgis_geometry_notes(),
        "shade_engine_input_contract": _shade_engine_contract() if request.include_shade_engine_contract else {},
    }
    input_hash = stable_json_hash(input_payload)
    output_hash = stable_json_hash(output_payload)
    built = build_calculation_run(CalculationRunCreate(
        project_id=project_id,
        run_type=CalculationRunType.yield_calc,
        engine_version="0.5.2-solar-geometry-prep",
        input_snapshot=input_payload,
        assumption_set_id="SOLAR_GEOMETRY_PREP_V0_1",
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
        assumption_set_id="SOLAR_GEOMETRY_PREP_V0_1",
        warnings=built.warnings,
        input_snapshot=input_payload,
        output_snapshot=output_payload,
    )
    db.add(row)
    db.commit()
    return SolarGeometryDebugRead(
        project_id=project_id,
        status=status,
        source_engine=_source_engine(),
        pvlib_available=pvlib_available(),
        site=geometry.site.model_dump(mode="json"),
        sample_day_mode=request.sample_day_mode,
        sample_hours_local=valid_hours,
        validation_report=validation,
        roof_plane_results=roof_results,
        pvgis_geometry_comparison_notes=_pvgis_geometry_notes(),
        shade_engine_input_contract=_shade_engine_contract() if request.include_shade_engine_contract else {},
        input_hash_sha256=input_hash,
        output_hash_sha256=output_hash,
        calculation_run_id=row.run_id,
    )


def _pvgis_geometry_notes() -> list[str]:
    return [
        "ArrayLab roof azimuth uses 180 degrees true south; PVGIS aspect uses 0 degrees south, -90 east, +90 west in the PVcalc adapter.",
        "PVGIS monthly output is location/weather evidence; this solar-geometry debug explains tilt/azimuth incidence assumptions before shade modelling.",
        "Phase 005C does not perform full POA irradiance transposition or pvlib ModelChain yet; it prepares the input contract and sanity checks.",
    ]


def _shade_engine_contract() -> dict:
    return {
        "contract_version": "SHADE_INPUT_CONTRACT_V0_1",
        "required_inputs": [
            "site.lat", "site.lon", "site.timezone", "roof_plane.pitch_deg", "roof_plane.azimuth_deg",
            "panel_placements.local_polygon_m", "panel_placements.roof_plane_id", "obstructions.polygon_local_m", "obstructions.height_m",
        ],
        "sample_output_fields": [
            "timestamp_local", "solar_elevation_deg", "solar_azimuth_deg", "panel_id", "sample_point_local_m", "ray_blocked", "blocker_id", "confidence",
        ],
        "truth_boundary": "Contract only. No shade-loss or final yield adjustment is calculated in Phase 005C.",
    }


def _make_project_for_check(db: Session, project_id: str, azimuth: float, pitch: float):
    project = create_project(db, ProjectCreate(project_id=project_id, title=f"Solar geometry check {project_id}"))
    upsert_site(db, project.project_id, SiteCreate(postcode="EX14 3JF", lat=50.8155, lon=-3.2806, timezone="Europe/London", source_type="postcode_lookup"))
    add_roof_plane(db, project.project_id, RoofPlaneCreate(
        roof_type=RoofType.tiled_pitched,
        pitch_deg=pitch,
        azimuth_deg=azimuth,
        height_m=6.0,
        polygon_local_m=[[0, 0], [11, 0], [11, 6], [0, 6]],
        source_confidence=0.7,
    ))
    layout = run_panel_packing(db, project.project_id, PanelPackingRequest(allow_dev_fallback_panels=True))
    return project, layout


def solar_geometry_self_check(db: Session) -> SolarGeometrySelfCheckRead:
    south, south_layout = _make_project_for_check(db, "prj_solar_geom_south_35", 180.0, 35.0)
    north, north_layout = _make_project_for_check(db, "prj_solar_geom_north_35", 0.0, 35.0)
    steep, steep_layout = _make_project_for_check(db, "prj_solar_geom_south_55", 180.0, 55.0)
    east, east_layout = _make_project_for_check(db, "prj_solar_geom_east_35", 90.0, 35.0)

    south_result = build_solar_geometry_debug(db, south.project_id, SolarGeometryDebugRequest(selected_layout_calculation_run_id=south_layout.calculation_run_id))
    north_result = build_solar_geometry_debug(db, north.project_id, SolarGeometryDebugRequest(selected_layout_calculation_run_id=north_layout.calculation_run_id))
    steep_result = build_solar_geometry_debug(db, steep.project_id, SolarGeometryDebugRequest(selected_layout_calculation_run_id=steep_layout.calculation_run_id))
    east_result = build_solar_geometry_debug(db, east.project_id, SolarGeometryDebugRequest(selected_layout_calculation_run_id=east_layout.calculation_run_id))

    south_factor = south_result.roof_plane_results[0].noon_mean_beam_plane_factor_vs_horizontal
    north_factor = north_result.roof_plane_results[0].noon_mean_beam_plane_factor_vs_horizontal
    east_factor = east_result.roof_plane_results[0].mean_beam_plane_factor_vs_horizontal
    south_mean = south_result.roof_plane_results[0].mean_beam_plane_factor_vs_horizontal
    all_samples = south_result.roof_plane_results[0].samples + north_result.roof_plane_results[0].samples + steep_result.roof_plane_results[0].samples + east_result.roof_plane_results[0].samples
    bounds_ok = all(
        -90.0 <= s.solar_elevation_deg <= 90.0
        and 0.0 <= s.solar_azimuth_deg < 360.0
        and 0.0 <= s.plane_of_array_cosine <= 1.0
        and 0.0 <= s.horizontal_cosine <= 1.0
        and 0.0 <= s.beam_plane_factor_vs_horizontal <= 5.0
        for s in all_samples
    )
    sample_count_ok = all(r.roof_plane_results[0].sample_count == 36 for r in [south_result, north_result, steep_result, east_result])
    ok = (
        south_factor > north_factor
        and steep_result.output_hash_sha256 != south_result.output_hash_sha256
        and abs(east_factor - south_mean) > 0.01
        and sample_count_ok
        and bounds_ok
        and east_result.output_hash_sha256 != south_result.output_hash_sha256
    )
    return SolarGeometrySelfCheckRead(
        status="ok" if ok else "failed",
        pvlib_available=pvlib_available(),
        source_engine=_source_engine(),
        south_35_beats_north_35=south_factor > north_factor,
        tilt_change_changes_hash=steep_result.output_hash_sha256 != south_result.output_hash_sha256,
        azimuth_change_changes_factor=abs(east_factor - south_mean) > 0.01,
        sample_count_ok=sample_count_ok,
        incidence_math_bounds_ok=bounds_ok,
        calculation_hash_changes_with_geometry=east_result.output_hash_sha256 != south_result.output_hash_sha256,
        project_id=south.project_id,
        output_hash_sha256=south_result.output_hash_sha256,
    )

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.validation import ValidationReport


class SolarGeometryDebugRequest(BaseModel):
    selected_layout_calculation_run_id: str | None = None
    sample_day_mode: str = Field(default="monthly_21st", pattern="^(monthly_21st|seasonal_key_days)$")
    sample_hours_local: list[int] = Field(default_factory=lambda: [9, 12, 15])
    include_shade_engine_contract: bool = True


class SolarPositionSampleRead(BaseModel):
    timestamp_local: str
    month: int
    hour_local: int
    solar_elevation_deg: float
    solar_azimuth_deg: float
    solar_zenith_deg: float
    declination_deg: float | None = None
    incidence_angle_deg: float | None = None
    plane_of_array_cosine: float
    horizontal_cosine: float
    beam_plane_factor_vs_horizontal: float
    sun_up: bool
    source_engine: str


class RoofPlaneSolarGeometryRead(BaseModel):
    roof_plane_id: str
    roof_label: str | None = None
    pitch_deg: float | None = None
    azimuth_deg: float | None = None
    panel_count_hint: int = 0
    dc_kwp_hint: float = 0.0
    sample_count: int
    mean_solar_elevation_deg: float
    mean_beam_plane_factor_vs_horizontal: float
    min_beam_plane_factor_vs_horizontal: float
    max_beam_plane_factor_vs_horizontal: float
    noon_mean_beam_plane_factor_vs_horizontal: float
    samples: list[SolarPositionSampleRead] = Field(default_factory=list)
    sanity_notes: list[str] = Field(default_factory=list)


class SolarGeometryDebugRead(BaseModel):
    project_id: str
    status: str
    source_engine: str
    pvlib_available: bool
    site: dict | None = None
    sample_day_mode: str
    sample_hours_local: list[int]
    validation_report: ValidationReport
    roof_plane_results: list[RoofPlaneSolarGeometryRead]
    pvgis_geometry_comparison_notes: list[str]
    shade_engine_input_contract: dict
    input_hash_sha256: str
    output_hash_sha256: str
    calculation_run_id: str | None = None
    truth_boundary: str = "Solar-position and roof-plane geometry debug only; not final yield, shade, or proposal truth."


class SolarGeometrySelfCheckRead(BaseModel):
    status: str
    pvlib_available: bool
    source_engine: str
    south_35_beats_north_35: bool
    tilt_change_changes_hash: bool
    azimuth_change_changes_factor: bool
    sample_count_ok: bool
    incidence_math_bounds_ok: bool
    calculation_hash_changes_with_geometry: bool
    project_id: str | None = None
    output_hash_sha256: str | None = None

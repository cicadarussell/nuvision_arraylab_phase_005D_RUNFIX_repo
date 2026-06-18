from __future__ import annotations

from app.core.compat import StrEnum
from pydantic import BaseModel, Field, model_validator

from app.schemas.validation import ValidationReport


class YieldModelTier(StrEnum):
    t0_rough = "T0_rough_kwh_per_kwp"
    t1_pvgis_monthly_stub = "T1_pvgis_monthly_stub"
    t1_pvgis_monthly_cached = "T1_pvgis_monthly_cached"


class YieldAssumptionSetRead(BaseModel):
    assumption_set_id: str
    title: str
    model_tier: YieldModelTier = YieldModelTier.t0_rough
    specific_yield_kwh_per_kwp_year: float = Field(ge=100, le=2000)
    system_loss_pct: float = Field(ge=0, le=60)
    shade_loss_pct: float = Field(default=0, ge=0, le=90)
    degradation_year1_pct: float = Field(default=0, ge=0, le=20)
    albedo: float = Field(default=0.2, ge=0, le=1)
    source: str
    review_status: str = "preview_default"
    created_by: str | None = None


class YieldPreviewRequest(BaseModel):
    selected_layout_calculation_run_id: str = Field(min_length=1)
    assumption_set_id: str = "UK_ROOF_PREVIEW_V0_1"
    model_tier: YieldModelTier = YieldModelTier.t0_rough
    specific_yield_kwh_per_kwp_year: float | None = Field(default=None, ge=100, le=2000)
    system_loss_pct: float | None = Field(default=None, ge=0, le=60)
    shade_loss_pct: float | None = Field(default=None, ge=0, le=90)
    include_pvgis_request_stub: bool = True
    use_pvgis_monthly: bool = False
    allow_pvgis_network_fetch: bool = False
    force_pvgis_refresh: bool = False
    pvgis_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    # Optional manual PVGIS monthly values for controlled tests/offline comparisons.
    # These are not fetched by the browser and still become preview evidence only.
    pvgis_monthly_kwh_override: list[float] | None = None

    @model_validator(mode="after")
    def validate_monthly_override(self):
        if self.pvgis_monthly_kwh_override is not None:
            if len(self.pvgis_monthly_kwh_override) != 12:
                raise ValueError("pvgis_monthly_kwh_override must contain exactly 12 monthly kWh values")
            if any(value < 0 for value in self.pvgis_monthly_kwh_override):
                raise ValueError("pvgis_monthly_kwh_override values must be non-negative")
        return self


class MonthlyYieldRead(BaseModel):
    month: int
    month_name: str
    kwh: float
    share_of_annual: float


class RoofPlaneYieldRead(BaseModel):
    roof_plane_id: str
    panel_count: int
    dc_kwp: float
    pitch_deg: float | None = None
    azimuth_deg: float | None = None
    azimuth_factor: float
    tilt_factor: float
    orientation_factor: float
    annual_kwh_preview: float


class PvgisCacheRead(BaseModel):
    request_hash_sha256: str
    endpoint: str
    params: dict
    status: str
    adapter_version: str
    attempt_count: int = 0
    cache_hit_count: int = 0
    http_status_code: int | None = None
    annual_kwh: float | None = None
    parsed_monthly: list[dict] = Field(default_factory=list)
    error_message: str | None = None
    url_preview: str | None = None
    final_url: str | None = None
    response_hash_sha256: str | None = None
    truth_boundary: str = "PVGIS cache evidence for preview yield; not final proposal truth by itself"


class YieldPreviewResultRead(BaseModel):
    project_id: str
    status: str
    design_status: str
    calculation_run_id: str
    selected_layout_calculation_run_id: str
    selected_layout_export_hash_sha256: str
    input_snapshot_hash_sha256: str
    output_hash_sha256: str
    assumption_set: YieldAssumptionSetRead
    validation_report: ValidationReport
    total_dc_kwp: float
    annual_kwh_preview: float
    specific_yield_kwh_per_kwp_after_losses: float
    monthly: list[MonthlyYieldRead] = Field(default_factory=list)
    roof_plane_results: list[RoofPlaneYieldRead] = Field(default_factory=list)
    pvgis_request_stub: dict | None = None
    pvgis_cache: PvgisCacheRead | None = None
    pvgis_comparison: dict | None = None
    summary: dict = Field(default_factory=dict)
    truth_boundary: str = "preview yield estimate only; not final proposal, MCS, financial, structural, or electrical approval"


class YieldRunRead(BaseModel):
    project_id: str
    calculation_run_id: str
    run_type: str
    input_snapshot_hash_sha256: str
    output_hash_sha256: str | None = None
    output_snapshot: dict | None = None
    truth_boundary: str = "stored yield calculation evidence; preview-only unless later upgraded with reviewed assumptions and external checks"


class YieldPreviewSelfCheckRead(BaseModel):
    status: str
    project_id: str
    panel_layout_required_blocked: bool
    annual_kwh_positive: bool
    monthly_sum_matches_annual: bool
    assumption_change_changes_hash: bool
    pvgis_stub_backend_only: bool
    pvgis_monthly_cache_hit: bool = False
    pvgis_monthly_values_used: bool = False
    pvgis_unavailable_fallback_warns: bool = False
    calculation_run_id: str | None = None
    output_hash_sha256: str | None = None

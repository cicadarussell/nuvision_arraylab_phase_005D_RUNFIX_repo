from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.validation import ValidationReport


class ShadePreviewRequest(BaseModel):
    """First preview-only shade contract.

    Uses a selected panel-packing run plus current obstruction records. This is not
    annual shade loss, not PVsyst, not final yield. It is a deterministic debugging
    layer so the next maths phase starts from visible sample evidence instead of vibes.
    """

    selected_layout_calculation_run_id: str = Field(min_length=1)
    sample_day_mode: str = "seasonal_key_days"
    sample_hours_local: list[int] = Field(default_factory=lambda: [9, 12, 15])
    sample_grid_x: int = Field(default=3, ge=1, le=12)
    sample_grid_y: int = Field(default=3, ge=1, le=12)
    include_unshaded_samples: bool = False


class PanelShadeSummaryRead(BaseModel):
    placement_id: str
    roof_plane_id: str
    panel_model_id: str
    sample_count: int
    shaded_sample_count: int
    shaded_fraction: float
    worst_blocker_ids: list[str] = Field(default_factory=list)


class ShadePreviewResultRead(BaseModel):
    project_id: str
    status: str
    calculation_run_id: str | None = None
    selected_layout_calculation_run_id: str
    selected_layout_export_hash_sha256: str | None = None
    input_hash_sha256: str
    output_hash_sha256: str
    validation_report: ValidationReport
    sample_engine: str = "arraylab_2d_obstruction_shadow_v0_1"
    sample_count_total: int
    shaded_sample_count_total: int
    shaded_fraction_preview: float
    worst_panels: list[PanelShadeSummaryRead] = Field(default_factory=list)
    sample_debug: list[dict] = Field(default_factory=list)
    obstruction_shadow_debug: list[dict] = Field(default_factory=list)
    shade_result_hash_sha256: str
    truth_boundary: str = "preview obstruction-shadow debug only; not annual shade-adjusted yield, not final proposal maths"


class ShadePreviewSelfCheckRead(BaseModel):
    status: str
    project_id: str
    shade_changes_with_obstruction_height: bool
    shade_changes_with_obstruction_position: bool
    missing_obstruction_height_blocks: bool
    worst_panel_list_present: bool
    shade_hash_changes_with_geometry: bool
    sample_bounds_ok: bool
    calculation_run_id: str | None = None
    output_hash_sha256: str | None = None

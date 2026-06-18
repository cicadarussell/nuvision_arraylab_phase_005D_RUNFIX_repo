from app.core.compat import StrEnum
from pydantic import BaseModel, Field

class CalculationRunType(StrEnum):
    catalogue_import = "catalogue_import"
    datasheet_parse = "datasheet_parse"
    roof_geometry = "roof_geometry"
    panel_packing = "panel_packing"
    shade = "shade"
    yield_calc = "yield"
    stringing = "stringing"
    mounting_precheck = "mounting_precheck"
    bom = "bom"
    quote = "quote"

class CalculationStatus(StrEnum):
    preview = "preview"
    design_draft = "design_draft"
    survey_required = "survey_required"
    manufacturer_calc_required = "manufacturer_calc_required"
    engineer_review_required = "engineer_review_required"
    quote_ready = "quote_ready"
    install_pack_ready = "install_pack_ready"
    failed = "failed"

class CalculationRunCreate(BaseModel):
    project_id: str | None = None
    run_type: CalculationRunType
    engine_version: str = "0.1.0"
    input_snapshot: dict = Field(default_factory=dict)
    product_data_snapshot_id: str | None = None
    assumption_set_id: str | None = None
    warnings: list[str] = Field(default_factory=list)

class CalculationRunRead(CalculationRunCreate):
    run_id: str
    software_version: str
    input_snapshot_hash_sha256: str
    outputs_hash_sha256: str | None = None
    status: CalculationStatus
    created_at_utc: str

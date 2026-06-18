from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DatasheetArchiveRead(BaseModel):
    datasheet_id: str
    product_id: str | None = None
    file_name: str
    source_url: str | None = None
    source_type: str = "manufacturer_datasheet"
    file_hash_sha256: str
    byte_size: int
    page_count: int
    text_hash_sha256: str | None = None
    status: str
    extraction_report: dict = Field(default_factory=dict)
    uploaded_by: str | None = None

    model_config = {"from_attributes": True}


class DatasheetCandidateRead(BaseModel):
    candidate_id: str
    datasheet_id: str
    product_id: str | None = None
    field_name: str
    value_text: str | None = None
    value_number: float | None = None
    unit: str | None = None
    normalized_value_si: float | None = None
    normalized_unit: str | None = None
    source_page: int | None = None
    source_text_quote: str | None = None
    extraction_method: str
    confidence: float
    status: str
    validation_report: dict = Field(default_factory=dict)
    created_spec_id: str | None = None
    reviewed_by: str | None = None

    model_config = {"from_attributes": True}


class DatasheetReviewRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    reviewer: str = "unassigned_reviewer"
    corrected_value_text: str | None = None
    corrected_value_number: float | None = None
    corrected_unit: str | None = None
    reason: str | None = None
    selected_manufacturer_model: str | None = None
    selected_datasheet_variant: str | None = None
    model_selection_basis: str | None = None


class DatasheetBatchReviewRequest(BaseModel):
    candidate_ids: list[str]
    action: str = Field(pattern="^(approve|reject)$")
    reviewer: str = "unassigned_reviewer"
    reason: str | None = None


class DatasheetReviewRead(BaseModel):
    review_id: str
    candidate_id: str
    action: str
    reviewer: str
    corrected_value_text: str | None = None
    corrected_value_number: float | None = None
    corrected_unit: str | None = None
    reason: str | None = None
    created_spec_id: str | None = None
    review_payload_hash_sha256: str
    review_payload: dict = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class DatasheetTablePreviewRead(BaseModel):
    datasheet_id: str
    file_name: str
    source_url: str | None = None
    table_extractors: list[dict] = Field(default_factory=list)
    table_previews: list[dict] = Field(default_factory=list)
    status: str


class DatasheetOcrStatusRead(BaseModel):
    datasheet_id: str
    file_name: str
    ocr_status: str
    reason: str | None = None
    next_action: str
    truth_boundary: str


class ProductDesignReadinessRead(BaseModel):
    product_id: str
    status: str
    design_ready: bool
    quality_level: str
    category: str
    reviewed_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    issues: list[dict] = Field(default_factory=list)
    truth_boundary: str


class DatasheetSourceDomainCreate(BaseModel):
    domain: str
    status: str = Field(default="approved", pattern="^(approved|blocked|deprecated)$")
    source_kind: str = "manufacturer"
    notes: str | None = None
    created_by: str | None = None


class DatasheetSourceDomainRead(BaseModel):
    domain_id: str
    domain: str
    status: str
    source_kind: str
    notes: str | None = None
    created_by: str | None = None

    model_config = {"from_attributes": True}


class DatasheetDownloadQueueRequest(BaseModel):
    source_url: str
    product_id: str | None = None
    requested_by: str | None = None


class DatasheetDownloadJobRead(BaseModel):
    job_id: str
    product_id: str | None = None
    source_url: str
    source_domain: str
    status: str
    validation_report: dict = Field(default_factory=dict)
    requested_by: str | None = None
    datasheet_id: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    last_attempt_at: datetime | None = None

    model_config = {"from_attributes": True}


class DatasheetDownloadRunRequest(BaseModel):
    run_by: str | None = "local_worker"


class DatasheetOcrJobRead(BaseModel):
    ocr_job_id: str
    datasheet_id: str
    status: str
    reason: str | None = None
    engine: str | None = None
    output_text_hash_sha256: str | None = None
    validation_report: dict = Field(default_factory=dict)
    requested_by: str | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}

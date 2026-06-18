from __future__ import annotations

from pydantic import BaseModel, Field


class StagedImportRowRead(BaseModel):
    staged_row_id: str
    import_id: str
    sheet_name: str
    row_number: int
    row_hash_sha256: str
    payload: dict
    status: str

    model_config = {"from_attributes": True}


class SpreadsheetImportRead(BaseModel):
    import_id: str
    file_name: str
    file_hash_sha256: str
    status: str
    uploaded_by: str | None = None
    approved_by: str | None = None
    rejected_by: str | None = None
    validation_report: dict = Field(default_factory=dict)
    diff_summary: dict = Field(default_factory=dict)
    staged_row_count: int = 0

    model_config = {"from_attributes": True}


class ProductDataSnapshotRead(BaseModel):
    snapshot_id: str
    import_id: str
    content_hash_sha256: str
    row_count: int
    summary: dict = Field(default_factory=dict)
    snapshot_payload: dict = Field(default_factory=dict)
    created_by: str | None = None

    model_config = {"from_attributes": True}


class ApprovalRequest(BaseModel):
    approved_by: str = "unassigned_reviewer"


class RejectRequest(BaseModel):
    rejected_by: str = "unassigned_reviewer"
    reason: str | None = None

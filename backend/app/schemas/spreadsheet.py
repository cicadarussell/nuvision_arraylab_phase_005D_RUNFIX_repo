from __future__ import annotations

from app.core.compat import StrEnum
from pydantic import BaseModel, Field

from app.schemas.validation import ValidationIssue


class SpreadsheetImportStatus(StrEnum):
    uploaded = "uploaded"
    staged = "staged"
    failed_validation = "failed_validation"
    approved = "approved"
    rejected = "rejected"
    applied = "applied"
    rolled_back = "rolled_back"


class SheetInspection(BaseModel):
    sheet_name: str
    row_count: int
    column_count: int
    headers: list[str] = Field(default_factory=list)
    required_headers_missing: list[str] = Field(default_factory=list)


class SpreadsheetInspectionReport(BaseModel):
    file_name: str
    file_hash_sha256: str
    status: SpreadsheetImportStatus
    sheets: list[SheetInspection] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    can_stage: bool = False

from __future__ import annotations

from app.core.compat import StrEnum
from pydantic import BaseModel, Field


class Severity(StrEnum):
    info = "info"
    warning = "warning"
    error = "error"
    blocker = "blocker"


class ValidationArea(StrEnum):
    product_data = "product_data"
    spreadsheet = "spreadsheet"
    calculation = "calculation"
    roof_geometry = "roof_geometry"
    panel_packing = "panel_packing"
    mounting = "mounting"
    electrical = "electrical"
    bom = "bom"
    system = "system"


class ValidationIssue(BaseModel):
    code: str = Field(..., min_length=3)
    severity: Severity
    area: ValidationArea
    message: str
    path: str | None = None
    suggested_fix: str | None = None
    blocks_status: bool = False


class ValidationReport(BaseModel):
    status: str
    issues: list[ValidationIssue] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)

    @property
    def has_blockers(self) -> bool:
        return any(i.severity == Severity.blocker or i.blocks_status for i in self.issues)

    @property
    def has_errors(self) -> bool:
        return any(i.severity in {Severity.error, Severity.blocker} for i in self.issues)

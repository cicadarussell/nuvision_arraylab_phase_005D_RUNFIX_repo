from __future__ import annotations

from pydantic import BaseModel, Field


class ProductApplyPreviewRequest(BaseModel):
    created_by: str = "unassigned_reviewer"
    note: str | None = None


class ProductApplyRequest(BaseModel):
    applied_by: str = "unassigned_reviewer"
    note: str | None = None


class ProductApplyDiffItem(BaseModel):
    action: str
    product_id: str
    sheet_name: str = "Products"
    row_number: int | None = None
    before: dict | None = None
    after: dict
    warnings: list[str] = Field(default_factory=list)


class ProductSnapshotApplicationRead(BaseModel):
    application_id: str
    snapshot_id: str
    status: str
    created_by: str | None = None
    applied_by: str | None = None
    preview_hash_sha256: str
    diff_summary: dict = Field(default_factory=dict)
    validation_report: dict = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class CurrentProductRead(BaseModel):
    product_id: str
    nuvision_sku: str | None = None
    manufacturer: str
    manufacturer_model: str | None = None
    category: str
    title: str
    status: str
    quality_level: str
    nuvision_url: str | None = None
    design_ready: bool

    model_config = {"from_attributes": True}

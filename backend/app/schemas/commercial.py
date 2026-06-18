from __future__ import annotations

from pydantic import BaseModel, Field


class PriceStockPreviewRequest(BaseModel):
    created_by: str = "unassigned_reviewer"
    note: str | None = None


class PriceStockApplyRequest(BaseModel):
    applied_by: str = "unassigned_reviewer"
    note: str | None = None


class PriceStockApplicationRead(BaseModel):
    application_id: str
    snapshot_id: str
    status: str
    created_by: str | None = None
    applied_by: str | None = None
    preview_hash_sha256: str
    diff_summary: dict = Field(default_factory=dict)
    validation_report: dict = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class PriceSnapshotRead(BaseModel):
    price_snapshot_id: str
    product_id: str
    application_id: str
    source_snapshot_id: str
    trade_price_gbp: float | None = None
    list_price_gbp: float | None = None
    currency: str = "GBP"
    payload_hash_sha256: str
    payload: dict = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class StockSnapshotRead(BaseModel):
    stock_snapshot_id: str
    product_id: str
    application_id: str
    source_snapshot_id: str
    stock_status: str
    stock_quantity: int | None = None
    lead_time_days: int | None = None
    supplier_priority: str | None = None
    payload_hash_sha256: str
    payload: dict = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class QuoteSnapshotCreate(BaseModel):
    project_id: str | None = None
    product_ids: list[str]
    created_by: str = "unassigned_reviewer"
    note: str | None = None


class QuoteSnapshotRead(BaseModel):
    quote_id: str
    project_id: str | None = None
    quote_payload_hash_sha256: str
    quote_payload: dict
    price_snapshot_ids: list[str] = Field(default_factory=list)
    stock_snapshot_ids: list[str] = Field(default_factory=list)
    created_by: str | None = None

    model_config = {"from_attributes": True}


class RollbackRecordCreate(BaseModel):
    target_type: str
    target_id: str
    reason: str
    requested_by: str = "unassigned_reviewer"
    payload: dict = Field(default_factory=dict)


class RollbackRecordRead(BaseModel):
    rollback_id: str
    target_type: str
    target_id: str
    rollback_kind: str
    reason: str
    requested_by: str | None = None
    target_hash_sha256: str | None = None
    payload: dict = Field(default_factory=dict)

    model_config = {"from_attributes": True}

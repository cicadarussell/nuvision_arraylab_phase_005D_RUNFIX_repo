from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.db_models import Base, PriceSnapshot, Product, StockSnapshot
from app.services.commercial_snapshots import (
    apply_price_stock_application,
    build_price_stock_apply_preview,
    create_quote_snapshot,
    list_latest_price_snapshots,
    list_latest_stock_snapshots,
)
from app.services.product_snapshot_apply import apply_product_snapshot_application, build_product_apply_preview, list_current_products
from app.services.spreadsheet_staging import approve_staged_import, stage_spreadsheet_import

REQUIRED_SHEETS = {
    "Products": ["product_id", "manufacturer", "category", "title", "status", "quality_level", "nuvision_sku", "manufacturer_model", "nuvision_url"],
    "Prices_Stock": ["product_id", "stock_status", "lead_time_days", "trade_price_gbp", "list_price_gbp", "currency", "stock_quantity", "supplier_priority"],
    "Datasheet_Review": ["product_id", "field_name", "review_status", "corrected_value", "unit", "source_url"],
    "Labour_Rules": ["rule_id", "scope", "condition", "base_hours", "multiplier", "review_status"],
    "Mounting_Rules": ["mapping_id", "roof_type", "manufacturer", "system_family", "requires_manufacturer_calc", "review_status"],
    "Workflow_Feedback": ["feedback_id", "category", "severity", "description", "triage_status"],
}


def make_workbook(product_id: str = "prd_panel_001", price: float = 100.0, stock_status: str = "in_stock", status: str = "active", include_product: bool = True) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, headers in REQUIRED_SHEETS.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
        if name == "Products" and include_product:
            ws.append([product_id, "JA Solar", "panel", "JA Solar commercial test panel", status, "Q3_reviewed", "JA-TEST-001", "JAM54D", "https://example.com/product"])
        elif name == "Datasheet_Review" and include_product:
            for field, value in {
                "power_stc_w": 450, "length_mm": 1762, "width_mm": 1134, "voc_v": 49.9, "vmp_v": 41.5, "isc_a": 11.4, "imp_a": 10.85,
            }.items():
                ws.append([product_id, field, "reviewed", value, "mixed", "https://example.com/datasheet.pdf"])
        elif name == "Prices_Stock":
            ws.append([product_id, stock_status, 3, price, price * 1.2, "GBP", 42, "preferred"])
        elif name == "Labour_Rules":
            ws.append(["lab_001", "roof_type", "tiled_pitched", 8, 1.0, "reviewed"])
        elif name == "Mounting_Rules":
            ws.append(["map_001", "tiled_pitched", "Van der Valk", "ValkPitched Clamp", True, "reviewed"])
        elif name == "Workflow_Feedback":
            ws.append(["fb_001", "data", "low", "seed feedback", "new"])
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as session:
        yield session


def approved_snapshot(db: Session, data: bytes):
    record = stage_spreadsheet_import(db, "commercial.xlsx", data, uploaded_by="tester")
    assert record.status == "staged"
    return approve_staged_import(db, record.import_id, approved_by="reviewer")


def materialise_product_and_price(db: Session, data: bytes):
    snapshot = approved_snapshot(db, data)
    product_app = build_product_apply_preview(db, snapshot.snapshot_id, created_by="reviewer")
    if product_app.status == "previewed":
        apply_product_snapshot_application(db, product_app.application_id, applied_by="reviewer")
    price_app = build_price_stock_apply_preview(db, snapshot.snapshot_id, created_by="reviewer")
    applied = apply_price_stock_application(db, price_app.application_id, applied_by="reviewer")
    return snapshot, applied


def test_price_stock_preview_does_not_mutate_snapshots(db: Session):
    snapshot = approved_snapshot(db, make_workbook())
    app = build_price_stock_apply_preview(db, snapshot.snapshot_id, created_by="reviewer")
    assert app.status == "previewed"
    assert app.diff_summary["actions"]["create"] == 1
    assert db.query(PriceSnapshot).count() == 0
    assert db.query(StockSnapshot).count() == 0


def test_apply_price_stock_creates_immutable_commercial_snapshots(db: Session):
    _snapshot, app = materialise_product_and_price(db, make_workbook(price=123.45))
    assert app.status == "applied"
    prices = list_latest_price_snapshots(db)
    stocks = list_latest_stock_snapshots(db)
    assert len(prices) == 1
    assert prices[0].trade_price_gbp == 123.45
    assert len(stocks) == 1
    assert stocks[0].stock_status == "in_stock"
    assert stocks[0].stock_quantity == 42


def test_price_change_does_not_rewrite_old_quote_snapshot(db: Session):
    materialise_product_and_price(db, make_workbook(price=100.0))
    quote = create_quote_snapshot(db, project_id="project_001", product_ids=["prd_panel_001"], created_by="tester")
    assert quote.quote_payload["items"][0]["price_snapshot"]["trade_price_gbp"] == 100.0

    materialise_product_and_price(db, make_workbook(price=155.0))
    assert list_latest_price_snapshots(db)[0].trade_price_gbp == 155.0

    db.refresh(quote)
    assert quote.quote_payload["items"][0]["price_snapshot"]["trade_price_gbp"] == 100.0
    assert quote.quote_payload_hash_sha256 == quote.quote_id.replace("quote_", "")[:16] or len(quote.quote_payload_hash_sha256) == 64


def test_omitted_product_does_not_vanish_silently(db: Session):
    materialise_product_and_price(db, make_workbook(product_id="prd_panel_001"))
    assert len(list_current_products(db)) == 1
    materialise_product_and_price(db, make_workbook(product_id="prd_panel_002", price=80.0))
    ids = {p.product_id for p in list_current_products(db)}
    assert ids == {"prd_panel_001", "prd_panel_002"}


def test_deleted_status_is_blocked_not_silently_removed(db: Session):
    snapshot = approved_snapshot(db, make_workbook(status="deleted"))
    app = build_product_apply_preview(db, snapshot.snapshot_id, created_by="reviewer")
    assert app.status == "blocked"
    assert any(i["code"] == "PRODUCT_DELETION_NOT_ALLOWED" for i in app.validation_report["issues"])
    assert db.query(Product).count() == 0

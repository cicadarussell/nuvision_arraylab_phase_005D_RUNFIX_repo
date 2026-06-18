from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.db_models import Base, Product, ProductDataSnapshot, ProductVersion
from app.services.product_snapshot_apply import (
    ProductApplyWorkflowError,
    apply_product_snapshot_application,
    build_product_apply_preview,
    list_current_products,
)
from app.services.spreadsheet_staging import approve_staged_import, stage_spreadsheet_import


REQUIRED_SHEETS = {
    "Products": ["product_id", "manufacturer", "category", "title", "status", "quality_level", "nuvision_sku", "manufacturer_model", "nuvision_url"],
    "Prices_Stock": ["product_id", "stock_status", "lead_time_days"],
    "Datasheet_Review": ["product_id", "field_name", "review_status", "corrected_value", "unit", "source_url"],
    "Labour_Rules": ["rule_id", "scope", "condition", "base_hours", "multiplier", "review_status"],
    "Mounting_Rules": ["mapping_id", "roof_type", "manufacturer", "system_family", "requires_manufacturer_calc", "review_status"],
    "Workflow_Feedback": ["feedback_id", "category", "severity", "description", "triage_status"],
}


def make_product_workbook(product_id: str = "prd_panel_001", quality_level: str = "Q3_reviewed", title: str = "JA Solar reviewed panel", reviewed_all_panel_specs: bool = True) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, headers in REQUIRED_SHEETS.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
        if name == "Products":
            ws.append([product_id, "JA Solar", "panel", title, "active", quality_level, "JA-TEST-001", "JAM54D", "https://example.com/product"])
        elif name == "Datasheet_Review":
            fields = ["power_stc_w"]
            if reviewed_all_panel_specs:
                fields += ["length_mm", "width_mm", "voc_v", "vmp_v", "isc_a", "imp_a"]
            values = {
                "power_stc_w": 450,
                "length_mm": 1762,
                "width_mm": 1134,
                "voc_v": 49.9,
                "vmp_v": 41.5,
                "isc_a": 11.4,
                "imp_a": 10.85,
            }
            for field in fields:
                ws.append([product_id, field, "reviewed", values[field], "mixed", "https://example.com/datasheet.pdf"])
        elif name == "Prices_Stock":
            ws.append([product_id, "in_stock", 3])
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


def approved_snapshot(db: Session, data: bytes) -> ProductDataSnapshot:
    record = stage_spreadsheet_import(db, "products.xlsx", data, uploaded_by="tester")
    assert record.status == "staged"
    return approve_staged_import(db, record.import_id, approved_by="reviewer")


def test_preview_does_not_mutate_current_products(db: Session):
    snapshot = approved_snapshot(db, make_product_workbook())
    app = build_product_apply_preview(db, snapshot.snapshot_id, created_by="reviewer")
    assert app.status == "previewed"
    assert app.diff_summary["actions"]["create"] == 1
    assert list_current_products(db) == []


def test_apply_snapshot_creates_current_product_version_and_specs(db: Session):
    snapshot = approved_snapshot(db, make_product_workbook())
    app = build_product_apply_preview(db, snapshot.snapshot_id, created_by="reviewer")
    applied = apply_product_snapshot_application(db, app.application_id, applied_by="reviewer")

    assert applied.status == "applied"
    products = list_current_products(db)
    assert len(products) == 1
    assert products[0].product_id == "prd_panel_001"
    assert products[0].design_ready is True
    assert db.query(ProductVersion).count() == 1
    assert db.query(Product).one().specs


def test_q0_product_is_applied_but_not_design_ready(db: Session):
    snapshot = approved_snapshot(db, make_product_workbook(quality_level="Q0_scraped"))
    app = build_product_apply_preview(db, snapshot.snapshot_id, created_by="reviewer")
    warning_text = " ".join(app.diff_summary["diff_items"][0]["warnings"])
    assert "not Q3/Q4" in warning_text
    apply_product_snapshot_application(db, app.application_id, applied_by="reviewer")
    assert list_current_products(db)[0].design_ready is False


def test_panel_missing_required_specs_is_not_design_ready(db: Session):
    snapshot = approved_snapshot(db, make_product_workbook(reviewed_all_panel_specs=False))
    app = build_product_apply_preview(db, snapshot.snapshot_id, created_by="reviewer")
    assert "missing reviewed critical specs" in " ".join(app.diff_summary["diff_items"][0]["warnings"])
    apply_product_snapshot_application(db, app.application_id, applied_by="reviewer")
    assert list_current_products(db)[0].design_ready is False


def test_application_cannot_be_applied_twice(db: Session):
    snapshot = approved_snapshot(db, make_product_workbook())
    app = build_product_apply_preview(db, snapshot.snapshot_id, created_by="reviewer")
    apply_product_snapshot_application(db, app.application_id, applied_by="reviewer")
    with pytest.raises(ProductApplyWorkflowError):
        apply_product_snapshot_application(db, app.application_id, applied_by="reviewer")


def test_later_update_preserves_old_product_version_payload(db: Session):
    first = approved_snapshot(db, make_product_workbook(title="Original title"))
    first_app = build_product_apply_preview(db, first.snapshot_id, created_by="reviewer")
    apply_product_snapshot_application(db, first_app.application_id, applied_by="reviewer")

    second = approved_snapshot(db, make_product_workbook(title="Updated title"))
    second_app = build_product_apply_preview(db, second.snapshot_id, created_by="reviewer")
    apply_product_snapshot_application(db, second_app.application_id, applied_by="reviewer")

    versions = db.query(ProductVersion).filter_by(product_id="prd_panel_001").all()
    titles = {v.product_payload["title"] for v in versions}
    assert titles == {"Original title", "Updated title"}
    assert list_current_products(db)[0].title == "Updated title"

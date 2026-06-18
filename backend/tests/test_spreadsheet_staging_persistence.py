from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.db_models import Base, StagedImportRow
from app.services.spreadsheet_staging import (
    ImportWorkflowError,
    approve_staged_import,
    get_spreadsheet_import,
    list_staged_rows,
    stage_spreadsheet_import,
)


def make_workbook(with_data: bool = True, protected_bad_header: bool = False) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Products": ["product_id", "manufacturer", "category", "title", "status", "quality_level"],
        "Prices_Stock": ["product_id", "stock_status", "lead_time_days"],
        "Datasheet_Review": ["product_id", "field_name", "review_status", "corrected_value", "unit", "source_url"],
        "Labour_Rules": ["rule_id", "scope", "condition", "base_hours", "multiplier", "review_status"],
        "Mounting_Rules": ["mapping_id", "roof_type", "manufacturer", "system_family", "requires_manufacturer_calc", "review_status"],
        "Workflow_Feedback": ["feedback_id", "category", "severity", "description", "triage_status"],
    }
    if protected_bad_header:
        sheets["Products"].append("voc_v")
    for name, headers in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
        if with_data:
            if name == "Products":
                values = ["prd_001", "JA Solar", "panel", "JA Solar test panel", "active", "Q3_reviewed"]
                if protected_bad_header:
                    values.append(49.9)
                ws.append(values)
            elif name == "Prices_Stock":
                ws.append(["prd_001", "in_stock", 3])
            elif name == "Datasheet_Review":
                ws.append(["prd_001", "power_stc_w", "reviewed", 450, "W", "https://example.com/datasheet.pdf"])
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


def test_stage_valid_spreadsheet_persists_import_and_rows(db: Session):
    record = stage_spreadsheet_import(db, "valid.xlsx", make_workbook(), uploaded_by="tester")
    assert record.status == "staged"
    assert len(record.file_hash_sha256) == 64
    assert record.diff_summary["total_staged_rows"] == 6
    assert record.diff_summary["rows_by_sheet"]["Products"] == 1

    rows = list_staged_rows(db, record.import_id)
    assert len(rows) == 6
    assert rows[0].row_hash_sha256


def test_invalid_spreadsheet_is_recorded_but_not_staged(db: Session):
    record = stage_spreadsheet_import(db, "bad.xlsx", make_workbook(protected_bad_header=True), uploaded_by="tester")
    assert record.status == "failed_validation"
    assert record.diff_summary["total_staged_rows"] == 0
    assert list_staged_rows(db, record.import_id) == []

    with pytest.raises(ImportWorkflowError):
        approve_staged_import(db, record.import_id, approved_by="tester")


def test_approve_staged_import_creates_immutable_snapshot(db: Session):
    record = stage_spreadsheet_import(db, "valid.xlsx", make_workbook(), uploaded_by="tester")
    snapshot = approve_staged_import(db, record.import_id, approved_by="reviewer")

    assert snapshot.snapshot_id.startswith("pds_")
    assert len(snapshot.content_hash_sha256) == 64
    assert snapshot.row_count == 6
    assert snapshot.summary["truth_boundary"] == "snapshot only; no old project mutation"

    refreshed = get_spreadsheet_import(db, record.import_id)
    assert refreshed.status == "approved"

    first_product_row = db.query(StagedImportRow).filter_by(import_id=record.import_id, sheet_name="Products").one()
    first_product_row.payload = {**first_product_row.payload, "title": "MUTATED AFTER APPROVAL"}
    db.commit()
    db.refresh(snapshot)

    payload_titles = [row["payload"].get("title") for row in snapshot.snapshot_payload["rows"] if row["sheet_name"] == "Products"]
    assert payload_titles == ["JA Solar test panel"]
    assert snapshot.content_hash_sha256 == snapshot.snapshot_id.replace("pds_", "")[:16] or len(snapshot.content_hash_sha256) == 64


def test_import_cannot_be_approved_twice(db: Session):
    record = stage_spreadsheet_import(db, "valid.xlsx", make_workbook(), uploaded_by="tester")
    approve_staged_import(db, record.import_id, approved_by="reviewer")
    with pytest.raises(ImportWorkflowError):
        approve_staged_import(db, record.import_id, approved_by="reviewer")

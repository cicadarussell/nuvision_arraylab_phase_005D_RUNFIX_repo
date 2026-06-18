from __future__ import annotations

from io import BytesIO

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.db_models import Base, Product, ProductSpec
from app.services.datasheet_review import (
    DatasheetWorkflowError,
    archive_datasheet_bytes,
    get_datasheet_ocr_status,
    get_datasheet_table_preview,
    list_candidates,
    list_datasheet_review_queue_v2,
    recalculate_product_design_readiness,
    review_candidate,
)

try:
    import pymupdf  # type: ignore
except Exception:  # pragma: no cover
    import fitz as pymupdf  # type: ignore


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as session:
        yield session


def make_pdf(text: str | None) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    out = BytesIO()
    doc.save(out)
    doc.close()
    return out.getvalue()


def add_product(db: Session, product_id: str = "prd_ui_panel") -> Product:
    product = Product(
        product_id=product_id,
        manufacturer="JA Solar",
        manufacturer_model="JAM54D-test",
        category="panel",
        title="JA Solar UI review test panel",
        status="active",
        quality_level="Q0_scraped",
        design_ready=False,
    )
    db.add(product)
    db.commit()
    return product


def test_conflicted_candidate_requires_model_selection_and_basis(db: Session):
    product = add_product(db)
    datasheet = archive_datasheet_bytes(
        db,
        file_name="multi_model.pdf",
        data=make_pdf("Maximum Power Pmax 440 W\nMaximum Power Pmax 450 W\nOpen Circuit Voltage Voc 49.9 V"),
        product_id=product.product_id,
    )
    power = next(c for c in list_candidates(db, datasheet_id=datasheet.datasheet_id) if c.field_name == "power_stc_w")
    assert power.status == "needs_model_review"

    with pytest.raises(DatasheetWorkflowError, match="selected_manufacturer_model"):
        review_candidate(
            db,
            power.candidate_id,
            action="approve",
            reviewer="technical_reviewer",
            corrected_value_number=450,
            corrected_unit="W",
            reason="450 W row matches the installed product.",
        )

    with pytest.raises(DatasheetWorkflowError, match="model_selection_basis"):
        review_candidate(
            db,
            power.candidate_id,
            action="approve",
            reviewer="technical_reviewer",
            corrected_value_number=450,
            corrected_unit="W",
            reason="450 W row matches the installed product.",
            selected_manufacturer_model="JAM54D-450",
        )

    review = review_candidate(
        db,
        power.candidate_id,
        action="approve",
        reviewer="technical_reviewer",
        corrected_value_number=450,
        corrected_unit="W",
        reason="450 W row matches the selected product model.",
        selected_manufacturer_model="JAM54D-450",
        model_selection_basis="Reviewer selected the 450 W column matching the product model and SKU.",
    )
    assert review.created_spec_id
    assert review.review_payload["selected_manufacturer_model"] == "JAM54D-450"
    assert review.review_payload["model_selection_basis"].startswith("Reviewer selected")


def test_q3_specs_recalculate_panel_design_readiness(db: Session):
    product = add_product(db, "prd_ready_panel")
    datasheet = archive_datasheet_bytes(
        db,
        file_name="single_model.pdf",
        data=make_pdf(
            "Maximum Power Pmax 450 W\n"
            "Open Circuit Voltage Voc 49.9 V\n"
            "Maximum Power Voltage Vmp 41.5 V\n"
            "Short Circuit Current Isc 11.4 A\n"
            "Maximum Power Current Imp 10.85 A\n"
            "Dimensions 1762 x 1134 x 30 mm\n"
            "Maximum Series Fuse 25 A\n"
            "Maximum System Voltage 1500 V\n"
        ),
        product_id=product.product_id,
    )
    critical = {"power_stc_w", "length_mm", "width_mm", "voc_v", "vmp_v", "isc_a", "imp_a"}
    candidates = [c for c in list_candidates(db, datasheet_id=datasheet.datasheet_id) if c.field_name in critical]
    assert {c.field_name for c in candidates} == critical

    for candidate in candidates:
        review_candidate(db, candidate.candidate_id, action="approve", reviewer="technical_reviewer")

    report = recalculate_product_design_readiness(db, product.product_id)
    refreshed = db.get(Product, product.product_id)
    assert report["design_ready"] is True
    assert report["missing_fields"] == []
    assert refreshed.design_ready is True
    assert refreshed.quality_level == "Q3_reviewed"
    assert db.query(ProductSpec).filter(ProductSpec.product_id == product.product_id).count() >= len(critical)


def test_review_queue_v2_groups_by_product_and_datasheet(db: Session):
    product = add_product(db, "prd_grouped_panel")
    datasheet = archive_datasheet_bytes(
        db,
        file_name="grouped.pdf",
        data=make_pdf("Maximum Power Pmax 450 W\nOpen Circuit Voltage Voc 49.9 V\nDimensions 1762 x 1134 x 30 mm"),
        product_id=product.product_id,
    )
    queue = list_datasheet_review_queue_v2(db)
    assert len(queue) == 1
    group = queue[0]
    assert group["product_id"] == product.product_id
    assert group["datasheet_id"] == datasheet.datasheet_id
    assert group["candidate_count"] >= 3
    assert "power_stc_w" in group["fields"]
    assert group["review_instructions"].startswith("Approve only exact")

    table_preview = get_datasheet_table_preview(db, datasheet.datasheet_id)
    assert table_preview["status"] in {"has_tables", "no_tables_detected"}
    assert table_preview["datasheet_id"] == datasheet.datasheet_id


def test_blank_pdf_gets_ocr_placeholder_status(db: Session):
    datasheet = archive_datasheet_bytes(
        db,
        file_name="blank_scan_placeholder.pdf",
        data=make_pdf(None),
        product_id=None,
    )
    assert list_candidates(db, datasheet_id=datasheet.datasheet_id) == []
    status = get_datasheet_ocr_status(db, datasheet.datasheet_id)
    assert status["ocr_status"] == "needs_ocr_placeholder"
    assert "OCR" in status["next_action"]

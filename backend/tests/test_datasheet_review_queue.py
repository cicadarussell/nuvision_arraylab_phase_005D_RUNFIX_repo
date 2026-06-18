from __future__ import annotations

from io import BytesIO

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.db_models import Base, Product, ProductSpec
from app.services.datasheet_review import (
    DatasheetWorkflowError,
    archive_datasheet_bytes,
    list_candidates,
    list_datasheets,
    list_reviewed_specs,
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


def make_test_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "JA Solar Test Module Datasheet\n"
        "Maximum Power Pmax 450 W\n"
        "Open Circuit Voltage Voc 49.9 V\n"
        "Maximum Power Voltage Vmp 41.5 V\n"
        "Short Circuit Current Isc 11.4 A\n"
        "Maximum Power Current Imp 10.85 A\n"
        "Dimensions 1762 x 1134 x 30 mm\n"
        "Maximum Series Fuse 25 A\n"
        "Maximum System Voltage 1500 V\n"
        "Module Weight 22.0 kg\n",
    )
    out = BytesIO()
    doc.save(out)
    doc.close()
    return out.getvalue()


def add_product(db: Session) -> Product:
    product = Product(
        product_id="prd_panel_test",
        manufacturer="JA Solar",
        manufacturer_model="JAM54D-test",
        category="panel",
        title="JA Solar test panel",
        status="active",
        quality_level="Q0_scraped",
        design_ready=False,
    )
    db.add(product)
    db.commit()
    return product


def test_archive_datasheet_extracts_candidates_but_not_q3_specs(db: Session):
    product = add_product(db)
    datasheet = archive_datasheet_bytes(
        db,
        file_name="ja_test_datasheet.pdf",
        data=make_test_pdf(),
        product_id=product.product_id,
        source_url="https://manufacturer.example/datasheet.pdf",
        uploaded_by="tester",
    )
    assert datasheet.status == "parsed"
    assert datasheet.page_count == 1
    assert datasheet.file_hash_sha256
    assert datasheet.text_hash_sha256

    fields = {c.field_name for c in list_candidates(db, datasheet_id=datasheet.datasheet_id)}
    assert {"power_stc_w", "length_mm", "width_mm", "voc_v", "vmp_v", "isc_a", "imp_a"}.issubset(fields)
    assert db.query(ProductSpec).count() == 0


def test_human_review_promotes_candidate_to_q3_product_spec(db: Session):
    product = add_product(db)
    datasheet = archive_datasheet_bytes(db, file_name="panel.pdf", data=make_test_pdf(), product_id=product.product_id)
    power = next(c for c in list_candidates(db, datasheet_id=datasheet.datasheet_id) if c.field_name == "power_stc_w")

    review = review_candidate(db, power.candidate_id, action="approve", reviewer="technical_reviewer")
    assert review.created_spec_id

    specs = list_reviewed_specs(db, product.product_id)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.field_name == "power_stc_w"
    assert spec.value_number == 450
    assert spec.unit == "W"
    assert spec.quality_level == "Q3_reviewed"
    assert spec.source_type == "manufacturer_datasheet"
    assert spec.source_file_hash_sha256 == datasheet.file_hash_sha256


def test_unassigned_reviewer_cannot_promote_candidate(db: Session):
    product = add_product(db)
    datasheet = archive_datasheet_bytes(db, file_name="panel.pdf", data=make_test_pdf(), product_id=product.product_id)
    power = next(c for c in list_candidates(db, datasheet_id=datasheet.datasheet_id) if c.field_name == "power_stc_w")
    with pytest.raises(DatasheetWorkflowError):
        review_candidate(db, power.candidate_id, action="approve", reviewer="unassigned_reviewer")
    assert db.query(ProductSpec).count() == 0


def test_reviewed_candidate_cannot_be_reviewed_twice(db: Session):
    product = add_product(db)
    datasheet = archive_datasheet_bytes(db, file_name="panel.pdf", data=make_test_pdf(), product_id=product.product_id)
    power = next(c for c in list_candidates(db, datasheet_id=datasheet.datasheet_id) if c.field_name == "power_stc_w")
    review_candidate(db, power.candidate_id, action="approve", reviewer="technical_reviewer")
    with pytest.raises(DatasheetWorkflowError):
        review_candidate(db, power.candidate_id, action="reject", reviewer="technical_reviewer")


def test_duplicate_datasheet_hash_returns_existing_archive(db: Session):
    product = add_product(db)
    data = make_test_pdf()
    first = archive_datasheet_bytes(db, file_name="one.pdf", data=data, product_id=product.product_id)
    second = archive_datasheet_bytes(db, file_name="two.pdf", data=data, product_id=product.product_id)
    assert first.datasheet_id == second.datasheet_id
    assert len(list_datasheets(db)) == 1

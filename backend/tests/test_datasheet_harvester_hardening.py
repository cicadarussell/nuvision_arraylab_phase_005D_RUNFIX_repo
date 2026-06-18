from __future__ import annotations

from io import BytesIO

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.db_models import Base, Product, ProductSpec
from app.services.datasheet_review import (
    DatasheetWorkflowError,
    archive_datasheet_bytes,
    batch_review_candidates,
    create_or_update_source_domain,
    list_candidates,
    list_datasheet_review_queue,
    list_download_jobs,
    queue_datasheet_download,
    review_candidate,
    validate_datasheet_source_url,
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


def make_pdf(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    out = BytesIO()
    doc.save(out)
    doc.close()
    return out.getvalue()


def add_product(db: Session) -> Product:
    product = Product(
        product_id="prd_harden_panel",
        manufacturer="JA Solar",
        manufacturer_model="JAM-test",
        category="panel",
        title="Hardening test panel",
        status="active",
        quality_level="Q0_scraped",
        design_ready=False,
    )
    db.add(product)
    db.commit()
    return product


def test_datasheet_source_registry_blocks_unapproved_domains(db: Session):
    report = validate_datasheet_source_url(db, "https://unknown.example/panel.pdf")
    assert report["status"] == "blocked"
    assert any(issue["code"] == "DATASHEET_SOURCE_DOMAIN_NOT_APPROVED" for issue in report["issues"])

    create_or_update_source_domain(db, "manufacturer.example", created_by="tester")
    ok = validate_datasheet_source_url(db, "https://manufacturer.example/panel.pdf")
    assert ok["status"] == "ok"


def test_datasheet_download_job_requires_approved_domain(db: Session):
    product = add_product(db)
    with pytest.raises(DatasheetWorkflowError):
        queue_datasheet_download(db, source_url="https://random.example/panel.pdf", product_id=product.product_id)

    create_or_update_source_domain(db, "manufacturer.example", created_by="tester")
    job = queue_datasheet_download(db, source_url="https://manufacturer.example/panel.pdf", product_id=product.product_id, requested_by="tester")
    assert job.status == "queued"
    assert job.source_domain == "manufacturer.example"
    assert len(list_download_jobs(db)) == 1


def test_multi_model_datasheet_creates_blocked_conflict_candidate(db: Session):
    product = add_product(db)
    pdf = make_pdf(
        "Multi model module datasheet\n"
        "Maximum Power Pmax 440 W\n"
        "Maximum Power Pmax 450 W\n"
        "Open Circuit Voltage Voc 49.9 V\n"
        "Dimensions 1762 x 1134 x 30 mm\n"
    )
    ds = archive_datasheet_bytes(db, file_name="multi.pdf", data=pdf, product_id=product.product_id)
    candidates = list_candidates(db, datasheet_id=ds.datasheet_id)
    power = next(c for c in candidates if c.field_name == "power_stc_w")
    assert power.status == "needs_model_review"
    assert power.validation_report["status"] == "blocked"
    assert any(i["code"] == "MULTI_MODEL_DATASHEET_CONFLICT" for i in power.validation_report["issues"])

    with pytest.raises(DatasheetWorkflowError):
        review_candidate(db, power.candidate_id, action="approve", reviewer="technical_reviewer")

    review = review_candidate(
        db,
        power.candidate_id,
        action="approve",
        reviewer="technical_reviewer",
        corrected_value_number=450,
        corrected_unit="W",
        reason="Product model is the 450 W variant on the manufacturer datasheet.",
        selected_manufacturer_model="JAM-test-450",
        model_selection_basis="Reviewed exact 450 W row in multi-model table/text.",
    )
    assert review.created_spec_id
    assert db.query(ProductSpec).count() == 1


def test_batch_approve_blocks_conflicted_candidates(db: Session):
    product = add_product(db)
    pdf = make_pdf("Maximum Power Pmax 440 W\nMaximum Power Pmax 450 W\nOpen Circuit Voltage Voc 49.9 V\n")
    ds = archive_datasheet_bytes(db, file_name="multi.pdf", data=pdf, product_id=product.product_id)
    ids = [c.candidate_id for c in list_candidates(db, datasheet_id=ds.datasheet_id)]
    with pytest.raises(DatasheetWorkflowError):
        batch_review_candidates(db, ids, action="approve", reviewer="technical_reviewer")

    # Batch reject is allowed because rejection does not create engineering truth.
    records = batch_review_candidates(db, ids, action="reject", reviewer="technical_reviewer", reason="Wrong model family")
    assert len(records) == len(ids)


def test_review_queue_groups_candidates_by_product(db: Session):
    product = add_product(db)
    pdf = make_pdf("Maximum Power Pmax 450 W\nOpen Circuit Voltage Voc 49.9 V\nDimensions 1762 x 1134 x 30 mm\n")
    archive_datasheet_bytes(db, file_name="panel.pdf", data=pdf, product_id=product.product_id)
    queue = list_datasheet_review_queue(db)
    assert len(queue) == 1
    assert queue[0]["product_id"] == product.product_id
    assert queue[0]["candidate_count"] >= 3

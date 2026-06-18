from __future__ import annotations

from io import BytesIO

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.db_models import Base, Product
from app.services.datasheet_downloader import FetchResult, datasheet_worker_debug, list_ocr_jobs, run_datasheet_download_job
from app.services.datasheet_review import create_or_update_source_domain, list_candidates, queue_datasheet_download

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


def add_product(db: Session) -> Product:
    product = Product(
        product_id="prd_download_panel",
        manufacturer="JA Solar",
        manufacturer_model="JAM-test",
        category="panel",
        title="Download worker test panel",
        status="active",
        quality_level="Q0_scraped",
        design_ready=False,
    )
    db.add(product)
    db.commit()
    return product


def test_download_worker_archives_pdf_and_extracts_candidates(db: Session):
    product = add_product(db)
    create_or_update_source_domain(db, "manufacturer.example", created_by="tester")
    job = queue_datasheet_download(
        db,
        source_url="https://manufacturer.example/panel.pdf",
        product_id=product.product_id,
        requested_by="tester",
    )

    pdf = make_pdf("Maximum Power Pmax 450 W\nOpen Circuit Voltage Voc 49.9 V\nDimensions 1762 x 1134 x 30 mm")
    result = run_datasheet_download_job(
        db,
        job.job_id,
        fetcher=lambda url: FetchResult(200, pdf, "application/pdf", final_url=url),
        run_by="test_worker",
    )

    assert result.status == "succeeded"
    assert result.datasheet_id
    candidates = list_candidates(db, datasheet_id=result.datasheet_id)
    assert {c.field_name for c in candidates} >= {"power_stc_w", "voc_v", "length_mm", "width_mm"}


def test_download_worker_marks_failures_without_archiving_bad_content(db: Session):
    add_product(db)
    create_or_update_source_domain(db, "manufacturer.example", created_by="tester")
    job = queue_datasheet_download(db, source_url="https://manufacturer.example/not-a-pdf.pdf", requested_by="tester")

    result = run_datasheet_download_job(
        db,
        job.job_id,
        fetcher=lambda url: FetchResult(200, b"<html>not a pdf</html>", "text/html", final_url=url),
        run_by="test_worker",
    )

    assert result.status == "failed"
    assert result.retry_count == 1
    assert "not a PDF" in (result.error_message or "")


def test_blank_download_creates_ocr_queue_but_not_truth(db: Session):
    create_or_update_source_domain(db, "manufacturer.example", created_by="tester")
    job = queue_datasheet_download(db, source_url="https://manufacturer.example/scanned.pdf", requested_by="tester")
    blank_pdf = make_pdf(None)

    result = run_datasheet_download_job(
        db,
        job.job_id,
        fetcher=lambda url: FetchResult(200, blank_pdf, "application/pdf", final_url=url),
        run_by="test_worker",
    )

    assert result.status == "succeeded"
    assert result.datasheet_id
    assert list_candidates(db, datasheet_id=result.datasheet_id) == []
    ocr_jobs = list_ocr_jobs(db)
    assert len(ocr_jobs) == 1
    assert ocr_jobs[0].status == "queued"
    assert "Q2" in ocr_jobs[0].validation_report["truth_boundary"]


def test_datasheet_worker_debug_summarises_queue(db: Session):
    create_or_update_source_domain(db, "manufacturer.example", created_by="tester")
    queue_datasheet_download(db, source_url="https://manufacturer.example/panel.pdf", requested_by="tester")
    report = datasheet_worker_debug(db)
    assert report["download_jobs_total"] == 1
    assert report["download_jobs_by_status"]["queued"] == 1
    assert report["truth_boundary"].startswith("Downloaded/OCR")

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import DatasheetDownloadJob, DatasheetFile, DatasheetOcrJob
from app.services.datasheet_review import DatasheetWorkflowError, archive_datasheet_bytes, validate_datasheet_source_url
from app.services.hash_utils import stable_json_hash


@dataclass(frozen=True)
class FetchResult:
    """Small fetch result interface so tests can inject fake network responses.

    No live internet is required for unit tests. The default fetcher uses httpx with a short timeout
    only when a human deliberately runs the worker against approved domains.
    """

    status_code: int
    content: bytes
    content_type: str | None = None
    final_url: str | None = None


class DatasheetDownloadWorkerError(ValueError):
    """Raised when a queued datasheet job cannot be safely processed."""


def _new_ocr_job_id(datasheet_id: str, reason: str) -> str:
    return f"ocr_{stable_json_hash({'datasheet_id': datasheet_id, 'reason': reason})[:14]}"


def _default_fetcher(url: str) -> FetchResult:
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": "NuVision-ArrayLab-DatasheetWorker/0.2D"})
        return FetchResult(
            status_code=response.status_code,
            content=response.content,
            content_type=response.headers.get("content-type"),
            final_url=str(response.url),
        )


def _file_name_from_url(url: str, job_id: str) -> str:
    path = urlparse(url).path.rstrip("/")
    raw = path.rsplit("/", 1)[-1] if path else ""
    name = raw or f"{job_id}.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name.replace(" ", "_")


def _looks_like_pdf(data: bytes, content_type: str | None) -> bool:
    if data.startswith(b"%PDF"):
        return True
    if content_type and "pdf" in content_type.lower() and len(data) > 32:
        return True
    return False


def create_ocr_job_if_needed(
    db: Session,
    datasheet: DatasheetFile,
    *,
    requested_by: str | None = None,
    reason: str | None = None,
) -> DatasheetOcrJob | None:
    report = datasheet.extraction_report or {}
    ocr_status = report.get("ocr_status")
    if ocr_status != "needs_ocr_placeholder":
        return None
    ocr_reason = reason or "Datasheet has no native text; OCR is required before further candidate extraction."
    job_id = _new_ocr_job_id(datasheet.datasheet_id, ocr_reason)
    existing = db.get(DatasheetOcrJob, job_id)
    if existing is not None:
        return existing
    row = DatasheetOcrJob(
        ocr_job_id=job_id,
        datasheet_id=datasheet.datasheet_id,
        status="queued",
        reason=ocr_reason,
        engine="not_implemented_placeholder",
        validation_report={
            "status": "queued",
            "truth_boundary": "OCR output may create Q2 candidates only; Q3 human review is still required.",
        },
        requested_by=requested_by,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def run_datasheet_download_job(
    db: Session,
    job_id: str,
    *,
    fetcher=None,
    run_by: str | None = "local_worker",
) -> DatasheetDownloadJob:
    job = db.get(DatasheetDownloadJob, job_id)
    if job is None:
        raise DatasheetDownloadWorkerError(f"Datasheet download job {job_id} not found.")
    if job.status == "succeeded":
        return job
    if job.status not in {"queued", "failed"}:
        raise DatasheetDownloadWorkerError(f"Job {job_id} is {job.status}; only queued/failed jobs can be run.")

    validation = validate_datasheet_source_url(db, job.source_url)
    if validation["status"] == "blocked":
        job.status = "failed"
        job.error_message = f"Source validation failed before fetch: {validation['issues']}"
        job.validation_report = validation
        job.retry_count = (job.retry_count or 0) + 1
        job.last_attempt_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(job)
        return job

    job.status = "running"
    job.error_message = None
    job.retry_count = (job.retry_count or 0) + 1
    job.last_attempt_at = datetime.now(UTC)
    job.updated_at = datetime.now(UTC)
    db.commit()

    try:
        result = (fetcher or _default_fetcher)(job.source_url)
        if result.status_code < 200 or result.status_code >= 300:
            raise DatasheetDownloadWorkerError(f"HTTP {result.status_code} while fetching datasheet.")
        if not _looks_like_pdf(result.content, result.content_type):
            raise DatasheetDownloadWorkerError("Fetched content is not a PDF; refusing to archive as manufacturer evidence.")
        datasheet = archive_datasheet_bytes(
            db,
            file_name=_file_name_from_url(result.final_url or job.source_url, job.job_id),
            data=result.content,
            product_id=job.product_id,
            source_url=result.final_url or job.source_url,
            uploaded_by=f"download_worker:{run_by or 'unknown'}",
        )
        create_ocr_job_if_needed(db, datasheet, requested_by=run_by)
        job.datasheet_id = datasheet.datasheet_id
        job.status = "succeeded"
        job.error_message = None
        job.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(job)
        return job
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)
        job.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(job)
        return job


def list_ocr_jobs(db: Session) -> list[DatasheetOcrJob]:
    return list(db.scalars(select(DatasheetOcrJob).order_by(DatasheetOcrJob.created_at.desc())))


def datasheet_worker_debug(db: Session, *, stuck_after_minutes: int = 20) -> dict:
    jobs = list(db.scalars(select(DatasheetDownloadJob)))
    ocr_jobs = list_ocr_jobs(db)
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=stuck_after_minutes)
    stuck = []
    for job in jobs:
        if job.status == "running" and job.last_attempt_at is not None:
            # SQLite may return naive datetimes. Make it comparable without a tantrum.
            attempt = job.last_attempt_at if job.last_attempt_at.tzinfo else job.last_attempt_at.replace(tzinfo=UTC)
            if attempt < cutoff:
                stuck.append(job.job_id)
    by_status: dict[str, int] = {}
    for job in jobs:
        by_status[job.status] = by_status.get(job.status, 0) + 1
    return {
        "status": "ok" if not stuck else "warning",
        "download_jobs_total": len(jobs),
        "download_jobs_by_status": by_status,
        "stuck_running_job_ids": stuck,
        "ocr_jobs_total": len(ocr_jobs),
        "ocr_jobs_by_status": {state: sum(1 for job in ocr_jobs if job.status == state) for state in sorted({job.status for job in ocr_jobs})},
        "truth_boundary": "Downloaded/OCR extracted values are Q2 candidates only until Q3 human review.",
    }

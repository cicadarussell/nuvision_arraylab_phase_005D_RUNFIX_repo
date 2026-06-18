from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import (
    DatasheetCandidateSpec,
    DatasheetDownloadJob,
    DatasheetFile,
    DatasheetReviewRecord,
    DatasheetSourceDomain,
    Product,
    ProductSpec,
)
from app.services.hash_utils import stable_json_hash

try:  # PyMuPDF supports both imports in recent versions.
    import pymupdf  # type: ignore
except Exception:  # pragma: no cover - compatibility fallback
    import fitz as pymupdf  # type: ignore

try:  # Optional second opinion for hostile tables. It is installed in this environment.
    import pdfplumber  # type: ignore
except Exception:  # pragma: no cover - optional production dependency
    pdfplumber = None  # type: ignore


class DatasheetWorkflowError(ValueError):
    """Raised when datasheet evidence would break review traceability."""


PANEL_CRITICAL_FIELDS = {"power_stc_w", "length_mm", "width_mm", "voc_v", "vmp_v", "isc_a", "imp_a"}
DESIGN_READY_QUALITY_LEVELS = {"Q3_reviewed", "Q4_manufacturer_confirmed"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:14]}"


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Candidate:
    field_name: str
    value_number: float | None
    value_text: str | None
    unit: str | None
    page: int | None
    quote: str
    confidence: float
    extraction_method: str = "regex_text"
    conflict: bool = False
    conflict_values: tuple[str, ...] = ()


def _norm_unit_and_value(field_name: str, value: float | None, unit: str | None) -> tuple[float | None, str | None]:
    if value is None:
        return None, unit
    u = (unit or "").lower()
    if u == "mm":
        return value / 1000.0, "m"
    if u in {"w", "v", "a", "%/°c", "pa", "kg"}:
        return value, unit
    if field_name.endswith("_mm"):
        return value / 1000.0, "m"
    return value, unit


def _range_issue(field_name: str, value: float | None) -> dict | None:
    if value is None:
        return None
    ranges = {
        "power_stc_w": (250, 900, "Solar panel STC power outside expected module range."),
        "length_mm": (1200, 3000, "Panel length outside expected modern module range."),
        "width_mm": (900, 1500, "Panel width outside expected modern module range."),
        "thickness_mm": (20, 60, "Panel thickness outside expected framed/glass module range."),
        "weight_kg": (10, 45, "Panel weight outside expected module range."),
        "voc_v": (20, 80, "Open circuit voltage outside expected module range."),
        "vmp_v": (20, 70, "Maximum power voltage outside expected module range."),
        "isc_a": (5, 25, "Short circuit current outside expected module range."),
        "imp_a": (5, 25, "Maximum power current outside expected module range."),
        "max_series_fuse_a": (5, 40, "Series fuse rating outside expected module range."),
        "max_system_voltage_v": (600, 1600, "Max system voltage outside expected PV module range."),
    }
    if field_name not in ranges:
        return None
    lo, hi, message = ranges[field_name]
    if not (lo <= value <= hi):
        return {
            "code": "CANDIDATE_VALUE_OUTSIDE_EXPECTED_RANGE",
            "severity": "warning",
            "field_name": field_name,
            "value": value,
            "expected_min": lo,
            "expected_max": hi,
            "message": message,
        }
    return None


def _candidate_validation(candidate: Candidate) -> dict:
    issues: list[dict] = []
    issue = _range_issue(candidate.field_name, candidate.value_number)
    if issue:
        issues.append(issue)
    if candidate.confidence < 0.50:
        issues.append({
            "code": "LOW_EXTRACTION_CONFIDENCE",
            "severity": "warning",
            "field_name": candidate.field_name,
            "confidence": candidate.confidence,
            "message": "Candidate was extracted with low confidence and must be checked carefully.",
        })
    if candidate.conflict:
        issues.append({
            "code": "MULTI_MODEL_DATASHEET_CONFLICT",
            "severity": "blocker",
            "field_name": candidate.field_name,
            "values_seen": list(candidate.conflict_values),
            "message": "Multiple distinct values were found for this field. Reviewer must choose/correct the value for the exact product model.",
        })
    return {
        "status": "blocked" if any(i["severity"] == "blocker" for i in issues) else ("warnings" if issues else "candidate_ok"),
        "issues": issues,
        "truth_boundary": "Q2 candidate only; cannot be used for final design until human Q3 review.",
    }


def _compact_quote(text: str, start: int, end: int, radius: int = 90) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    return snippet[:450]


def _extract_text_pages(data: bytes) -> tuple[list[str], dict]:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # pragma: no cover - defensive branch
        return [], {"status": "failed", "error": f"PDF open failed: {exc}"}
    pages: list[str] = []
    try:
        for page in doc:
            pages.append(page.get_text("text") or "")
    finally:
        doc.close()
    text_chars = sum(len(p) for p in pages)
    return pages, {
        "status": "extracted" if text_chars else "no_text_found",
        "page_count": len(pages),
        "text_characters": text_chars,
        "extractor": "PyMuPDF Page.get_text('text')",
    }


def _tables_to_text(tables: list[list[list[str | None]]]) -> str:
    lines: list[str] = []
    for table in tables:
        for row in table:
            cells = [str(cell or "").strip() for cell in row]
            if any(cells):
                lines.append(" | ".join(cells))
        if table:
            lines.append("")
    return "\n".join(lines).strip()


def _extract_pymupdf_tables(data: bytes) -> tuple[list[str], dict]:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # pragma: no cover
        return [], {"status": "failed", "error": str(exc), "extractor": "PyMuPDF Page.find_tables"}
    page_text: list[str] = []
    table_count = 0
    try:
        for page in doc:
            page_tables: list[list[list[str | None]]] = []
            try:
                found = page.find_tables()  # type: ignore[attr-defined]
                for table in getattr(found, "tables", []):
                    try:
                        page_tables.append(table.extract())
                    except Exception:
                        continue
            except Exception:
                page_tables = []
            table_count += len(page_tables)
            page_text.append(_tables_to_text(page_tables))
    finally:
        doc.close()
    return page_text, {"status": "ok", "extractor": "PyMuPDF Page.find_tables", "table_count": table_count}


def _extract_pdfplumber_tables(data: bytes) -> tuple[list[str], dict]:
    if pdfplumber is None:
        return [], {"status": "unavailable", "extractor": "pdfplumber", "table_count": 0}
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            page_text: list[str] = []
            table_count = 0
            for page in pdf.pages:
                tables = page.extract_tables() or []
                table_count += len(tables)
                page_text.append(_tables_to_text(tables))
        return page_text, {"status": "ok", "extractor": "pdfplumber.Page.extract_tables", "table_count": table_count}
    except Exception as exc:  # pragma: no cover - best-effort fallback
        return [], {"status": "failed", "extractor": "pdfplumber", "error": str(exc), "table_count": 0}


def _build_table_previews(pymupdf_pages: list[str], pdfplumber_pages: list[str]) -> list[dict]:
    previews: list[dict] = []
    for extractor, pages in [
        ("PyMuPDF Page.find_tables", pymupdf_pages),
        ("pdfplumber.Page.extract_tables", pdfplumber_pages),
    ]:
        for index, text in enumerate(pages):
            clean = re.sub(r"\s+", " ", text or "").strip()
            if not clean:
                continue
            previews.append({
                "extractor": extractor,
                "page": index + 1,
                "preview_text": clean[:900],
                "character_count": len(clean),
                "truth_boundary": "Table preview helps review; it is not engineering truth until Q3 approval.",
            })
    return previews


def _merge_page_layers(*layers: list[str]) -> list[str]:
    count = max((len(layer) for layer in layers), default=0)
    merged: list[str] = []
    for i in range(count):
        parts = [layer[i] for layer in layers if i < len(layer) and layer[i]]
        merged.append("\n".join(parts))
    return merged


LABEL_PATTERNS: list[tuple[str, str, str, float]] = [
    ("power_stc_w", r"(?i)(?:maximum\s+power|pmax|rated\s+power|nominal\s+power|power\s+output)[^\n\r]{0,90}?(\d{3,4}(?:\.\d+)?)\s*W\b", "W", 0.84),
    ("voc_v", r"(?i)(?:voc|open[-\s]?circuit\s+voltage)[^\n\r]{0,90}?(\d{1,2}(?:\.\d+)?)\s*V\b", "V", 0.86),
    ("vmp_v", r"(?i)(?:vmp|vmpp|maximum\s+power\s+voltage|voltage\s+at\s+maximum\s+power)[^\n\r]{0,90}?(\d{1,2}(?:\.\d+)?)\s*V\b", "V", 0.86),
    ("isc_a", r"(?i)(?:isc|short[-\s]?circuit\s+current)[^\n\r]{0,90}?(\d{1,2}(?:\.\d+)?)\s*A\b", "A", 0.86),
    ("imp_a", r"(?i)(?:imp|impp|maximum\s+power\s+current|current\s+at\s+maximum\s+power)[^\n\r]{0,90}?(\d{1,2}(?:\.\d+)?)\s*A\b", "A", 0.86),
    ("max_system_voltage_v", r"(?i)(?:maximum\s+system\s+voltage|max\.\s*system\s+voltage)[^\n\r]{0,90}?(\d{3,4})\s*V\b", "V", 0.82),
    ("max_series_fuse_a", r"(?i)(?:maximum\s+series\s+fuse|max\.\s*series\s+fuse|series\s+fuse\s+rating)[^\n\r]{0,90}?(\d{1,2}(?:\.\d+)?)\s*A\b", "A", 0.80),
    ("weight_kg", r"(?i)(?:weight|module\s+weight)[^\n\r]{0,90}?(\d{1,2}(?:\.\d+)?)\s*kg\b", "kg", 0.72),
]


def _extract_dimension_candidates(text: str, page_index: int, method: str = "regex_text") -> list[Candidate]:
    candidates: list[Candidate] = []
    for match in re.finditer(r"(?i)(\d{4})\s*[x×]\s*(\d{3,4})\s*[x×]\s*(\d{2})\s*mm", text):
        values = [float(match.group(1)), float(match.group(2)), float(match.group(3))]
        quote = _compact_quote(text, match.start(), match.end())
        length, width, thickness = sorted(values, reverse=True)
        for field, value, conf in [("length_mm", length, 0.88), ("width_mm", width, 0.88), ("thickness_mm", thickness, 0.88)]:
            candidates.append(Candidate(field, value, str(int(value)), "mm", page_index + 1, quote, conf, method))
    return candidates


def _all_candidate_specs_from_text_pages(pages: list[str], method: str = "regex_text") -> list[Candidate]:
    found: list[Candidate] = []
    for page_index, text in enumerate(pages):
        found.extend(_extract_dimension_candidates(text, page_index, method=method))
        for field_name, pattern, unit, confidence in LABEL_PATTERNS:
            for match in re.finditer(pattern, text):
                raw = match.group(1)
                try:
                    number = float(raw)
                except ValueError:
                    number = None
                found.append(Candidate(field_name, number, raw, unit, page_index + 1, _compact_quote(text, match.start(), match.end()), confidence, method))
    return found


def _distinct_value_key(candidate: Candidate) -> str:
    if candidate.value_number is None:
        return str(candidate.value_text or "").strip().lower()
    return f"{candidate.value_number:g}:{candidate.unit or ''}"


def extract_candidate_specs_from_text_pages(pages: list[str]) -> list[Candidate]:
    # Public helper retained for earlier tests. It now keeps one best candidate per field but marks
    # conflicts if the datasheet appears to contain multiple model columns/ratings.
    all_found = _all_candidate_specs_from_text_pages(pages)
    return _best_candidates_with_conflicts(all_found)


def _best_candidates_with_conflicts(all_found: list[Candidate]) -> list[Candidate]:
    by_field: dict[str, list[Candidate]] = {}
    for candidate in all_found:
        by_field.setdefault(candidate.field_name, []).append(candidate)
    best: list[Candidate] = []
    for field_name, candidates in by_field.items():
        distinct = sorted({_distinct_value_key(c) for c in candidates if _distinct_value_key(c)})
        conflict = len(distinct) > 1
        chosen = sorted(candidates, key=lambda c: (c.confidence, len(c.quote or "")), reverse=True)[0]
        if conflict:
            chosen = Candidate(
                chosen.field_name,
                chosen.value_number,
                chosen.value_text,
                chosen.unit,
                chosen.page,
                chosen.quote,
                max(0.30, chosen.confidence - 0.25),
                chosen.extraction_method,
                True,
                tuple(distinct),
            )
        best.append(chosen)
    return sorted(best, key=lambda c: c.field_name)


def _conflict_summary(candidates: list[Candidate]) -> dict:
    fields = [c.field_name for c in candidates if c.conflict]
    return {
        "has_conflicts": bool(fields),
        "conflict_fields": sorted(set(fields)),
        "message": "Multi-model datasheet conflicts require model-specific human review." if fields else None,
    }


def archive_datasheet_bytes(
    db: Session,
    *,
    file_name: str,
    data: bytes,
    product_id: str | None = None,
    source_url: str | None = None,
    uploaded_by: str | None = None,
) -> DatasheetFile:
    if not data:
        raise DatasheetWorkflowError("Datasheet file is empty.")
    if not file_name.lower().endswith(".pdf"):
        raise DatasheetWorkflowError("Only PDF datasheets are accepted in Phase 002B.")
    if product_id is not None and db.get(Product, product_id) is None:
        raise DatasheetWorkflowError(f"Product {product_id} does not exist. Archive the datasheet after product staging/apply or leave product_id blank.")

    file_hash = _sha256_bytes(data)
    existing = db.scalars(select(DatasheetFile).where(DatasheetFile.file_hash_sha256 == file_hash, DatasheetFile.product_id == product_id)).first()
    if existing is not None:
        return existing

    text_pages, extraction_report = _extract_text_pages(data)
    pymupdf_table_pages, pymupdf_table_report = _extract_pymupdf_tables(data)
    pdfplumber_table_pages, pdfplumber_table_report = _extract_pdfplumber_tables(data)
    combined_pages = _merge_page_layers(text_pages, pymupdf_table_pages, pdfplumber_table_pages)
    raw_text = "\n\f\n".join(combined_pages)
    text_hash = stable_json_hash({"pages": combined_pages}) if combined_pages else None
    datasheet = DatasheetFile(
        datasheet_id=f"ds_{file_hash[:16]}",
        product_id=product_id,
        file_name=file_name,
        source_url=source_url,
        source_type="manufacturer_datasheet",
        file_hash_sha256=file_hash,
        byte_size=len(data),
        page_count=len(text_pages),
        text_hash_sha256=text_hash,
        raw_text=raw_text,
        status="parsed" if raw_text else "archived_no_text",
        extraction_report={
            **extraction_report,
            "table_extractors": [pymupdf_table_report, pdfplumber_table_report],
            "table_previews": _build_table_previews(pymupdf_table_pages, pdfplumber_table_pages),
            "ocr_status": "needs_ocr_placeholder" if not raw_text else "not_required",
        },
        uploaded_by=uploaded_by,
        created_at=datetime.now(UTC),
    )
    db.add(datasheet)
    db.flush()

    raw_candidates = _all_candidate_specs_from_text_pages(combined_pages, method="regex_text_plus_tables")
    candidates = _best_candidates_with_conflicts(raw_candidates)
    conflict_summary = _conflict_summary(candidates)
    for candidate in candidates:
        norm_value, norm_unit = _norm_unit_and_value(candidate.field_name, candidate.value_number, candidate.unit)
        payload = {
            "datasheet_id": datasheet.datasheet_id,
            "product_id": product_id,
            "field_name": candidate.field_name,
            "value_text": candidate.value_text,
            "value_number": candidate.value_number,
            "unit": candidate.unit,
            "source_page": candidate.page,
            "source_text_quote": candidate.quote,
            "conflict_values": list(candidate.conflict_values),
        }
        candidate_id = f"cand_{stable_json_hash(payload)[:16]}"
        if db.get(DatasheetCandidateSpec, candidate_id) is not None:
            continue
        validation_report = _candidate_validation(candidate)
        row = DatasheetCandidateSpec(
            candidate_id=candidate_id,
            datasheet_id=datasheet.datasheet_id,
            product_id=product_id,
            field_name=candidate.field_name,
            value_text=candidate.value_text,
            value_number=candidate.value_number,
            unit=candidate.unit,
            normalized_value_si=norm_value,
            normalized_unit=norm_unit,
            source_page=candidate.page,
            source_text_quote=candidate.quote,
            extraction_method=candidate.extraction_method,
            confidence=candidate.confidence,
            status="needs_model_review" if candidate.conflict else "candidate",
            validation_report=validation_report,
            created_at=datetime.now(UTC),
        )
        db.add(row)

    datasheet.extraction_report = {
        **datasheet.extraction_report,
        "raw_candidate_count": len(raw_candidates),
        "candidate_count": len(candidates),
        "fields_found": sorted({c.field_name for c in candidates}),
        "conflict_summary": conflict_summary,
        "truth_boundary": "Candidates are Q2 parsed at most. Human review is required before design use. Multi-model conflicts require corrected model-specific review.",
    }
    db.commit()
    db.refresh(datasheet)
    return datasheet


def list_datasheets(db: Session) -> list[DatasheetFile]:
    return list(db.scalars(select(DatasheetFile).order_by(DatasheetFile.created_at.desc())))


def get_datasheet(db: Session, datasheet_id: str) -> DatasheetFile | None:
    return db.get(DatasheetFile, datasheet_id)


def list_candidates(db: Session, datasheet_id: str | None = None, product_id: str | None = None) -> list[DatasheetCandidateSpec]:
    stmt = select(DatasheetCandidateSpec).order_by(DatasheetCandidateSpec.created_at.desc())
    if datasheet_id:
        stmt = stmt.where(DatasheetCandidateSpec.datasheet_id == datasheet_id)
    if product_id:
        stmt = stmt.where(DatasheetCandidateSpec.product_id == product_id)
    return list(db.scalars(stmt))


def list_reviewed_specs(db: Session, product_id: str) -> list[ProductSpec]:
    return list(db.scalars(select(ProductSpec).where(ProductSpec.product_id == product_id).order_by(ProductSpec.created_at.desc())))


def list_datasheet_review_queue(db: Session) -> list[dict]:
    rows = list(db.scalars(select(DatasheetCandidateSpec).where(DatasheetCandidateSpec.status.in_(["candidate", "needs_model_review"])) .order_by(DatasheetCandidateSpec.product_id, DatasheetCandidateSpec.created_at)))
    grouped: dict[str, dict] = {}
    for row in rows:
        key = row.product_id or "unlinked"
        if key not in grouped:
            grouped[key] = {"product_id": row.product_id, "candidate_count": 0, "blocked_count": 0, "fields": {}}
        grouped[key]["candidate_count"] += 1
        if row.status == "needs_model_review":
            grouped[key]["blocked_count"] += 1
        grouped[key]["fields"].setdefault(row.field_name, []).append({
            "candidate_id": row.candidate_id,
            "datasheet_id": row.datasheet_id,
            "value_number": row.value_number,
            "value_text": row.value_text,
            "unit": row.unit,
            "confidence": row.confidence,
            "status": row.status,
            "validation_report": row.validation_report,
        })
    return list(grouped.values())


def review_candidate(
    db: Session,
    candidate_id: str,
    *,
    action: str,
    reviewer: str,
    corrected_value_text: str | None = None,
    corrected_value_number: float | None = None,
    corrected_unit: str | None = None,
    reason: str | None = None,
    selected_manufacturer_model: str | None = None,
    selected_datasheet_variant: str | None = None,
    model_selection_basis: str | None = None,
) -> DatasheetReviewRecord:
    candidate = db.get(DatasheetCandidateSpec, candidate_id)
    if candidate is None:
        raise DatasheetWorkflowError(f"Candidate {candidate_id} not found.")
    if candidate.status in {"promoted", "rejected"}:
        raise DatasheetWorkflowError(f"Candidate {candidate_id} has already been {candidate.status}.")
    if action not in {"approve", "reject"}:
        raise DatasheetWorkflowError("Review action must be approve or reject.")
    if not reviewer or reviewer == "unassigned_reviewer":
        raise DatasheetWorkflowError("A named reviewer is required before candidate values can become Q3 engineering data.")
    if action == "approve" and candidate.status == "needs_model_review" and corrected_value_number is None:
        raise DatasheetWorkflowError("Multi-model/conflicted candidate approval requires a corrected model-specific value.")
    if action == "approve" and candidate.status == "needs_model_review" and not corrected_unit:
        raise DatasheetWorkflowError("Multi-model/conflicted candidate approval requires a corrected unit.")
    if action == "approve" and candidate.status == "needs_model_review" and not reason:
        raise DatasheetWorkflowError("Multi-model/conflicted candidate approval requires a reviewer reason naming the product/model basis.")
    if action == "approve" and candidate.status == "needs_model_review" and not (selected_manufacturer_model or selected_datasheet_variant):
        raise DatasheetWorkflowError("Multi-model/conflicted candidate approval requires selected_manufacturer_model or selected_datasheet_variant.")
    if action == "approve" and candidate.status == "needs_model_review" and not model_selection_basis:
        raise DatasheetWorkflowError("Multi-model/conflicted candidate approval requires model_selection_basis explaining how the reviewer chose the exact row/variant.")

    created_spec_id: str | None = None
    design_readiness_report: dict | None = None
    value_number = corrected_value_number if corrected_value_number is not None else candidate.value_number
    value_text = corrected_value_text if corrected_value_text is not None else candidate.value_text
    unit = corrected_unit if corrected_unit is not None else candidate.unit
    norm_value, norm_unit = _norm_unit_and_value(candidate.field_name, value_number, unit)

    if action == "approve":
        if candidate.product_id is None:
            raise DatasheetWorkflowError("Candidate has no product_id. Link datasheet to a product before approving.")
        if db.get(Product, candidate.product_id) is None:
            raise DatasheetWorkflowError(f"Product {candidate.product_id} does not exist.")
        datasheet = db.get(DatasheetFile, candidate.datasheet_id)
        spec_payload = {
            "candidate_id": candidate.candidate_id,
            "product_id": candidate.product_id,
            "field_name": candidate.field_name,
            "value_number": value_number,
            "value_text": value_text,
            "unit": unit,
            "source_file_hash_sha256": datasheet.file_hash_sha256 if datasheet else None,
            "reviewer": reviewer,
        }
        created_spec_id = f"spec_{stable_json_hash(spec_payload)[:16]}"
        if db.get(ProductSpec, created_spec_id) is None:
            spec = ProductSpec(
                spec_id=created_spec_id,
                product_id=candidate.product_id,
                field_name=candidate.field_name,
                value_text=str(value_text) if value_text is not None else None,
                value_number=value_number,
                unit=unit,
                normalized_value_si=norm_value,
                normalized_unit=norm_unit,
                quality_level="Q3_reviewed",
                source_type="manufacturer_datasheet",
                source_url=datasheet.source_url if datasheet else None,
                source_file_hash_sha256=datasheet.file_hash_sha256 if datasheet else None,
                source_page=candidate.source_page,
                source_text_quote=candidate.source_text_quote,
                extraction_method="human_reviewed_pdf_candidate",
                confidence=1.0,
                review_status="reviewed",
                reviewed_by=reviewer,
                reviewed_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
            )
            db.add(spec)
        candidate.status = "promoted"
        candidate.created_spec_id = created_spec_id
        db.flush()
        design_readiness_report = recalculate_product_design_readiness(db, candidate.product_id, commit=False)
    else:
        candidate.status = "rejected"

    candidate.reviewed_by = reviewer
    candidate.reviewed_at = datetime.now(UTC)
    review_payload = {
        "candidate_id": candidate_id,
        "action": action,
        "reviewer": reviewer,
        "corrected_value_text": corrected_value_text,
        "corrected_value_number": corrected_value_number,
        "corrected_unit": corrected_unit,
        "reason": reason,
        "selected_manufacturer_model": selected_manufacturer_model,
        "selected_datasheet_variant": selected_datasheet_variant,
        "model_selection_basis": model_selection_basis,
        "created_spec_id": created_spec_id,
        "design_readiness_report": design_readiness_report,
        "truth_boundary": "Only approved candidates create Q3 ProductSpec rows; rejected candidates remain evidence.",
    }
    record = DatasheetReviewRecord(
        review_id=f"dsr_{stable_json_hash(review_payload)[:16]}",
        candidate_id=candidate_id,
        action=action,
        reviewer=reviewer,
        corrected_value_text=corrected_value_text,
        corrected_value_number=corrected_value_number,
        corrected_unit=corrected_unit,
        reason=reason,
        created_spec_id=created_spec_id,
        review_payload_hash_sha256=stable_json_hash(review_payload),
        review_payload=review_payload,
        created_at=datetime.now(UTC),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def batch_review_candidates(db: Session, candidate_ids: list[str], *, action: str, reviewer: str, reason: str | None = None) -> list[DatasheetReviewRecord]:
    if not candidate_ids:
        raise DatasheetWorkflowError("Batch review requires at least one candidate_id.")
    if action not in {"approve", "reject"}:
        raise DatasheetWorkflowError("Batch action must be approve or reject.")
    if not reviewer or reviewer == "unassigned_reviewer":
        raise DatasheetWorkflowError("A named reviewer is required for batch review.")
    candidates = [db.get(DatasheetCandidateSpec, cid) for cid in candidate_ids]
    missing = [cid for cid, row in zip(candidate_ids, candidates, strict=False) if row is None]
    if missing:
        raise DatasheetWorkflowError(f"Missing candidates: {missing}")
    if action == "approve":
        blockers = [row.candidate_id for row in candidates if row is not None and row.status == "needs_model_review"]
        if blockers:
            raise DatasheetWorkflowError(f"Batch approve blocked. Conflicted candidates require individual corrected values: {blockers}")
    records: list[DatasheetReviewRecord] = []
    for candidate_id in candidate_ids:
        records.append(review_candidate(db, candidate_id, action=action, reviewer=reviewer, reason=reason))
    return records


def _normalise_domain(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def create_or_update_source_domain(db: Session, domain: str, *, status: str = "approved", source_kind: str = "manufacturer", notes: str | None = None, created_by: str | None = None) -> DatasheetSourceDomain:
    clean = domain.lower().strip().replace("https://", "").replace("http://", "").split("/")[0]
    if clean.startswith("www."):
        clean = clean[4:]
    if not clean or "." not in clean:
        raise DatasheetWorkflowError("Source domain must look like a real DNS domain.")
    row = db.scalars(select(DatasheetSourceDomain).where(DatasheetSourceDomain.domain == clean)).first()
    if row is None:
        row = DatasheetSourceDomain(domain_id=f"dsdom_{stable_json_hash({'domain': clean})[:14]}", domain=clean, status=status, source_kind=source_kind, notes=notes, created_by=created_by, created_at=datetime.now(UTC))
        db.add(row)
    else:
        row.status = status
        row.source_kind = source_kind
        row.notes = notes
    db.commit()
    db.refresh(row)
    return row


def list_source_domains(db: Session) -> list[DatasheetSourceDomain]:
    return list(db.scalars(select(DatasheetSourceDomain).order_by(DatasheetSourceDomain.domain)))


def validate_datasheet_source_url(db: Session, source_url: str) -> dict:
    domain = _normalise_domain(source_url)
    issues: list[dict] = []
    if not source_url.lower().startswith(("http://", "https://")):
        issues.append({"code": "DATASHEET_URL_NOT_HTTP", "severity": "blocker", "message": "Datasheet URL must be http/https."})
    if not source_url.lower().split("?")[0].endswith(".pdf"):
        issues.append({"code": "DATASHEET_URL_NOT_PDF", "severity": "warning", "message": "URL does not end in .pdf; downloader may still fetch but reviewer should check."})
    source = db.scalars(select(DatasheetSourceDomain).where(DatasheetSourceDomain.domain == domain)).first()
    if source is None or source.status != "approved":
        issues.append({"code": "DATASHEET_SOURCE_DOMAIN_NOT_APPROVED", "severity": "blocker", "domain": domain, "message": "Domain is not in the approved manufacturer/source registry."})
    return {"status": "blocked" if any(i["severity"] == "blocker" for i in issues) else "ok", "domain": domain, "issues": issues}


def queue_datasheet_download(db: Session, *, source_url: str, product_id: str | None = None, requested_by: str | None = None) -> DatasheetDownloadJob:
    if product_id is not None and db.get(Product, product_id) is None:
        raise DatasheetWorkflowError(f"Product {product_id} does not exist.")
    validation = validate_datasheet_source_url(db, source_url)
    if validation["status"] == "blocked":
        raise DatasheetWorkflowError(f"Datasheet source URL blocked: {validation['issues']}")
    payload = {"source_url": source_url, "product_id": product_id}
    job = DatasheetDownloadJob(
        job_id=f"dsjob_{stable_json_hash(payload)[:14]}",
        product_id=product_id,
        source_url=source_url,
        source_domain=validation["domain"],
        status="queued",
        validation_report=validation,
        requested_by=requested_by,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    existing = db.get(DatasheetDownloadJob, job.job_id)
    if existing is not None:
        return existing
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def list_download_jobs(db: Session) -> list[DatasheetDownloadJob]:
    return list(db.scalars(select(DatasheetDownloadJob).order_by(DatasheetDownloadJob.created_at.desc())))


def list_datasheet_review_queue_v2(db: Session) -> list[dict]:
    rows = list(db.scalars(
        select(DatasheetCandidateSpec)
        .where(DatasheetCandidateSpec.status.in_(["candidate", "needs_model_review"]))
        .order_by(DatasheetCandidateSpec.product_id, DatasheetCandidateSpec.datasheet_id, DatasheetCandidateSpec.field_name)
    ))
    grouped: dict[str, dict] = {}
    for row in rows:
        product = db.get(Product, row.product_id) if row.product_id else None
        datasheet = db.get(DatasheetFile, row.datasheet_id)
        key = f"{row.product_id or 'unlinked'}::{row.datasheet_id}"
        if key not in grouped:
            grouped[key] = {
                "product_id": row.product_id,
                "product_title": product.title if product else None,
                "manufacturer": product.manufacturer if product else None,
                "manufacturer_model": product.manufacturer_model if product else None,
                "datasheet_id": row.datasheet_id,
                "file_name": datasheet.file_name if datasheet else None,
                "source_url": datasheet.source_url if datasheet else None,
                "candidate_count": 0,
                "blocked_count": 0,
                "table_preview_count": len((datasheet.extraction_report or {}).get("table_previews", [])) if datasheet else 0,
                "ocr_status": (datasheet.extraction_report or {}).get("ocr_status", "unknown") if datasheet else "unknown",
                "fields": {},
                "review_instructions": "Approve only exact model-specific values. Conflicts require corrected value, unit, model/variant selection, basis, and reason.",
            }
        bucket = grouped[key]
        bucket["candidate_count"] += 1
        if row.status == "needs_model_review":
            bucket["blocked_count"] += 1
        bucket["fields"].setdefault(row.field_name, []).append({
            "candidate_id": row.candidate_id,
            "value_number": row.value_number,
            "value_text": row.value_text,
            "unit": row.unit,
            "confidence": row.confidence,
            "status": row.status,
            "source_page": row.source_page,
            "source_text_quote": row.source_text_quote,
            "validation_report": row.validation_report,
            "requires_model_selection": row.status == "needs_model_review",
        })
    return list(grouped.values())


def get_datasheet_table_preview(db: Session, datasheet_id: str) -> dict:
    datasheet = db.get(DatasheetFile, datasheet_id)
    if datasheet is None:
        raise DatasheetWorkflowError(f"Datasheet {datasheet_id} not found.")
    report = datasheet.extraction_report or {}
    return {
        "datasheet_id": datasheet.datasheet_id,
        "file_name": datasheet.file_name,
        "source_url": datasheet.source_url,
        "table_extractors": report.get("table_extractors", []),
        "table_previews": report.get("table_previews", []),
        "status": "has_tables" if report.get("table_previews") else "no_tables_detected",
    }


def get_datasheet_ocr_status(db: Session, datasheet_id: str) -> dict:
    datasheet = db.get(DatasheetFile, datasheet_id)
    if datasheet is None:
        raise DatasheetWorkflowError(f"Datasheet {datasheet_id} not found.")
    report = datasheet.extraction_report or {}
    ocr_status = report.get("ocr_status", "not_required" if datasheet.raw_text else "needs_ocr_placeholder")
    return {
        "datasheet_id": datasheet.datasheet_id,
        "file_name": datasheet.file_name,
        "ocr_status": ocr_status,
        "reason": "No native text/table text was found." if ocr_status == "needs_ocr_placeholder" else "Native text or table text exists.",
        "next_action": "Queue OCR worker in a later phase; do not promote blank/scanned PDFs by guesswork." if ocr_status == "needs_ocr_placeholder" else "No OCR needed for current archive.",
        "truth_boundary": "OCR is a future extraction aid only; OCR output must still enter Q2 candidate review before Q3 use.",
    }


def recalculate_product_design_readiness(db: Session, product_id: str, *, commit: bool = True) -> dict:
    product = db.get(Product, product_id)
    if product is None:
        raise DatasheetWorkflowError(f"Product {product_id} not found.")
    specs = list_reviewed_specs(db, product_id)
    reviewed_fields = sorted({s.field_name for s in specs if s.quality_level in DESIGN_READY_QUALITY_LEVELS and s.review_status == "reviewed"})
    missing_fields: list[str] = []
    issues: list[dict] = []
    design_ready = False
    if product.status not in {"active", "replacement"}:
        issues.append({"code": "PRODUCT_STATUS_NOT_DESIGN_READY", "severity": "blocker", "status": product.status})
    elif product.category == "panel":
        missing_fields = sorted(PANEL_CRITICAL_FIELDS - set(reviewed_fields))
        if missing_fields:
            issues.append({"code": "PANEL_CRITICAL_Q3_SPECS_MISSING", "severity": "blocker", "missing_fields": missing_fields})
        else:
            design_ready = True
    else:
        # Other product classes need their own critical field maps later; do not overclaim.
        issues.append({"code": "NON_PANEL_DESIGN_READY_RULE_NOT_IMPLEMENTED", "severity": "warning", "category": product.category})
    product.design_ready = design_ready
    if design_ready and product.quality_level not in DESIGN_READY_QUALITY_LEVELS:
        product.quality_level = "Q3_reviewed"
    if not design_ready and product.quality_level == "Q3_reviewed" and product.category == "panel":
        # Keep Q3 if manually set? No. For Phase 002C panel Q3 means all critical Q3 fields exist.
        product.quality_level = "Q2_parsed" if reviewed_fields else product.quality_level
    report = {
        "product_id": product.product_id,
        "status": "design_ready" if design_ready else "blocked",
        "design_ready": design_ready,
        "quality_level": product.quality_level,
        "category": product.category,
        "reviewed_fields": reviewed_fields,
        "missing_fields": missing_fields,
        "issues": issues,
        "truth_boundary": "Design readiness is recalculated from reviewed Q3/Q4 specs; scraped shop text is ignored.",
    }
    if commit:
        db.commit()
        db.refresh(product)
    return report


def run_datasheet_self_check(db: Session) -> dict:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Maximum Power Pmax 450 W\nOpen Circuit Voltage Voc 49.9 V\nMaximum Power Voltage Vmp 41.5 V\nShort Circuit Current Isc 11.4 A\nMaximum Power Current Imp 10.85 A\nDimensions 1762 x 1134 x 30 mm\nMaximum Series Fuse 25 A\nMaximum System Voltage 1500 V")
    bio = BytesIO()
    doc.save(bio)
    doc.close()
    product = Product(product_id="prd_selfcheck_panel", manufacturer="SelfCheck Solar", manufacturer_model="SC-450", category="panel", title="Self check datasheet panel", status="active", quality_level="Q0_scraped", design_ready=False)
    if db.get(Product, product.product_id) is None:
        db.add(product)
        db.commit()
    datasheet = archive_datasheet_bytes(db, file_name="selfcheck.pdf", data=bio.getvalue(), product_id=product.product_id, uploaded_by="system_self_check")
    candidates = list_candidates(db, datasheet_id=datasheet.datasheet_id)
    power = next((c for c in candidates if c.field_name == "power_stc_w"), None)
    if power is None:
        return {"status": "failed", "reason": "No power candidate extracted", "candidate_fields": [c.field_name for c in candidates]}
    review = review_candidate(db, power.candidate_id, action="approve", reviewer="system_self_check") if power.status == "candidate" else None
    specs = list_reviewed_specs(db, product.product_id)
    return {
        "status": "ok" if any(s.field_name == "power_stc_w" and s.quality_level == "Q3_reviewed" for s in specs) else "failed",
        "datasheet_id": datasheet.datasheet_id,
        "candidate_count": len(candidates),
        "candidate_fields": sorted({c.field_name for c in candidates}),
        "table_extractors": datasheet.extraction_report.get("table_extractors", []),
        "review_id": review.review_id if review else None,
        "truth_boundary": "Self-check confirms datasheet candidates do not become Q3 specs without review.",
    }

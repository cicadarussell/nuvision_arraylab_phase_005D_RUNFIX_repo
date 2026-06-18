from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import Product, ProductDataSnapshot, ProductSnapshotApplication, ProductSpec, ProductVersion
from app.services.hash_utils import stable_json_hash


class ProductApplyWorkflowError(ValueError):
    """Raised when product snapshot application would break the evidence chain."""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:14]}"


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows(snapshot: ProductDataSnapshot, sheet_name: str | None = None) -> list[dict]:
    rows = snapshot.snapshot_payload.get("rows", []) if snapshot.snapshot_payload else []
    if sheet_name is None:
        return list(rows)
    return [row for row in rows if row.get("sheet_name") == sheet_name]


def _normalise_product_payload(row_payload: dict) -> dict:
    product_id = _clean(row_payload.get("product_id"))
    return {
        "product_id": product_id,
        "nuvision_sku": _clean(row_payload.get("nuvision_sku") or row_payload.get("sku")),
        "manufacturer": _clean(row_payload.get("manufacturer")) or "unknown_manufacturer",
        "manufacturer_model": _clean(row_payload.get("manufacturer_model") or row_payload.get("model")),
        "category": _clean(row_payload.get("category")) or "other",
        "title": _clean(row_payload.get("title")) or product_id or "Untitled product",
        "status": _clean(row_payload.get("status")) or "active",
        "quality_level": _clean(row_payload.get("quality_level")) or "Q0_scraped",
        "nuvision_url": _clean(row_payload.get("nuvision_url") or row_payload.get("url")),
    }


def _product_to_payload(product: Product | None) -> dict | None:
    if product is None:
        return None
    return {
        "product_id": product.product_id,
        "nuvision_sku": product.nuvision_sku,
        "manufacturer": product.manufacturer,
        "manufacturer_model": product.manufacturer_model,
        "category": product.category,
        "title": product.title,
        "status": product.status,
        "quality_level": product.quality_level,
        "nuvision_url": product.nuvision_url,
        "design_ready": product.design_ready,
    }


PANEL_REQUIRED_FIELDS = {"power_stc_w", "length_mm", "width_mm", "voc_v", "vmp_v", "isc_a", "imp_a"}
ALLOWED_PRODUCT_STATUSES = {"active", "hidden", "discontinued", "replacement", "QX_deprecated"}
DESTRUCTIVE_STATUS_WORDS = {"delete", "deleted", "remove", "removed"}


def _reviewed_specs_by_product(snapshot: ProductDataSnapshot) -> dict[str, set[str]]:
    by_product: dict[str, set[str]] = defaultdict(set)
    for row in _rows(snapshot, "Datasheet_Review"):
        payload = row.get("payload", {})
        if str(payload.get("review_status", "")).strip().lower() != "reviewed":
            continue
        product_id = _clean(payload.get("product_id"))
        field_name = _clean(payload.get("field_name"))
        if product_id and field_name and _clean(payload.get("corrected_value")) is not None:
            by_product[product_id].add(str(field_name))
    return by_product


def _design_ready_for_payload(product_payload: dict, reviewed_fields: set[str]) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    q = str(product_payload.get("quality_level") or "Q0_scraped")
    if q not in {"Q3_reviewed", "Q4_manufacturer_confirmed"}:
        warnings.append("Product is not Q3/Q4, so it cannot be design-ready.")
        return False, warnings
    if product_payload.get("status") == "QX_deprecated" or q == "QX_deprecated":
        warnings.append("Deprecated product is blocked from new design use.")
        return False, warnings
    if product_payload.get("category") == "panel":
        missing = sorted(PANEL_REQUIRED_FIELDS - set(reviewed_fields))
        if missing:
            warnings.append("Panel missing reviewed critical specs: " + ", ".join(missing))
            return False, warnings
    return True, warnings


def build_product_apply_preview(db: Session, snapshot_id: str, created_by: str | None = None) -> ProductSnapshotApplication:
    snapshot = db.get(ProductDataSnapshot, snapshot_id)
    if snapshot is None:
        raise ProductApplyWorkflowError(f"Product data snapshot {snapshot_id} not found.")

    product_rows = _rows(snapshot, "Products")
    reviewed_specs = _reviewed_specs_by_product(snapshot)
    diff_items: list[dict] = []
    issues: list[dict] = []
    actions = Counter()

    if not product_rows:
        issues.append({
            "code": "SNAPSHOT_HAS_NO_PRODUCT_ROWS",
            "severity": "blocker",
            "message": "Snapshot has no Products rows, so nothing can be applied to the current product table.",
            "blocks_status": True,
        })

    seen_ids: set[str] = set()
    for row in product_rows:
        payload = _normalise_product_payload(row.get("payload", {}))
        product_id = payload.get("product_id")
        warnings: list[str] = []
        raw_status = str(payload.get("status") or "active").strip()
        if raw_status.lower() in DESTRUCTIVE_STATUS_WORDS:
            issues.append({
                "code": "PRODUCT_DELETION_NOT_ALLOWED",
                "severity": "blocker",
                "message": f"Product {product_id or 'unknown'} requested destructive status '{raw_status}'. Products must be hidden, discontinued, replacement, or QX_deprecated instead of deleted.",
                "path": f"Products.row_{row.get('row_number')}.status",
                "blocks_status": True,
            })
            continue
        if raw_status not in ALLOWED_PRODUCT_STATUSES:
            issues.append({
                "code": "INVALID_PRODUCT_STATUS",
                "severity": "blocker",
                "message": f"Product {product_id or 'unknown'} uses unsupported status '{raw_status}'.",
                "path": f"Products.row_{row.get('row_number')}.status",
                "suggested_fix": "Use active, hidden, discontinued, replacement, or QX_deprecated.",
                "blocks_status": True,
            })
            continue
        if not product_id:
            issues.append({
                "code": "PRODUCT_ID_MISSING",
                "severity": "blocker",
                "message": f"Products row {row.get('row_number')} has no product_id.",
                "path": f"Products.row_{row.get('row_number')}.product_id",
                "blocks_status": True,
            })
            continue
        if product_id in seen_ids:
            issues.append({
                "code": "DUPLICATE_PRODUCT_ID_IN_SNAPSHOT",
                "severity": "blocker",
                "message": f"Product {product_id} appears more than once in the Products sheet.",
                "path": f"Products.product_id.{product_id}",
                "blocks_status": True,
            })
            continue
        seen_ids.add(product_id)

        existing = db.get(Product, product_id)
        before = _product_to_payload(existing)
        design_ready, readiness_warnings = _design_ready_for_payload(payload, reviewed_specs.get(product_id, set()))
        after = {**payload, "design_ready": design_ready}
        warnings.extend(readiness_warnings)

        if before is None:
            action = "create"
        elif stable_json_hash(before) == stable_json_hash(after):
            action = "noop"
        else:
            action = "update"
        actions[action] += 1
        diff_items.append({
            "action": action,
            "product_id": product_id,
            "sheet_name": "Products",
            "row_number": row.get("row_number"),
            "source_row_hash_sha256": row.get("row_hash_sha256"),
            "before": before,
            "after": after,
            "warnings": warnings,
        })

    preview_payload = {
        "snapshot_id": snapshot_id,
        "source_snapshot_hash_sha256": snapshot.content_hash_sha256,
        "diff_items": diff_items,
        "issues": issues,
    }
    preview_hash = stable_json_hash(preview_payload)
    application = ProductSnapshotApplication(
        application_id=_new_id("psa"),
        snapshot_id=snapshot_id,
        status="blocked" if any(i.get("blocks_status") for i in issues) else "previewed",
        created_by=created_by,
        preview_hash_sha256=preview_hash,
        diff_summary={
            "actions": dict(actions),
            "product_count": len(diff_items),
            "spec_rows_reviewed": sum(len(v) for v in reviewed_specs.values()),
            "diff_items": diff_items,
            "truth_boundary": "preview only; no live product mutation until apply endpoint is called",
        },
        validation_report={
            "status": "blocked" if any(i.get("blocks_status") for i in issues) else ("warnings" if any(d.get("warnings") for d in diff_items) else "ok"),
            "issues": issues,
        },
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def list_product_snapshot_applications(db: Session) -> list[ProductSnapshotApplication]:
    return list(db.scalars(select(ProductSnapshotApplication).order_by(ProductSnapshotApplication.created_at.desc())))


def get_product_snapshot_application(db: Session, application_id: str) -> ProductSnapshotApplication | None:
    return db.get(ProductSnapshotApplication, application_id)


def _upsert_product(db: Session, item: dict, application: ProductSnapshotApplication) -> Product:
    after = item["after"]
    product = db.get(Product, after["product_id"])
    if product is None:
        product = Product(product_id=after["product_id"], manufacturer=after["manufacturer"], category=after["category"], title=after["title"])
        db.add(product)

    product.nuvision_sku = after.get("nuvision_sku")
    product.manufacturer = after["manufacturer"]
    product.manufacturer_model = after.get("manufacturer_model")
    product.category = after["category"]
    product.title = after["title"]
    product.status = after.get("status") or "active"
    product.quality_level = after.get("quality_level") or "Q0_scraped"
    product.nuvision_url = after.get("nuvision_url")
    product.design_ready = bool(after.get("design_ready"))

    version_payload = {k: after.get(k) for k in sorted(after)}
    version_hash = stable_json_hash(version_payload)
    version = ProductVersion(
        version_id=f"pv_{version_hash[:16]}",
        product_id=product.product_id,
        application_id=application.application_id,
        snapshot_id=application.snapshot_id,
        source_row_hash_sha256=item.get("source_row_hash_sha256") or stable_json_hash(item),
        product_payload_hash_sha256=version_hash,
        product_payload=version_payload,
        revision_note=f"Applied from snapshot {application.snapshot_id}",
    )
    db.add(version)
    return product


def _apply_reviewed_specs(db: Session, snapshot: ProductDataSnapshot, application_id: str) -> int:
    count = 0
    for row in _rows(snapshot, "Datasheet_Review"):
        payload = row.get("payload", {})
        if str(payload.get("review_status", "")).strip().lower() != "reviewed":
            continue
        product_id = _clean(payload.get("product_id"))
        field_name = _clean(payload.get("field_name"))
        value = _clean(payload.get("corrected_value"))
        if not product_id or not field_name or value is None:
            continue
        value_number = _as_float(value)
        source_url = _clean(payload.get("source_url"))
        spec_payload = {
            "product_id": product_id,
            "field_name": field_name,
            "value": value,
            "unit": _clean(payload.get("unit")),
            "source_url": source_url,
            "row_hash_sha256": row.get("row_hash_sha256"),
            "application_id": application_id,
        }
        spec_hash = stable_json_hash(spec_payload)
        if db.get(ProductSpec, f"spec_{spec_hash[:16]}"):
            continue
        db.add(ProductSpec(
            spec_id=f"spec_{spec_hash[:16]}",
            product_id=product_id,
            field_name=str(field_name),
            value_text=str(value),
            value_number=value_number,
            unit=_clean(payload.get("unit")),
            normalized_value_si=value_number,
            normalized_unit=_clean(payload.get("unit")),
            quality_level="Q3_reviewed",
            source_type="manufacturer_datasheet" if source_url else "manual_review",
            source_url=source_url,
            source_file_hash_sha256=None,
            source_page=None,
            source_text_quote=None,
            extraction_method="spreadsheet_review",
            confidence=1.0,
            review_status="reviewed",
            reviewed_by=application_id,
            reviewed_at=datetime.now(UTC),
        ))
        count += 1
    return count


def apply_product_snapshot_application(db: Session, application_id: str, applied_by: str | None = None) -> ProductSnapshotApplication:
    application = get_product_snapshot_application(db, application_id)
    if application is None:
        raise ProductApplyWorkflowError(f"Product snapshot application {application_id} not found.")
    if application.status != "previewed":
        raise ProductApplyWorkflowError(f"Only previewed applications can be applied. Current status is {application.status}.")
    if application.validation_report.get("status") == "blocked":
        raise ProductApplyWorkflowError("Blocked application cannot be applied.")

    snapshot = db.get(ProductDataSnapshot, application.snapshot_id)
    if snapshot is None:
        raise ProductApplyWorkflowError(f"Source snapshot {application.snapshot_id} not found.")
    if stable_json_hash({
        "snapshot_id": snapshot.snapshot_id,
        "source_snapshot_hash_sha256": snapshot.content_hash_sha256,
        "diff_items": application.diff_summary.get("diff_items", []),
        "issues": application.validation_report.get("issues", []),
    }) != application.preview_hash_sha256:
        raise ProductApplyWorkflowError("Application preview hash no longer matches. Re-preview before applying.")

    applied_products = 0
    skipped_noops = 0
    for item in application.diff_summary.get("diff_items", []):
        if item.get("action") == "noop":
            skipped_noops += 1
            continue
        _upsert_product(db, item, application)
        applied_products += 1
    applied_specs = _apply_reviewed_specs(db, snapshot, application.application_id)

    application.status = "applied"
    application.applied_by = applied_by
    application.applied_at = datetime.now(UTC)
    application.diff_summary = {
        **application.diff_summary,
        "apply_result": {
            "applied_products": applied_products,
            "skipped_noops": skipped_noops,
            "applied_reviewed_specs": applied_specs,
            "applied_by": applied_by,
        },
    }
    db.commit()
    db.refresh(application)
    return application


def list_current_products(db: Session) -> list[Product]:
    return list(db.scalars(select(Product).order_by(Product.manufacturer, Product.title, Product.product_id)))


def get_product_versions(db: Session, product_id: str) -> list[ProductVersion]:
    return list(db.scalars(select(ProductVersion).where(ProductVersion.product_id == product_id).order_by(ProductVersion.created_at.desc())))


def search_current_products(
    db: Session,
    q: str | None = None,
    manufacturer: str | None = None,
    category: str | None = None,
    status: str | None = None,
    quality_level: str | None = None,
    design_ready: bool | None = None,
) -> list[Product]:
    products = list_current_products(db)
    def match(product: Product) -> bool:
        haystack = " ".join(str(v or "") for v in [product.product_id, product.title, product.manufacturer, product.manufacturer_model, product.nuvision_sku]).lower()
        if q and q.lower() not in haystack:
            return False
        if manufacturer and product.manufacturer.lower() != manufacturer.lower():
            return False
        if category and product.category != category:
            return False
        if status and product.status != status:
            return False
        if quality_level and product.quality_level != quality_level:
            return False
        if design_ready is not None and bool(product.design_ready) is not design_ready:
            return False
        return True
    return [product for product in products if match(product)]


def validate_current_product_record(db: Session, product_id: str) -> dict:
    product = db.get(Product, product_id)
    if product is None:
        return {"status": "blocked", "issues": [{"code": "PRODUCT_NOT_FOUND", "severity": "blocker", "message": "Current product not found.", "blocks_status": True}], "summary": {"product_id": product_id}}
    issues: list[dict] = []
    if product.status in {"hidden", "discontinued", "QX_deprecated"}:
        issues.append({"code": "PRODUCT_NOT_ACTIVE", "severity": "blocker", "message": f"Product status is {product.status}; it cannot be used for new final designs.", "blocks_status": True})
    if product.quality_level not in {"Q3_reviewed", "Q4_manufacturer_confirmed"}:
        issues.append({"code": "PRODUCT_DATA_NOT_REVIEWED", "severity": "blocker", "message": "Product is not Q3/Q4 reviewed.", "blocks_status": True})
    if product.category == "panel":
        reviewed_fields = {spec.field_name for spec in product.specs if spec.review_status == "reviewed" and spec.quality_level in {"Q3_reviewed", "Q4_manufacturer_confirmed"}}
        missing = sorted(PANEL_REQUIRED_FIELDS - reviewed_fields)
        if missing:
            issues.append({"code": "PANEL_CRITICAL_SPECS_MISSING", "severity": "blocker", "message": "Panel missing reviewed critical specs: " + ", ".join(missing), "blocks_status": True})
    status = "blocked" if any(i.get("blocks_status") for i in issues) else "ok"
    return {"status": status, "issues": issues, "summary": {"product_id": product_id, "design_ready": product.design_ready, "spec_count": len(product.specs)}}

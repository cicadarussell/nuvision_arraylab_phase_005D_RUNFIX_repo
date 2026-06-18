from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import (
    CommercialQuoteSnapshot,
    PriceSnapshot,
    PriceStockApplication,
    ProductDataSnapshot,
    RollbackRecord,
    StockSnapshot,
)
from app.services.hash_utils import stable_json_hash


class CommercialWorkflowError(ValueError):
    """Raised when commercial snapshot workflow would break traceability."""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:14]}"


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _as_float(value) -> float | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _rows(snapshot: ProductDataSnapshot, sheet_name: str | None = None) -> list[dict]:
    rows = snapshot.snapshot_payload.get("rows", []) if snapshot.snapshot_payload else []
    if sheet_name is None:
        return list(rows)
    return [row for row in rows if row.get("sheet_name") == sheet_name]


def _normalise_price_stock_payload(row_payload: dict) -> dict:
    return {
        "product_id": _clean(row_payload.get("product_id")),
        "trade_price_gbp": _as_float(row_payload.get("trade_price_gbp")),
        "list_price_gbp": _as_float(row_payload.get("list_price_gbp")),
        "currency": _clean(row_payload.get("currency")) or "GBP",
        "stock_status": _clean(row_payload.get("stock_status")) or "unknown",
        "stock_quantity": _as_int(row_payload.get("stock_quantity")),
        "lead_time_days": _as_int(row_payload.get("lead_time_days")),
        "supplier_priority": _clean(row_payload.get("supplier_priority")),
    }


def _current_price(db: Session, product_id: str) -> PriceSnapshot | None:
    return db.scalars(select(PriceSnapshot).where(PriceSnapshot.product_id == product_id).order_by(PriceSnapshot.created_at.desc())).first()


def _current_stock(db: Session, product_id: str) -> StockSnapshot | None:
    return db.scalars(select(StockSnapshot).where(StockSnapshot.product_id == product_id).order_by(StockSnapshot.created_at.desc())).first()


def build_price_stock_apply_preview(db: Session, snapshot_id: str, created_by: str | None = None) -> PriceStockApplication:
    snapshot = db.get(ProductDataSnapshot, snapshot_id)
    if snapshot is None:
        raise CommercialWorkflowError(f"Product data snapshot {snapshot_id} not found.")

    rows = _rows(snapshot, "Prices_Stock")
    issues: list[dict] = []
    diff_items: list[dict] = []
    actions = Counter()

    if not rows:
        issues.append({
            "code": "SNAPSHOT_HAS_NO_PRICE_STOCK_ROWS",
            "severity": "blocker",
            "message": "Snapshot has no Prices_Stock rows, so no commercial data can be applied.",
            "blocks_status": True,
        })

    seen: set[str] = set()
    for row in rows:
        payload = _normalise_price_stock_payload(row.get("payload", {}))
        product_id = payload.get("product_id")
        warnings: list[str] = []
        if not product_id:
            issues.append({
                "code": "PRICE_STOCK_PRODUCT_ID_MISSING",
                "severity": "blocker",
                "message": f"Prices_Stock row {row.get('row_number')} has no product_id.",
                "path": f"Prices_Stock.row_{row.get('row_number')}.product_id",
                "blocks_status": True,
            })
            continue
        if product_id in seen:
            issues.append({
                "code": "DUPLICATE_PRICE_STOCK_ROW",
                "severity": "blocker",
                "message": f"Product {product_id} appears more than once in Prices_Stock.",
                "path": f"Prices_Stock.product_id.{product_id}",
                "blocks_status": True,
            })
            continue
        seen.add(product_id)

        if payload["currency"] != "GBP":
            warnings.append("Non-GBP currency stored as commercial warning; quote maths should confirm conversion before customer use.")
        if payload["stock_status"] in {"", "unknown", None}:
            warnings.append("Stock status unknown; quote cannot become purchase-ready without NuVision review.")

        current_price = _current_price(db, product_id)
        current_stock = _current_stock(db, product_id)
        before = {
            "price": current_price.payload if current_price else None,
            "stock": current_stock.payload if current_stock else None,
        }
        after = payload
        action = "create" if current_price is None and current_stock is None else ("noop" if stable_json_hash(before) == stable_json_hash({"price": after, "stock": after}) else "update")
        actions[action] += 1
        diff_items.append({
            "action": action,
            "product_id": product_id,
            "sheet_name": "Prices_Stock",
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
    app = PriceStockApplication(
        application_id=_new_id("psa_com"),
        snapshot_id=snapshot_id,
        status="blocked" if any(i.get("blocks_status") for i in issues) else "previewed",
        created_by=created_by,
        preview_hash_sha256=stable_json_hash(preview_payload),
        diff_summary={
            "actions": dict(actions),
            "price_stock_row_count": len(diff_items),
            "diff_items": diff_items,
            "truth_boundary": "commercial preview only; old quotes are immutable evidence packets",
        },
        validation_report={
            "status": "blocked" if any(i.get("blocks_status") for i in issues) else ("warnings" if any(d.get("warnings") for d in diff_items) else "ok"),
            "issues": issues,
        },
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


def get_price_stock_application(db: Session, application_id: str) -> PriceStockApplication | None:
    return db.get(PriceStockApplication, application_id)


def list_price_stock_applications(db: Session) -> list[PriceStockApplication]:
    return list(db.scalars(select(PriceStockApplication).order_by(PriceStockApplication.created_at.desc())))


def apply_price_stock_application(db: Session, application_id: str, applied_by: str | None = None) -> PriceStockApplication:
    app = get_price_stock_application(db, application_id)
    if app is None:
        raise CommercialWorkflowError(f"Price/stock application {application_id} not found.")
    if app.status != "previewed":
        raise CommercialWorkflowError(f"Only previewed applications can be applied. Current status is {app.status}.")
    if app.validation_report.get("status") == "blocked":
        raise CommercialWorkflowError("Blocked price/stock application cannot be applied.")

    snapshot = db.get(ProductDataSnapshot, app.snapshot_id)
    if snapshot is None:
        raise CommercialWorkflowError(f"Source snapshot {app.snapshot_id} not found.")
    preview_hash = stable_json_hash({
        "snapshot_id": snapshot.snapshot_id,
        "source_snapshot_hash_sha256": snapshot.content_hash_sha256,
        "diff_items": app.diff_summary.get("diff_items", []),
        "issues": app.validation_report.get("issues", []),
    })
    if preview_hash != app.preview_hash_sha256:
        raise CommercialWorkflowError("Price/stock preview hash no longer matches. Re-preview before applying.")

    applied = 0
    skipped = 0
    for item in app.diff_summary.get("diff_items", []):
        if item.get("action") == "noop":
            skipped += 1
            continue
        payload = item["after"]
        payload_hash = stable_json_hash(payload)
        price = PriceSnapshot(
            price_snapshot_id=f"price_{payload_hash[:16]}",
            product_id=payload["product_id"],
            application_id=app.application_id,
            source_snapshot_id=app.snapshot_id,
            source_row_hash_sha256=item.get("source_row_hash_sha256") or stable_json_hash(item),
            trade_price_gbp=payload.get("trade_price_gbp"),
            list_price_gbp=payload.get("list_price_gbp"),
            currency=payload.get("currency") or "GBP",
            payload_hash_sha256=payload_hash,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        stock = StockSnapshot(
            stock_snapshot_id=f"stock_{payload_hash[:16]}",
            product_id=payload["product_id"],
            application_id=app.application_id,
            source_snapshot_id=app.snapshot_id,
            source_row_hash_sha256=item.get("source_row_hash_sha256") or stable_json_hash(item),
            stock_status=payload.get("stock_status") or "unknown",
            stock_quantity=payload.get("stock_quantity"),
            lead_time_days=payload.get("lead_time_days"),
            supplier_priority=payload.get("supplier_priority"),
            payload_hash_sha256=payload_hash,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        if db.get(PriceSnapshot, price.price_snapshot_id) is None:
            db.add(price)
        if db.get(StockSnapshot, stock.stock_snapshot_id) is None:
            db.add(stock)
        applied += 1

    app.status = "applied"
    app.applied_by = applied_by
    app.applied_at = datetime.now(UTC)
    app.diff_summary = {**app.diff_summary, "apply_result": {"applied_rows": applied, "skipped_noops": skipped, "applied_by": applied_by}}
    db.commit()
    db.refresh(app)
    return app


def list_latest_price_snapshots(db: Session) -> list[PriceSnapshot]:
    rows = list(db.scalars(select(PriceSnapshot).order_by(PriceSnapshot.product_id, PriceSnapshot.created_at.desc())))
    latest: dict[str, PriceSnapshot] = {}
    for row in rows:
        latest.setdefault(row.product_id, row)
    return list(latest.values())


def list_latest_stock_snapshots(db: Session) -> list[StockSnapshot]:
    rows = list(db.scalars(select(StockSnapshot).order_by(StockSnapshot.product_id, StockSnapshot.created_at.desc())))
    latest: dict[str, StockSnapshot] = {}
    for row in rows:
        latest.setdefault(row.product_id, row)
    return list(latest.values())


def create_quote_snapshot(db: Session, project_id: str | None, product_ids: list[str], created_by: str | None = None, note: str | None = None) -> CommercialQuoteSnapshot:
    quote_items: list[dict] = []
    price_ids: list[str] = []
    stock_ids: list[str] = []
    for product_id in product_ids:
        price = _current_price(db, product_id)
        stock = _current_stock(db, product_id)
        item = {
            "product_id": product_id,
            "price_snapshot": price.payload if price else None,
            "stock_snapshot": stock.payload if stock else None,
        }
        if price:
            price_ids.append(price.price_snapshot_id)
        if stock:
            stock_ids.append(stock.stock_snapshot_id)
        quote_items.append(item)
    payload = {"project_id": project_id, "items": quote_items, "note": note, "truth_boundary": "quote copies snapshot payloads; later price/stock updates do not mutate this packet"}
    content_hash = stable_json_hash(payload)
    quote = CommercialQuoteSnapshot(
        quote_id=f"quote_{content_hash[:16]}",
        project_id=project_id,
        quote_payload_hash_sha256=content_hash,
        quote_payload=payload,
        price_snapshot_ids=price_ids,
        stock_snapshot_ids=stock_ids,
        created_by=created_by,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return quote


def get_quote_snapshot(db: Session, quote_id: str) -> CommercialQuoteSnapshot | None:
    return db.get(CommercialQuoteSnapshot, quote_id)


def list_quote_snapshots(db: Session) -> list[CommercialQuoteSnapshot]:
    return list(db.scalars(select(CommercialQuoteSnapshot).order_by(CommercialQuoteSnapshot.created_at.desc())))


def create_rollback_record(db: Session, target_type: str, target_id: str, reason: str, requested_by: str | None = None, payload: dict | None = None) -> RollbackRecord:
    marker_payload = payload or {}
    rollback = RollbackRecord(
        rollback_id=_new_id("rb"),
        target_type=target_type,
        target_id=target_id,
        rollback_kind="non_destructive_marker",
        reason=reason,
        requested_by=requested_by,
        target_hash_sha256=stable_json_hash(marker_payload) if marker_payload else None,
        payload=marker_payload,
    )
    db.add(rollback)
    db.commit()
    db.refresh(rollback)
    return rollback


def list_rollback_records(db: Session) -> list[RollbackRecord]:
    return list(db.scalars(select(RollbackRecord).order_by(RollbackRecord.created_at.desc())))

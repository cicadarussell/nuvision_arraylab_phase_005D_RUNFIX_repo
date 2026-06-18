from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.models.db_models import Base
from app.schemas.project import ProjectSnapshot
from app.services.product_snapshot_apply import apply_product_snapshot_application, build_product_apply_preview, list_current_products
from app.services.commercial_snapshots import (
    apply_price_stock_application,
    build_price_stock_apply_preview,
    create_quote_snapshot,
    list_latest_price_snapshots,
)
from app.services.self_check import run_backend_self_checks
from app.services.spreadsheet_staging import approve_staged_import, stage_spreadsheet_import
from app.services.validation_engine import validate_project_for_mounting_precheck
from app.services.datasheet_review import (
    archive_datasheet_bytes,
    create_or_update_source_domain,
    list_candidates,
    queue_datasheet_download,
    review_candidate,
    run_datasheet_self_check,
    recalculate_product_design_readiness,
    get_datasheet_ocr_status,
    list_datasheet_review_queue_v2,
)
from app.models.db_models import Product
from app.services.datasheet_downloader import FetchResult, datasheet_worker_debug, list_ocr_jobs, run_datasheet_download_job
from app.services.project_geometry import project_geometry_self_check
from app.services.geometry_import import geometry_import_self_check
from app.services.map_geometry_sync import map_sync_self_check
from app.services.geometry_quality import geometry_quality_self_check
from app.services.panel_packing import panel_packing_governance_self_check, panel_packing_self_check
from app.services.yield_preview import yield_preview_self_check
from app.services.solar_geometry import solar_geometry_self_check
from app.services.shade_preview import shade_preview_self_check


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_quality_gate_workbook() -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Products": ["product_id", "manufacturer", "category", "title", "status", "quality_level", "nuvision_sku", "manufacturer_model", "nuvision_url"],
        "Prices_Stock": ["product_id", "stock_status", "lead_time_days", "trade_price_gbp", "list_price_gbp", "currency", "stock_quantity", "supplier_priority"],
        "Datasheet_Review": ["product_id", "field_name", "review_status", "corrected_value", "unit", "source_url"],
        "Labour_Rules": ["rule_id", "scope", "condition", "base_hours", "multiplier", "review_status"],
        "Mounting_Rules": ["mapping_id", "roof_type", "manufacturer", "system_family", "requires_manufacturer_calc", "review_status"],
        "Workflow_Feedback": ["feedback_id", "category", "severity", "description", "triage_status"],
    }
    for name, headers in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
        if name == "Products":
            ws.append(["prd_gate_panel", "JA Solar", "panel", "Gate test panel", "active", "Q3_reviewed", "GATE-001", "JAM54D", "https://example.com/product"])
        elif name == "Datasheet_Review":
            for field, value in {
                "power_stc_w": 450,
                "length_mm": 1762,
                "width_mm": 1134,
                "voc_v": 49.9,
                "vmp_v": 41.5,
                "isc_a": 11.4,
                "imp_a": 10.85,
            }.items():
                ws.append(["prd_gate_panel", field, "reviewed", value, "mixed", "https://example.com/datasheet.pdf"])
        elif name == "Prices_Stock":
            ws.append(["prd_gate_panel", "in_stock", 3, 100.0, 120.0, "GBP", 9, "preferred"])
        elif name == "Labour_Rules":
            ws.append(["lab_gate", "roof_type", "tiled_pitched", 8, 1.0, "reviewed"])
        elif name == "Mounting_Rules":
            ws.append(["map_gate", "tiled_pitched", "Van der Valk", "ValkPitched Clamp", True, "reviewed"])
        elif name == "Workflow_Feedback":
            ws.append(["fb_gate", "data", "low", "quality gate", "new"])
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def run_product_apply_smoke() -> dict:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        record = stage_spreadsheet_import(db, "quality_gate.xlsx", make_quality_gate_workbook(), uploaded_by="quality_gate")
        snapshot = approve_staged_import(db, record.import_id, approved_by="quality_gate")
        app = build_product_apply_preview(db, snapshot.snapshot_id, created_by="quality_gate")
        applied = apply_product_snapshot_application(db, app.application_id, applied_by="quality_gate")
        price_app = build_price_stock_apply_preview(db, snapshot.snapshot_id, created_by="quality_gate")
        applied_price = apply_price_stock_application(db, price_app.application_id, applied_by="quality_gate")
        quote = create_quote_snapshot(db, project_id="quality_gate_project", product_ids=["prd_gate_panel"], created_by="quality_gate")
        products = list_current_products(db)
        latest_prices = list_latest_price_snapshots(db)
        return {
            "staged_status": record.status,
            "snapshot_id": snapshot.snapshot_id,
            "application_status": applied.status,
            "price_stock_application_status": applied_price.status,
            "current_product_count": len(products),
            "latest_price_count": len(latest_prices),
            "quote_hash_length": len(quote.quote_payload_hash_sha256),
            "quote_price_copied": quote.quote_payload["items"][0]["price_snapshot"]["trade_price_gbp"],
            "first_product_design_ready": products[0].design_ready if products else False,
        }


def run_datasheet_hardening_smoke() -> dict:
    from io import BytesIO
    try:
        import pymupdf  # type: ignore
    except Exception:  # pragma: no cover
        import fitz as pymupdf  # type: ignore

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        product = Product(
            product_id="prd_gate_conflict_panel", manufacturer="JA Solar", manufacturer_model="JAM conflict",
            category="panel", title="Conflict gate panel", status="active", quality_level="Q0_scraped", design_ready=False,
        )
        db.add(product)
        db.commit()
        create_or_update_source_domain(db, "manufacturer.example", created_by="quality_gate")
        job = queue_datasheet_download(db, source_url="https://manufacturer.example/panel.pdf", product_id=product.product_id, requested_by="quality_gate")
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Maximum Power Pmax 440 W\nMaximum Power Pmax 450 W\nOpen Circuit Voltage Voc 49.9 V\nDimensions 1762 x 1134 x 30 mm")
        bio = BytesIO()
        doc.save(bio)
        doc.close()
        ds = archive_datasheet_bytes(db, file_name="conflict.pdf", data=bio.getvalue(), product_id=product.product_id, source_url="https://manufacturer.example/panel.pdf", uploaded_by="quality_gate")
        candidates = list_candidates(db, datasheet_id=ds.datasheet_id)
        conflict = next((c for c in candidates if c.field_name == "power_stc_w"), None)
        blocked_without_correction = False
        try:
            review_candidate(db, conflict.candidate_id, action="approve", reviewer="quality_gate")
        except Exception:
            blocked_without_correction = True
        review = review_candidate(
            db, conflict.candidate_id, action="approve", reviewer="quality_gate",
            corrected_value_number=450, corrected_unit="W", reason="Exact product model is 450 W.",
            selected_manufacturer_model="JAM conflict 450", model_selection_basis="Quality gate selected 450 W model row."
        )
        return {
            "download_job_status": job.status,
            "datasheet_id": ds.datasheet_id,
            "conflict_status": conflict.status if conflict else None,
            "blocked_without_correction": blocked_without_correction,
            "corrected_review_created_spec": bool(review.created_spec_id),
            "table_extractors": ds.extraction_report.get("table_extractors", []),
        }


def run_datasheet_review_ui_smoke() -> dict:
    from io import BytesIO
    try:
        import pymupdf  # type: ignore
    except Exception:  # pragma: no cover
        import fitz as pymupdf  # type: ignore

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        product = Product(
            product_id="prd_gate_ready_panel", manufacturer="JA Solar", manufacturer_model="JAM ready",
            category="panel", title="Ready gate panel", status="active", quality_level="Q0_scraped", design_ready=False,
        )
        db.add(product)
        db.commit()
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Maximum Power Pmax 450 W\nOpen Circuit Voltage Voc 49.9 V\nMaximum Power Voltage Vmp 41.5 V\nShort Circuit Current Isc 11.4 A\nMaximum Power Current Imp 10.85 A\nDimensions 1762 x 1134 x 30 mm")
        bio = BytesIO()
        doc.save(bio)
        doc.close()
        ds = archive_datasheet_bytes(db, file_name="ready.pdf", data=bio.getvalue(), product_id=product.product_id, uploaded_by="quality_gate")
        queue = list_datasheet_review_queue_v2(db)
        critical = {"power_stc_w", "length_mm", "width_mm", "voc_v", "vmp_v", "isc_a", "imp_a"}
        for candidate in list_candidates(db, datasheet_id=ds.datasheet_id):
            if candidate.field_name in critical:
                review_candidate(db, candidate.candidate_id, action="approve", reviewer="quality_gate")
        readiness = recalculate_product_design_readiness(db, product.product_id)
        ocr = get_datasheet_ocr_status(db, ds.datasheet_id)
        return {
            "queue_groups": len(queue),
            "datasheet_id": ds.datasheet_id,
            "design_ready": readiness["design_ready"],
            "missing_fields": readiness["missing_fields"],
            "ocr_status": ocr["ocr_status"],
        }



def run_datasheet_downloader_worker_smoke() -> dict:
    try:
        import pymupdf  # type: ignore
    except Exception:  # pragma: no cover
        import fitz as pymupdf  # type: ignore

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        product = Product(
            product_id="prd_gate_worker_panel", manufacturer="JA Solar", manufacturer_model="JAM worker",
            category="panel", title="Worker gate panel", status="active", quality_level="Q0_scraped", design_ready=False,
        )
        db.add(product)
        db.commit()
        create_or_update_source_domain(db, "manufacturer.example", created_by="quality_gate")
        job = queue_datasheet_download(db, source_url="https://manufacturer.example/worker.pdf", product_id=product.product_id, requested_by="quality_gate")
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Maximum Power Pmax 450 W\nOpen Circuit Voltage Voc 49.9 V\nDimensions 1762 x 1134 x 30 mm")
        bio = BytesIO()
        doc.save(bio)
        doc.close()
        result = run_datasheet_download_job(
            db,
            job.job_id,
            fetcher=lambda url: FetchResult(200, bio.getvalue(), "application/pdf", final_url=url),
            run_by="quality_gate",
        )
        debug = datasheet_worker_debug(db)
        return {
            "job_status": result.status,
            "datasheet_id": result.datasheet_id,
            "retry_count": result.retry_count,
            "worker_debug_status": debug["status"],
            "download_jobs_by_status": debug["download_jobs_by_status"],
            "ocr_jobs_total": len(list_ocr_jobs(db)),
        }

def main() -> int:
    print("ArrayLab quality gate: phase005D_RUNFIX")
    self_check = run_backend_self_checks()
    print(json.dumps(self_check, indent=2))
    if self_check["status"] != "ok":
        return 1

    # Datasheet self-check proves Q2 candidates do not become Q3 engineering data without review.
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        datasheet_check = run_datasheet_self_check(db)
    print(json.dumps({"datasheet_self_check": datasheet_check}, indent=2))
    if datasheet_check.get("status") != "ok" or "power_stc_w" not in datasheet_check.get("candidate_fields", []):
        return 1

    datasheet_hardening = run_datasheet_hardening_smoke()
    print(json.dumps({"datasheet_hardening_smoke": datasheet_hardening}, indent=2))
    if not datasheet_hardening["blocked_without_correction"] or not datasheet_hardening["corrected_review_created_spec"] or datasheet_hardening["download_job_status"] != "queued":
        return 1

    datasheet_review_ui = run_datasheet_review_ui_smoke()
    print(json.dumps({"datasheet_review_ui_smoke": datasheet_review_ui}, indent=2))
    if not datasheet_review_ui["design_ready"] or datasheet_review_ui["missing_fields"] or datasheet_review_ui["ocr_status"] != "not_required":
        return 1

    datasheet_downloader_worker = run_datasheet_downloader_worker_smoke()
    print(json.dumps({"datasheet_downloader_worker_smoke": datasheet_downloader_worker}, indent=2))
    if datasheet_downloader_worker["job_status"] != "succeeded" or not datasheet_downloader_worker["datasheet_id"] or datasheet_downloader_worker["retry_count"] != 1:
        return 1

    product_apply = run_product_apply_smoke()
    print(json.dumps({"product_apply_smoke": product_apply}, indent=2))
    if product_apply["application_status"] != "applied" or product_apply["price_stock_application_status"] != "applied" or product_apply["latest_price_count"] != 1 or product_apply["quote_price_copied"] != 100.0 or product_apply["current_product_count"] != 1 or not product_apply["first_product_design_ready"]:
        return 1

    bench_dir = ROOT / "tests" / "benchmark_projects"
    failures = []
    for path in sorted(bench_dir.glob("*.json")):
        data = load_json(path)
        project = ProjectSnapshot.model_validate({
            "project_id": data.get("project_id", path.stem),
            "site": data.get("site", {}),
            "roof_planes": data.get("roof_planes", []),
        })
        report = validate_project_for_mounting_precheck(project)
        expected = data.get("expected", {})
        expected_warnings = expected.get("warnings", [])
        codes_and_messages = " ".join([i.code + " " + i.message for i in report.issues]).lower()
        normalized = codes_and_messages.replace("_", " ").replace(".", "")
        for expected_warning in expected_warnings:
            expected_norm = expected_warning.lower().replace("_", " ").replace(".", "")
            if expected_norm not in normalized:
                failures.append({"file": path.name, "missing_expected_warning": expected_warning, "actual": [i.model_dump() for i in report.issues]})
    if failures:
        print(json.dumps({"benchmark_failures": failures}, indent=2))
        return 1

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        geometry_check = project_geometry_self_check(db)
    print(json.dumps({"geometry_self_check": geometry_check}, indent=2))
    if geometry_check.get("status") != "ok":
        return 1

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        geometry_import_check = geometry_import_self_check(db)
    print(json.dumps({"geometry_import_self_check": geometry_import_check}, indent=2))
    if geometry_import_check.get("status") != "ok" or geometry_import_check.get("roof_planes_created") != 2:
        return 1



    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        map_sync_check = map_sync_self_check(db)
    print(json.dumps({"map_sync_self_check": map_sync_check}, indent=2))
    if map_sync_check.get("status") != "ok" or not map_sync_check.get("closed_ring_exported"):
        return 1

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        geometry_quality_check = geometry_quality_self_check(db).model_dump(mode="json")
    print(json.dumps({"geometry_quality_self_check": geometry_quality_check}, indent=2))
    if geometry_quality_check.get("status") != "ok" or not geometry_quality_check.get("bad_polygon_blocked") or not geometry_quality_check.get("obstruction_reduces_usable_area"):
        return 1

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        panel_packing_check = panel_packing_self_check(db).model_dump(mode="json")
    print(json.dumps({"panel_packing_self_check": panel_packing_check}, indent=2))
    if panel_packing_check.get("status") != "ok" or not panel_packing_check.get("preview_panels_fit") or not panel_packing_check.get("final_blocks_dev_fallback") or not panel_packing_check.get("invalid_geometry_blocked"):
        return 1

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        panel_governance_check = panel_packing_governance_self_check(db).model_dump(mode="json")
    print(json.dumps({"panel_packing_governance_self_check": panel_governance_check}, indent=2))
    if panel_governance_check.get("status") != "ok" or not panel_governance_check.get("override_history_immutable") or not panel_governance_check.get("selected_layout_export_hash_stable") or not panel_governance_check.get("final_override_blocked_from_preview_fallback"):
        return 1



    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        yield_check = yield_preview_self_check(db).model_dump(mode="json")
    print(json.dumps({"yield_preview_self_check": yield_check}, indent=2))
    if (
        yield_check.get("status") != "ok"
        or not yield_check.get("annual_kwh_positive")
        or not yield_check.get("monthly_sum_matches_annual")
        or not yield_check.get("assumption_change_changes_hash")
        or not yield_check.get("panel_layout_required_blocked")
        or not yield_check.get("pvgis_monthly_cache_hit")
        or not yield_check.get("pvgis_monthly_values_used")
        or not yield_check.get("pvgis_unavailable_fallback_warns")
    ):
        return 1


    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        solar_geometry_check = solar_geometry_self_check(db).model_dump(mode="json")
    print(json.dumps({"solar_geometry_self_check": solar_geometry_check}, indent=2))
    if (
        solar_geometry_check.get("status") != "ok"
        or not solar_geometry_check.get("south_35_beats_north_35")
        or not solar_geometry_check.get("tilt_change_changes_hash")
        or not solar_geometry_check.get("azimuth_change_changes_factor")
        or not solar_geometry_check.get("sample_count_ok")
        or not solar_geometry_check.get("incidence_math_bounds_ok")
        or not solar_geometry_check.get("calculation_hash_changes_with_geometry")
    ):
        return 1


    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        shade_check = shade_preview_self_check(db).model_dump(mode="json")
    print(json.dumps({"shade_preview_self_check": shade_check}, indent=2))
    if (
        shade_check.get("status") != "ok"
        or not shade_check.get("shade_changes_with_obstruction_height")
        or not shade_check.get("shade_changes_with_obstruction_position")
        or not shade_check.get("missing_obstruction_height_blocks")
        or not shade_check.get("worst_panel_list_present")
        or not shade_check.get("shade_hash_changes_with_geometry")
        or not shade_check.get("sample_bounds_ok")
    ):
        return 1

    print("QUALITY GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

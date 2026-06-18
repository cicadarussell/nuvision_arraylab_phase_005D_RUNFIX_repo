from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db_models import Base
from app.schemas.calculation import CalculationRunCreate, CalculationRunType
from app.schemas.project import ProjectSnapshot
from app.services.calculation_run import build_calculation_run
from app.services.hash_utils import stable_json_hash
from app.services.spreadsheet_staging import approve_staged_import, stage_spreadsheet_import
from app.services.validation_engine import validate_project_for_mounting_precheck


def _make_self_check_workbook() -> bytes:
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
    for name, headers in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
        if name == "Products":
            ws.append(["prd_self", "SelfCheck", "panel", "Self-check panel", "active", "Q3_reviewed"])
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _run_persistence_self_check() -> dict:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        record = stage_spreadsheet_import(db, "self_check.xlsx", _make_self_check_workbook(), uploaded_by="self_check")
        snapshot = approve_staged_import(db, record.import_id, approved_by="self_check")
        return {
            "name": "spreadsheet_stage_approve_snapshot",
            "passed": record.status == "approved" and snapshot.row_count >= 1 and len(snapshot.content_hash_sha256) == 64,
            "detail": {"import_id": record.import_id, "snapshot_id": snapshot.snapshot_id, "row_count": snapshot.row_count},
        }


def run_backend_self_checks() -> dict:
    checks: list[dict] = []

    h1 = stable_json_hash({"b": 2, "a": 1})
    h2 = stable_json_hash({"a": 1, "b": 2})
    checks.append({"name": "stable_json_hash_order_independent", "passed": h1 == h2, "detail": h1})

    payload = CalculationRunCreate(
        run_type=CalculationRunType.roof_geometry,
        input_snapshot={"site": {"postcode": "EX14 4PB"}, "roof": {"pitch_deg": 35, "azimuth_deg": 180}},
    )
    run = build_calculation_run(payload)
    checks.append({"name": "calculation_run_created", "passed": run.run_id.startswith("run_"), "detail": run.run_id})

    bad_project = ProjectSnapshot.model_validate({
        "project_id": "self_check_missing_height",
        "site": {"postcode": "EX14 4PB"},
        "roof_planes": [{"roof_plane_id": "r1", "pitch_deg": 35, "azimuth_deg": 180, "height_m": None, "roof_type": "unknown"}],
    })
    report = validate_project_for_mounting_precheck(bad_project)
    checks.append({"name": "missing_roof_height_blocks_mounting", "passed": report.has_blockers, "detail": [i.code for i in report.issues]})

    checks.append(_run_persistence_self_check())

    passed = all(c["passed"] for c in checks)
    return {"status": "ok" if passed else "failed", "checks": checks}

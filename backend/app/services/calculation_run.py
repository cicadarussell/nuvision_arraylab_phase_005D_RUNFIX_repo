from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.core.settings import settings
from app.schemas.calculation import CalculationRunCreate, CalculationRunRead, CalculationStatus
from app.schemas.validation import Severity
from app.services.hash_utils import stable_json_hash
from app.services.validation_engine import validate_calculation_inputs

SOFTWARE_VERSION = settings.software_version


def infer_status(payload: CalculationRunCreate) -> CalculationStatus:
    report = validate_calculation_inputs(payload.run_type.value, payload.input_snapshot)
    if report.has_blockers:
        return CalculationStatus.survey_required
    if payload.run_type.value == "mounting_precheck":
        return CalculationStatus.manufacturer_calc_required
    if payload.warnings or report.has_errors:
        return CalculationStatus.survey_required
    return CalculationStatus.preview


def build_calculation_run(payload: CalculationRunCreate) -> CalculationRunRead:
    input_hash = stable_json_hash(payload.input_snapshot)
    status = infer_status(payload)
    warnings = list(payload.warnings)
    validation_report = validate_calculation_inputs(payload.run_type.value, payload.input_snapshot)
    for item in validation_report.issues:
        if item.severity in {Severity.error, Severity.blocker}:
            warnings.append(f"{item.code}: {item.message}")

    clean_payload = payload.model_copy(update={"warnings": warnings})
    return CalculationRunRead(
        **clean_payload.model_dump(),
        run_id=f"run_{uuid4().hex[:12]}",
        software_version=SOFTWARE_VERSION,
        input_snapshot_hash_sha256=input_hash,
        outputs_hash_sha256=None,
        status=status,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )

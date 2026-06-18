from app.schemas.calculation import CalculationRunCreate, CalculationRunType
from app.services.calculation_run import build_calculation_run

def test_calculation_run_has_hash_and_id():
    payload = CalculationRunCreate(run_type=CalculationRunType.yield_calc, input_snapshot={"roof_pitch_deg": 35, "azimuth_deg": 180})
    run = build_calculation_run(payload)
    assert run.run_id.startswith("run_")
    assert len(run.input_snapshot_hash_sha256) == 64

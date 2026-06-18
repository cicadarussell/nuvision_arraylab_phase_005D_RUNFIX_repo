from app.schemas.calculation import CalculationRunCreate, CalculationRunType
from app.services.calculation_run import build_calculation_run

payload = CalculationRunCreate(run_type=CalculationRunType.roof_geometry, input_snapshot={"site":{"postcode":"EX14 4PB"},"roof":{"pitch_deg":35,"azimuth_deg":180,"height_m":None}}, warnings=["roof height missing: structural status blocked"])
run = build_calculation_run(payload)
print(run.model_dump_json(indent=2))

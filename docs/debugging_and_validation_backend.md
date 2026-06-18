# Debugging and Validation Backend

Phase 001B adds the first explicit mistake-prevention layer.

## Debug endpoints

- `GET /health` - service health.
- `GET /api/debug/version` - phase, version, truth boundary.
- `GET /api/debug/self-check` - backend self-checks for stable hashing, calculation-run creation, and missing-roof-height blocking.

## Validation endpoints

- `POST /api/projects/validate-mounting-precheck` - checks roof height/type/polygon before mounting workflow.
- `POST /api/products/{product_id}/validate-for-design` - blocks Q0/Q1 products from design-ready use.
- `POST /api/imports/inspect-spreadsheet` - inspects XLSX imports and blocks unsafe spreadsheet edits.

## Rules implemented now

- Missing roof height blocks wind-zone/mounting readiness.
- Unknown roof type blocks mounting recommendation.
- Editable product spreadsheets cannot include protected electrical or engineering fields like Voc/Isc/dimensions.
- Calculation runs use stable input hashes.
- Calculation outputs are immutable by policy: recalculation creates a new run.

## What is deliberately not claimed

The backend does not calculate final wind loading, final hook spacing, final ballast, MCS compliance, or roof structural adequacy. It only blocks bad workflow states and prepares evidence for manufacturer/engineer workflows.

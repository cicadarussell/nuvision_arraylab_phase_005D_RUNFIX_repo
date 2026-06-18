# Phase 005B: PVGIS/PVLib Preview Yield Engine

Phase 005B adds the first selected-layout-to-yield calculation path.

## Scope

Built:

- yield assumption-set table,
- default UK preview assumption set,
- T0 kWh/kWp/year estimate,
- monthly output distribution,
- per-roof orientation/tilt factors,
- backend-only PVGIS request stub,
- calculation run evidence packet,
- frontend yield preview controls,
- yield self-check and tests.

Not built:

- live PVGIS request execution,
- pvlib modelchain,
- hourly weather/shade model,
- financial/payback model,
- final proposal status.

## Calculation policy

The T0 model is deliberately simple:

`annual_kWh = dc_kWp × specific_yield × azimuth_factor × tilt_factor × loss_multiplier`

Where:

- `specific_yield` defaults to 950 kWh/kWp/year,
- `azimuth_factor` penalises deviation from true south,
- `tilt_factor` penalises deviation from 35 degrees,
- `loss_multiplier` includes system loss, shade loss and year-1 degradation.

This is transparent, testable, and deliberately marked as preview-only. It is not proposal-grade yield maths.

## PVGIS policy

PVGIS calls must be backend-owned. Phase 005B stores a request stub only so the next phase can add a cacheable adapter without changing the frontend contract.

## Evidence

Every yield preview stores:

- selected layout calculation run ID,
- selected layout export hash,
- site/roof basis,
- assumption set,
- request payload,
- monthly output,
- output hash,
- calculation run row.

# Phase 005B: PVGIS Backend Adapter + Cache

Phase 005B upgrades the preview yield path from a PVGIS request stub to a backend-owned PVGIS adapter and request/response cache.

## Boundary

This phase still produces **preview yield evidence only**. It is not a final proposal, MCS design, electrical design, financial model, or structural approval.

## What changed

- Added `pvgis_request_cache` table.
- Added backend PVGIS PVcalc parameter builder.
- Added ArrayLab true-azimuth to PVGIS aspect conversion.
- Added PVGIS monthly JSON parser for `outputs.monthly.fixed` / `E_m` rows.
- Added cache read/write by request hash.
- Added backend-only network fetch path, disabled unless explicitly allowed.
- Added fallback warning when PVGIS is requested but unavailable.
- Added T0 vs PVGIS comparison payload.
- Added `/api/debug/pvgis-cache`.

## PVGIS truth policy

PVGIS data is weather/location-backed evidence, not final truth by itself. A final design still needs reviewed product specs, selected layout evidence, shade assumptions, electrical checks, mounting workflow, and quote/BOM gates.

## Browser policy

The browser must not call PVGIS directly. Frontend requests go to ArrayLab backend. Backend creates or reuses a cache record and stores request params, response hash, parsed monthly kWh, status, errors and timestamps.

## Request identity

PVGIS cache records are keyed by stable hash of:

- endpoint
- lat/lon
- peakpower
- slope
- PVGIS aspect
- loss
- technology/mounting parameters
- adapter version

Changing assumptions changes the request hash and creates a new calculation packet.

## Failure policy

If PVGIS is requested and unavailable:

- the run returns a warning,
- T0 preview remains available,
- the cache status is visible,
- the calculation packet records the fallback.

No silent PVGIS failure is allowed.

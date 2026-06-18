# Phase 005C: pvlib-ready Solar Position + Roof Plane Irradiance Prep

Phase 005C adds a solar-geometry debug layer between PVGIS monthly yield and future shade modelling.

## Truth boundary

This phase is preview/debug only. It does not create final PV yield, shade-loss, MCS, structural, or electrical approval.

## Added

- Optional pvlib-ready solar-position service.
- Deterministic ArrayLab NOAA-lite fallback when pvlib is not installed.
- Roof-plane incidence-angle samples for monthly 21st days at 09:00, 12:00 and 15:00 local time.
- Plane-of-array cosine and beam factor vs horizontal.
- PVGIS geometry comparison notes.
- Shade-engine input contract V0.1.
- Debug endpoint and self-check.

## New endpoints

- `GET /api/debug/solar-geometry-self-check`
- `POST /api/projects/{project_id}/yield/solar-geometry-debug`

## Mathematical checks

The self-check proves:

- south-facing 35 degree roof has better noon incidence than north-facing 35 degree roof at EX14-style latitude;
- changing tilt changes the calculation hash;
- changing azimuth changes the incidence factor;
- sample count is stable;
- solar elevation, azimuth, cosines and incidence factors remain in expected bounds;
- calculation hash changes with roof geometry.

## pvlib note

`pvlib` is optional in Phase 005C. If installed with the `solar` extra, the service can use `pvlib.solarposition`. If not installed, tests use ArrayLab's deterministic fallback so the project remains runnable offline.

## Next phase

NVA_005D should add first shade-ray sample contract and obstruction-shadow preview. Keep it low-resolution and traceable before attempting annual/hourly shade losses.

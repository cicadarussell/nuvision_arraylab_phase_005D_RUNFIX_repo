# Phase 005D-RUNFIX: Shade Preview + Local Startup Preservation

This phase merges the Phase 005D obstruction-shadow preview work with the Phase 005C RUNFIX launcher improvements.

## Purpose

Keep the software easy to run on Python 3.9+ while adding the first preview-only shade-ray contract.

## Added / preserved

- Python 3.9+ backend package requirement.
- Python 3.9-compatible StrEnum shim.
- Safer Windows run_dev/run_tests/doctor launchers.
- Shade preview schemas and service.
- Shade preview debug endpoint.
- Per-panel shade sample summaries and worst-panel list.
- Blocker for obstruction height missing.
- Output hashes and calculation run evidence.

## Truth boundary

Shade preview is a deterministic debug model only. It is not annual shade loss, not PVsyst, not final yield, not final proposal maths, and not structural/electrical approval.

## Next

NVA_005E should connect shade preview output into preview yield as an optional explicit assumption, with visible confidence labels and no silent replacement of PVGIS/pvlib evidence.

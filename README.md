# NuVision ArrayLab Phase 005D-RUNFIX

## TLDR

Phase 005D-RUNFIX keeps the Python 3.9+ local startup repair from Phase 005C-RUNFIX and adds the first preview-only obstruction-shadow/shade debug layer.

This is still design-assist software. It is not structural approval, not final yield truth, not PVsyst, not MCS sign-off, and not an electrical design. It is the first traceable bridge from solar geometry and panel placements into shade evidence.

## What changed

- Preserved the safer Windows launchers from RUNFIX:
  - `run_dev.bat`
  - `run_tests.bat`
  - `doctor.bat`
  - `scripts/windows_arraylab_launcher.ps1`
- Preserved Python 3.9+ compatibility:
  - backend now allows `>=3.9`
  - `StrEnum` shim is used instead of Python 3.11-only `enum.StrEnum`
- Added shade preview schemas and service.
- Added debug/self-check route for shade preview.
- Added project shade-preview route.
- Added per-panel shade sample summaries.
- Added worst-panel list.
- Added obstruction-height blocker.
- Added shade result hashes and calculation-run evidence.
- Updated frontend with a **Run shade preview** button and shade result panel.
- Updated route smoke and quality gate to include shade preview.

## New endpoints

- `GET /api/debug/shade-preview-self-check`
- `POST /api/projects/{project_id}/shade/preview`

## How to run backend

From the repo root:

```powershell
run_dev.bat
```

If setup fails:

```powershell
doctor.bat
```

Then open:

```text
http://127.0.0.1:8000/docs
```

## How to run frontend

```powershell
cd frontend
npm install
npm run dev
```

Then open the Vite URL, usually:

```text
http://127.0.0.1:5173
```

## Human test path

1. Start backend with `run_dev.bat`.
2. Start frontend with `npm run dev` inside `frontend`.
3. Create a test project.
4. Draw and sync a roof polygon.
5. Run geometry quality/setbacks.
6. Run panel packing preview.
7. Run preview yield.
8. Run solar geometry debug.
9. Run shade preview.

## Truth boundary

Shade preview is a deterministic low-resolution obstruction-shadow debug model. It does not calculate annual shade-adjusted yield yet. It does not replace PVGIS, pvlib, PVsyst, SAM, installer review, electrical design, manufacturer mounting reports, or engineer sign-off.

## Verification

- Backend tests: 100 passed.
- Backend compile: passed.
- Quality gate: passed.
- Route smoke test: passed.
- Shade preview self-check: passed.
- Frontend TypeScript/Vite build: passed.
- Vite warning: MapLibre bundle is large. Not a functional failure.

## Still not done

- Annual/hourly shade loss.
- Full 3D raycasting.
- Tree seasonality.
- String-level shade/electrical loss.
- Feeding shade output into yield automatically.
- Inverter/string matching.
- Van der Valk Assist export.
- BOM/proposal generator.
- Final structural/electrical approval workflow.

## Next phase

NVA_005E should connect shade preview output into preview yield as an optional explicit assumption, with visible confidence labels and no silent replacement of PVGIS/pvlib evidence.

# Testing Phase 005D-RUNFIX

## Quick environment check

```powershell
doctor.bat
```

Expected:

- Python 3.9+ detected.
- Backend app found.
- Existing broken `.venv` reported as rebuildable if needed.

## Backend run

```powershell
run_dev.bat
```

Expected:

- dependencies install without the old Python `>=3.11` error
- backend starts at `http://127.0.0.1:8000`
- docs open at `http://127.0.0.1:8000/docs`

## Backend tests

```powershell
run_tests.bat
```

Expected result:

```text
100 passed
```

## Quality gate

```powershell
cd backend
.\.venv\Scripts\activate
cd ..
python scripts\run_quality_gate.py
```

Expected result:

```text
QUALITY GATE PASSED
```

Quality gate now includes:

- product/data checks
- geometry checks
- panel packing checks
- PVGIS/yield checks
- solar geometry checks
- shade preview checks

## Route smoke test

```powershell
python scripts\route_smoke_test.py
```

Expected result: status `ok` and route map includes:

- `/api/debug/shade-preview-self-check`
- `/api/projects/{project_id}/shade/preview`

## Frontend test

```powershell
cd frontend
npm install
npm run build
npm run dev
```

Expected:

- build passes
- map appears
- roof polygon can be drawn/synced
- panel packing preview can run
- solar geometry debug can run
- shade preview can run after panel packing

## Known warning

Vite warns that the bundled JS chunk is large because MapLibre is heavy. That is not a functional failure. Later we can split the map page.

## Truth boundary check

Shade preview is only preview/debug evidence. It is not:

- annual shade-adjusted yield
- PVsyst/SAM/pvlib modelchain output
- final proposal maths
- final structural approval
- final mounting approval
- final electrical design
- MCS compliance

ArrayLab prepares evidence. Manufacturer/installer/engineer approval remains external.

# Phase 004E: Candidate Override Governance + Selected Layout Export

## Purpose

Phase 004E makes panel-packing selection auditable before ArrayLab moves into PVGIS/yield, stringing, inverter choice, or BOM generation.

The key rule is unchanged: a layout is not final design authority. It is pre-design evidence that must remain traceable.

## Added

- Persistent append-only `panel_packing_overrides` table.
- Override reviewer and reviewer role fields.
- Override history endpoint.
- Selected layout export endpoint for future yield/stringing/BOM phases.
- Panel layout edit/delete data contract endpoint.
- Panel-packing governance self-check endpoint.
- Frontend buttons for override persistence, history, selected layout export, and edit-contract debug.

## Safety rules

- A persistent override must reference the selected candidate from a calculation run.
- To choose a different candidate, rerun packing with that candidate override first, then persist the selected run.
- Final-use overrides cannot be created from preview/dev-fallback panel evidence.
- Overrides do not bypass Q3/Q4 product requirements.
- Overrides do not bypass structural, electrical, manufacturer, or engineer gates.
- Layout edits are contracts only in this phase. They are not applied yet.

## New endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/debug/panel-packing-governance-self-check` | proves override governance works |
| `POST /api/projects/{project_id}/panel-packing/runs/{calculation_run_id}/overrides` | records selected candidate override evidence |
| `GET /api/projects/{project_id}/panel-packing/overrides` | append-only override history |
| `GET /api/projects/{project_id}/panel-packing/runs/{calculation_run_id}/selected-layout-export` | selected layout contract for yield/stringing/BOM |
| `GET /api/projects/{project_id}/panel-packing/layout-edit-contract` | future edit/delete action contract |

## Verification

- Backend tests: 84 passed.
- Quality gate: passed.
- Route smoke test: passed.
- Frontend build: passed.
- Compile check: passed.
- Ruff: not installed in this environment, so not claimed.

## Still not done

- Persistent panel edit/delete execution.
- Click-to-select candidate on the map.
- PVGIS/pvlib yield endpoint.
- Inverter/string matching.
- BOM/proposal generator.
- Van der Valk Assist export.
- Final structural/electrical approval workflow.

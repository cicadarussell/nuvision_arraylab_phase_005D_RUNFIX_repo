# Phase 004A: Product-Aware Panel Packing Engine V0

## Purpose

Phase 004A creates the first roof-to-panels loop:

1. use project/site/roof geometry,
2. use geometry quality + setback rules,
3. export packer-allowed area,
4. place rectangular PV modules in portrait/landscape candidates,
5. store the panel packing result as a calculation run/evidence packet.

This is still design-assist. It is not structural approval, inverter approval, MCS approval, or final installation design.

## Truth boundary

- Development fallback panels are preview-only.
- Final mode blocks fallback panels.
- Real panel models must come from Q3/Q4 reviewed ProductSpec rows.
- Bad roof geometry blocks packing.
- Every packing result gets an input snapshot hash and output hash.

## Added endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/debug/panel-packing-self-check` | proves packer behaviour |
| `POST /api/projects/{project_id}/panel-packing/run` | runs panel packing and writes a calculation run |

## Panel model requirements for real use

A panel product needs reviewed Q3/Q4 specs for:

- `power_stc_w`
- `length_mm`
- `width_mm`

Later phases must also use electrical values for inverter/string matching:

- `voc_v`
- `vmp_v`
- `isc_a`
- `imp_a`

## Known limits

- V0 packs axis-aligned rectangles only.
- It chooses the highest-power candidate for the whole run.
- It does not yet optimise aesthetics, mixed models, string grouping, or roof-plane rotation.
- It does not create BOM or electrical design.
- It does not approve structural/mounting safety.

## Verification

Expected checks:

- backend tests pass,
- quality gate passes,
- route smoke passes,
- frontend build passes,
- dev fallback works in preview,
- dev fallback is blocked in final mode,
- invalid geometry cannot be packed,
- panel packing writes a calculation run.

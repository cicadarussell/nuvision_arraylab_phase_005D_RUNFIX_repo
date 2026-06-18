# Phase 003A - Project / Site / Roof Geometry Spine

## Purpose

Move ArrayLab from pure data/datasheet backend into the first real solar-design workflow object model:

project -> site -> roof plane -> obstruction -> immutable geometry snapshot -> mounting precheck calculation run.

This phase deliberately does **not** build final roof drawing UI, PVGIS yield, panel packing, stringing, BOM, or Van der Valk replacement. It creates the data spine those features need.

## Truth boundary

Mounting precheck is design-assist only. It can block missing data and flag manufacturer-calculation-required status. It does not approve final wind loading, ballast, roof fixing, MCS compliance, or installation safety.

## Added tables

- `projects`
- `project_version_snapshots`
- `sites`
- `roof_planes`
- `obstructions`

## Added behaviours

- Site data stores source type and confidence.
- Roof planes store pitch, azimuth, height, roof type, polygon, area, and edge-zone depth.
- Edge-zone depth is calculated as height / 5 for precheck warnings, following the established project rule from Van der Valk guidance. This is not final structural calculation.
- Obstructions can be stored as manual blocks for later shade modelling.
- Geometry snapshots are immutable and hash-stable.
- Mounting precheck stores a calculation run with input snapshot hash and validation output hash.

## New endpoints

- `GET /api/debug/geometry-self-check`
- `POST /api/projects`
- `GET /api/projects`
- `GET /api/projects/{project_id}/geometry`
- `POST /api/projects/{project_id}/site`
- `POST /api/projects/{project_id}/roof-planes`
- `POST /api/projects/{project_id}/obstructions`
- `POST /api/projects/{project_id}/snapshots`
- `GET /api/projects/{project_id}/snapshots`
- `GET /api/projects/{project_id}/validate-geometry`
- `POST /api/projects/{project_id}/mounting-precheck`

## Pass gates

- Missing roof height blocks mounting readiness.
- Unknown roof type blocks mounting recommendation.
- Known roof data moves only to `S2_manufacturer_calc_required`, never final approval.
- Project snapshots remain hash-stable when geometry does not change.
- Mounting precheck creates a calculation run.

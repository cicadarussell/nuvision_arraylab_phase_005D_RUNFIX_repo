# Phase 004A: Geometry Quality + Setback/Margin Rules

## Purpose

Create a geometry-quality layer between roof drawing and panel packing.

Panel packing must never run directly on raw map clicks without sanity checks. Raw map clicks can contain bad scale, crossed polygons, tiny edges, unknown roof type, missing height, and obstruction conflicts.

## Checks

- polygon exists
- polygon is valid and non-self-crossing
- area is useful
- tiny edges are flagged
- extreme aspect ratios are flagged
- unknown roof type blocks fallback use
- edge setback is applied
- obstruction clearance is subtracted
- usable area is exported separately from raw area

## Outputs

- `GeometryQualityReportRead`
- `PackerAllowedAreaExportRead`
- geometry quality evidence snapshot

## Boundary

Setbacks in this phase are conservative pre-design defaults. Formal rules must come from NuVision/manufacturer/installer/engineer workflow.

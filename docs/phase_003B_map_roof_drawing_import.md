# Phase 003B - Map/Roof Drawing Test UI + Geometry Import

## Purpose

Make the geometry spine testable by a human before building the heavier MapLibre/solar-maths layer.

## Added

- local polygon drawing in `local_test_harness.html`
- CICADA Solar Field Planner V2 JSON import
- local geometry export
- geometry import self-check
- CORS for local browser testing
- route/quality gate coverage

## Engineering boundary

Imported/drawn geometry is planning evidence only. It must not become structural approval.

## Next

Move this from local harness into the real React/MapLibre project page while keeping the local harness as a debugging fallback.

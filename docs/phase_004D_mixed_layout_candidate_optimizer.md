# Phase 004D: Mixed Layout Candidate Optimiser + Aesthetic Rows

## Purpose

Phase 004D improves panel-packing selection quality without relaxing the evidence rules.

The phase adds a deterministic candidate optimiser that compares:

- portrait-only layouts
- landscape-only layouts
- mixed portrait/landscape layouts
- max kWp score
- best-fit score
- fewer-panel score
- aesthetic-row score

It also adds manual candidate override evidence so a human can choose a different candidate while leaving a reason and hashable record.

## Non-goals

This phase does not add:

- final electrical design
- stringing
- PVGIS/pvlib yield
- final BOM
- Van der Valk structural approval
- edited panel placement UI

## Aesthetic scoring

The aesthetic score is a transparent heuristic, not a final design rule. It considers:

- row count
- row straightness
- orphan-panel count
- orientation mix penalty

The result is a pre-design ranking aid only.

## Manual override policy

Manual candidate override is allowed only if:

- the candidate ID exists in the generated candidate set
- the candidate has placements
- an override reason is provided
- existing final-mode/Q3/structural gates still pass

Override never bypasses product quality or structural truth.

## Candidate export

The new candidate export endpoint returns candidate summaries and selected placements from a stored calculation run. This creates a clean handoff for future yield/BOM/electrical phases.

## Truth boundary

Panel-packing results are layout evidence only. They are not final install approval.

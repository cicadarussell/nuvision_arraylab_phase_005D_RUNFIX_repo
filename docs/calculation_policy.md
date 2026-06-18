# Calculation Policy

Every calculation output must have:

- run ID
- software version
- engine version
- input snapshot hash
- product data snapshot ID
- assumption set ID
- source URLs / file hashes
- warning list
- status: preview, design draft, survey required, manufacturer calc required, quote ready, install pack ready

## Calculation run types

- catalogue_import
- datasheet_parse
- roof_geometry
- panel_packing
- shade
- yield
- stringing
- mounting_precheck
- bom
- quote

## Immutable rule

Old calculation runs are never edited. Recalculate means create a new run.

# Phase 001D - Product Snapshot Apply + Admin Review UI

## Purpose

Turn an approved spreadsheet snapshot into current product records without breaking the evidence chain.

The rule is still boring and correct:

- Upload does not mutate live product data.
- Stage does not mutate live product data.
- Approval creates an immutable ProductDataSnapshot.
- Preview apply creates a ProductSnapshotApplication diff.
- Apply updates the current Product table and writes immutable ProductVersion rows.
- Old snapshots and old calculation packets remain unchanged.

## New tables

### product_snapshot_applications

A controlled application record. It stores the preview diff and validation state before any live product changes occur.

### product_versions

An immutable version row for each product after each applied snapshot. Product is the current index; ProductVersion is the audit trail.

## New endpoints

- `POST /api/product-data-snapshots/{snapshot_id}/preview-apply`
- `GET /api/snapshot-applications`
- `GET /api/snapshot-applications/{application_id}`
- `POST /api/snapshot-applications/{application_id}/apply`
- `GET /api/catalogue/current-products`
- `GET /api/catalogue/current-products/{product_id}/versions`

## Design readiness rule in this phase

A product is only marked `design_ready=true` when:

1. product quality is Q3 or Q4, and
2. for panels, critical reviewed datasheet fields exist:
   - `power_stc_w`
   - `length_mm`
   - `width_mm`
   - `voc_v`
   - `vmp_v`
   - `isc_a`
   - `imp_a`

This is stricter than just trusting a spreadsheet field. Good. The spreadsheet is not king. Evidence is.

## Known limitations

- Prices/stock are staged but not materialised into dedicated price tables yet.
- Datasheet review rows create ProductSpec rows, but there is no full datasheet parser yet.
- Current Product is mutable by design, but every applied change writes ProductVersion rows.
- Old project/calculation snapshot linkage is designed but not yet fully connected to project UI.

## Pass gate

A NuVision user can now:

1. upload a spreadsheet,
2. validate and stage it,
3. approve it into an immutable product-data snapshot,
4. preview applying it to live product records,
5. apply it deliberately,
6. inspect current products and their immutable version history.

# Phase 001E: Product Apply Hardening + Price/Stock Snapshot Tables

## Purpose

This phase hardens the product-data spine so NuVision can update commercial data without corrupting engineering truth or old quote evidence.

ArrayLab now separates:

- product engineering truth: product specs, Q-levels, design readiness;
- commercial truth: prices, stock status, lead times, supplier priority;
- quote evidence: immutable copied price/stock payloads at the time of quote.

## New safety rules

1. Products are never silently deleted.
2. `deleted`, `delete`, `remove`, and `removed` statuses are blocked.
3. Product omission from a later import does not remove the current product.
4. Price/stock updates create new snapshot rows.
5. Old quote snapshots keep copied price/stock payloads and do not recalculate silently.
6. Rollback is non-destructive. It records intent and target, then future restoration must be a new forward action.

## New tables

| Table | Purpose |
|---|---|
| `price_stock_applications` | preview/apply records for commercial spreadsheet rows |
| `price_snapshots` | immutable price evidence rows |
| `stock_snapshots` | immutable stock/lead-time evidence rows |
| `commercial_quote_snapshots` | immutable quote packets copied from price/stock snapshots |
| `rollback_records` | non-destructive rollback markers |

## New endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/product-data-snapshots/{snapshot_id}/preview-price-stock` | preview price/stock import |
| `POST /api/price-stock-applications/{application_id}/apply` | apply price/stock snapshot rows |
| `GET /api/catalogue/price-snapshots/latest` | latest known price rows |
| `GET /api/catalogue/stock-snapshots/latest` | latest known stock rows |
| `GET /api/catalogue/search` | source-aware current product search |
| `POST /api/catalogue/current-products/{product_id}/validate-persistent` | persistent product validation |
| `POST /api/quotes/snapshot` | create immutable quote snapshot |
| `GET /api/quotes` | list quote snapshots |
| `POST /api/rollback-records` | create rollback marker |

## Verification

Phase 001E tests prove:

- price/stock preview does not mutate data;
- apply creates immutable price/stock snapshots;
- later price changes do not rewrite old quotes;
- omitted products do not vanish silently;
- destructive product delete statuses are blocked;
- seed catalogue can dry-run through staging and preview without going design-ready.

## Current boundary

This is still backend evidence infrastructure, not a solar design engine. No PVGIS, roof drawing, panel packing, inverter matching, or Van der Valk export is complete yet.

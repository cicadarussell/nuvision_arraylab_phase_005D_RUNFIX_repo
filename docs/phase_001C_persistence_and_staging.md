# Phase 001C - Persistence and Spreadsheet Staging

## Purpose

The goal is not speed. The goal is to stop bad data from entering the design engine.

This phase creates the controlled import spine:

1. Upload spreadsheet.
2. Hash file bytes.
3. Inspect workbook structure.
4. Validate sheets and headers.
5. Block protected engineering fields in editable sheets.
6. Stage non-empty rows.
7. Approve or reject import.
8. Approval creates an immutable product-data snapshot.
9. Old projects and old snapshots do not mutate.

## Critical rule

Approval does not mean every product field is engineering-grade. Approval means the spreadsheet state has been versioned and captured. Product specs still require Q3 reviewed datasheet provenance before final design use.

## Tables added

- `spreadsheet_imports`
- `staged_import_rows`
- `product_data_snapshots`

## Why snapshots matter

If NuVision changes stock, price, labour assumptions, or product preference later, old quotes must not silently change.

Old project records should point to the product-data snapshot used at the time. Recalculation with latest data must create a new calculation run and a new comparison, not rewrite history.

## Approved workflow

- Failed validation imports are stored, but cannot be approved.
- Staged imports can be approved once.
- Approved imports create one immutable product-data snapshot.
- Rejected imports record reviewer and optional reason.

## Current limitation

This phase does not yet apply staged rows into live product tables. That is deliberate. The next step should add an explicit "apply snapshot to product catalogue" workflow with extra review rules, rather than quietly mutating product records.

# Database Snapshot Policy

## Law

No old quote mutation.

## Product data snapshots

A product-data snapshot is a frozen evidence packet created from an approved spreadsheet import or later from a verified catalogue/datasheet ingest.

Every calculation run must store the snapshot ID used for its product data.

## Recalculation

When data changes, the user can run:

- original calculation using original snapshot,
- recalculation using latest snapshot,
- comparison report.

The original output remains unchanged.

## Snapshot content

A snapshot stores:

- source import ID,
- source file hash,
- staged row payloads,
- row hashes,
- row count,
- summary,
- reviewer identity.

## What snapshots do not prove

A snapshot does not prove that a panel spec is manufacturer-reviewed. Product fields still require field-level Q3/Q4 provenance before use in final design, stringing, BOM, or mounting workflows.

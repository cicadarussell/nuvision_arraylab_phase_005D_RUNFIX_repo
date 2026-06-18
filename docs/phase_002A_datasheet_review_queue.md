# Phase 002A: Datasheet Harvester + Review Queue

## Purpose

Turn manufacturer datasheets into controlled engineering evidence without letting parser guesses become final design data.

## Rules

1. Shop text remains Q0.
2. Archived PDFs are evidence, not truth by themselves.
3. Extracted values are Q2 candidates only.
4. A named reviewer is required to promote a candidate into Q3 ProductSpec.
5. Each promoted spec stores PDF hash, source page, source quote, extraction method, reviewer, and review time.
6. Duplicate datasheet hashes are not re-ingested for the same product.
7. Candidate values outside expected engineering ranges create warnings, not silent approval.

## Current parser scope

Phase 002A extracts common panel fields:

- `power_stc_w`
- `length_mm`
- `width_mm`
- `thickness_mm`
- `weight_kg`
- `voc_v`
- `vmp_v`
- `isc_a`
- `imp_a`
- `max_series_fuse_a`
- `max_system_voltage_v`

## Parser limits

This is not final datasheet intelligence. It handles native-text PDFs and common labels. It does not yet handle scanned-only PDFs, multilingual tables, complex multi-model datasheets, or manufacturer-specific table layouts robustly.

## Next hardening target

Phase 002B should add a PDF download queue, source registry, PyMuPDF table extraction / pdfplumber comparison, and a simple human review UI.

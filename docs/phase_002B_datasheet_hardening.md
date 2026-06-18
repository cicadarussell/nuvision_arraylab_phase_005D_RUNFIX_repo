# Phase 002B Datasheet Hardening Policy

## Purpose

Turn the Phase 002A datasheet parser into a safer review workflow.

The danger in PV product datasheets is not just bad OCR. It is **multi-model datasheets**: one PDF often contains several module ratings, sizes, currents, voltages, and mechanical variants. A naive parser can grab the wrong model column and quietly poison inverter/string maths.

## Source registry

Datasheet downloads must come from approved domains. The source registry is not final trust, but it blocks obvious rubbish.

Allowed states:

- `approved`
- `blocked`
- `deprecated`

## Candidate status

| Status | Meaning |
|---|---|
| `candidate` | Q2 parsed candidate, review required |
| `needs_model_review` | conflicting values found, corrected value required |
| `promoted` | reviewed into Q3 ProductSpec |
| `rejected` | rejected evidence, kept for audit |

## Conflict rule

If multiple distinct values are found for the same field, the candidate is marked `needs_model_review`.

Approve is blocked unless the reviewer provides:

- corrected value
- corrected unit where needed
- named reviewer
- reason naming the model/variant basis

## Batch review rule

Batch approve is blocked for conflict candidates. Batch reject is allowed.

## Table extraction

Phase 002B records table extraction attempts from:

- PyMuPDF `Page.find_tables()`
- pdfplumber `Page.extract_tables()`

Table extraction output is added to the parsing text layer, but still creates only Q2 candidates.

## Debugging requirement

Every datasheet archive stores:

- PDF hash
- text hash
- page count
- text character count
- table extractor reports
- candidate count
- conflict summary
- fields found


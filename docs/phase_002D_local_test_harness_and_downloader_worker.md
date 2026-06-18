# Phase 002D, Local Test Harness + Datasheet Downloader Worker

## Purpose

Phase 002D makes the project testable by a human before adding heavier solar design maths.

It also turns datasheet download jobs from inert queue rows into controlled worker-executable jobs.

## Research basis

- FastAPI `TestClient` is used for route smoke tests because it can test the ASGI app without opening a real socket.
- Uvicorn is used for local development because it supports `--reload`, host and port settings.
- Pytest remains the quality gate runner because it gives repeatable pass/fail exit codes.
- Vite/React remains the frontend dev path, but `local_test_harness.html` exists so Thomas can test the backend without fighting npm first.

## Worker states

| State | Meaning |
|---|---|
| `queued` | validated job waiting to run |
| `running` | worker is fetching/downloading |
| `succeeded` | PDF fetched and archived through datasheet pipeline |
| `failed` | error stored and visible |

## OCR rule

No-text PDFs create OCR queue jobs. OCR is not yet implemented.

When implemented, OCR output must create Q2 candidates only. It cannot promote directly into Q3 design truth.

## Debug endpoints

- `/api/debug/restore-check`
- `/api/debug/route-map`
- `/api/datasheet-download-jobs/debug`
- `/api/datasheet-ocr-jobs`

## Pass gate

A human can run the backend locally, open the test harness, inspect debug endpoints, run tests, and verify that controlled datasheet downloads and OCR placeholder handling do not bypass Q3 review.

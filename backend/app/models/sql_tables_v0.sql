-- NuVision ArrayLab Phase 001C SQL reference.
-- Production target is PostgreSQL + PostGIS. SQLite is supported for local tests.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    nuvision_sku TEXT,
    manufacturer TEXT NOT NULL,
    manufacturer_model TEXT,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    quality_level TEXT NOT NULL DEFAULT 'Q0_scraped',
    nuvision_url TEXT,
    design_ready BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE product_specs (
    spec_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    field_name TEXT NOT NULL,
    value_text TEXT,
    value_number DOUBLE PRECISION,
    unit TEXT,
    normalized_value_si DOUBLE PRECISION,
    normalized_unit TEXT,
    quality_level TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT,
    source_file_hash_sha256 TEXT,
    source_page INTEGER,
    source_text_quote TEXT,
    extraction_method TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    valid_from DATE,
    valid_to DATE,
    supersedes_spec_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE calculation_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    software_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    input_snapshot_hash_sha256 TEXT NOT NULL,
    output_hash_sha256 TEXT,
    product_data_snapshot_id TEXT,
    assumption_set_id TEXT,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_snapshot JSONB NOT NULL,
    output_snapshot JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE spreadsheet_imports (
    import_id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    file_hash_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploaded',
    uploaded_by TEXT,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    rejected_by TEXT,
    rejected_at TIMESTAMPTZ,
    validation_report JSONB NOT NULL DEFAULT '{}'::jsonb,
    diff_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE staged_import_rows (
    staged_row_id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL REFERENCES spreadsheet_imports(import_id),
    sheet_name TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    row_hash_sha256 TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_staged_row_import_sheet_row UNIQUE (import_id, sheet_name, row_number)
);

CREATE TABLE product_data_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL REFERENCES spreadsheet_imports(import_id),
    content_hash_sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    snapshot_payload JSONB NOT NULL,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

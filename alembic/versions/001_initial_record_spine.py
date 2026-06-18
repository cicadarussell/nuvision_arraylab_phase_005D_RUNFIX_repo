"""initial engineering record spine

Revision ID: 001_record_spine
Revises:
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "001_record_spine"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("product_id", sa.String(), primary_key=True),
        sa.Column("nuvision_sku", sa.String(), nullable=True),
        sa.Column("manufacturer", sa.String(), nullable=False),
        sa.Column("manufacturer_model", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("quality_level", sa.String(), nullable=False, server_default="Q0_scraped"),
        sa.Column("nuvision_url", sa.Text(), nullable=True),
        sa.Column("design_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "product_specs",
        sa.Column("spec_id", sa.String(), primary_key=True),
        sa.Column("product_id", sa.String(), sa.ForeignKey("products.product_id"), nullable=False),
        sa.Column("field_name", sa.String(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_number", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("normalized_value_si", sa.Float(), nullable=True),
        sa.Column("normalized_unit", sa.String(), nullable=True),
        sa.Column("quality_level", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_file_hash_sha256", sa.String(), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_text_quote", sa.Text(), nullable=True),
        sa.Column("extraction_method", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(), nullable=False, server_default="unreviewed"),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_from", sa.String(), nullable=True),
        sa.Column("valid_to", sa.String(), nullable=True),
        sa.Column("supersedes_spec_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "calculation_runs",
        sa.Column("run_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("run_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("software_version", sa.String(), nullable=False),
        sa.Column("engine_version", sa.String(), nullable=False),
        sa.Column("input_snapshot_hash_sha256", sa.String(), nullable=False),
        sa.Column("output_hash_sha256", sa.String(), nullable=True),
        sa.Column("product_data_snapshot_id", sa.String(), nullable=True),
        sa.Column("assumption_set_id", sa.String(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("output_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "spreadsheet_imports",
        sa.Column("import_id", sa.String(), primary_key=True),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("file_hash_sha256", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="uploaded"),
        sa.Column("uploaded_by", sa.String(), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.String(), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("diff_summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "staged_import_rows",
        sa.Column("staged_row_id", sa.String(), primary_key=True),
        sa.Column("import_id", sa.String(), sa.ForeignKey("spreadsheet_imports.import_id"), nullable=False),
        sa.Column("sheet_name", sa.String(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("row_hash_sha256", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("import_id", "sheet_name", "row_number", name="uq_staged_row_import_sheet_row"),
    )
    op.create_table(
        "product_data_snapshots",
        sa.Column("snapshot_id", sa.String(), primary_key=True),
        sa.Column("import_id", sa.String(), sa.ForeignKey("spreadsheet_imports.import_id"), nullable=False),
        sa.Column("content_hash_sha256", sa.String(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("product_data_snapshots")
    op.drop_table("staged_import_rows")
    op.drop_table("spreadsheet_imports")
    op.drop_table("calculation_runs")
    op.drop_table("product_specs")
    op.drop_table("products")

"""phase001E commercial snapshots and non-destructive rollback records

Revision ID: 003_commercial_snapshots
Revises: 002_product_snapshot_application
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "003_commercial_snapshots"
down_revision = "002_product_snapshot_application"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_stock_applications",
        sa.Column("application_id", sa.String(), primary_key=True),
        sa.Column("snapshot_id", sa.String(), sa.ForeignKey("product_data_snapshots.snapshot_id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="previewed"),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("applied_by", sa.String(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preview_hash_sha256", sa.String(), nullable=False),
        sa.Column("diff_summary", sa.JSON(), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "price_snapshots",
        sa.Column("price_snapshot_id", sa.String(), primary_key=True),
        sa.Column("product_id", sa.String(), nullable=False, index=True),
        sa.Column("application_id", sa.String(), sa.ForeignKey("price_stock_applications.application_id"), nullable=False),
        sa.Column("source_snapshot_id", sa.String(), sa.ForeignKey("product_data_snapshots.snapshot_id"), nullable=False),
        sa.Column("source_row_hash_sha256", sa.String(), nullable=False),
        sa.Column("trade_price_gbp", sa.Float(), nullable=True),
        sa.Column("list_price_gbp", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False, server_default="GBP"),
        sa.Column("payload_hash_sha256", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "stock_snapshots",
        sa.Column("stock_snapshot_id", sa.String(), primary_key=True),
        sa.Column("product_id", sa.String(), nullable=False, index=True),
        sa.Column("application_id", sa.String(), sa.ForeignKey("price_stock_applications.application_id"), nullable=False),
        sa.Column("source_snapshot_id", sa.String(), sa.ForeignKey("product_data_snapshots.snapshot_id"), nullable=False),
        sa.Column("source_row_hash_sha256", sa.String(), nullable=False),
        sa.Column("stock_status", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("stock_quantity", sa.Integer(), nullable=True),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        sa.Column("supplier_priority", sa.String(), nullable=True),
        sa.Column("payload_hash_sha256", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "commercial_quote_snapshots",
        sa.Column("quote_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("quote_payload_hash_sha256", sa.String(), nullable=False),
        sa.Column("quote_payload", sa.JSON(), nullable=False),
        sa.Column("price_snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("stock_snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "rollback_records",
        sa.Column("rollback_id", sa.String(), primary_key=True),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("rollback_kind", sa.String(), nullable=False, server_default="non_destructive_marker"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=True),
        sa.Column("target_hash_sha256", sa.String(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("rollback_records")
    op.drop_table("commercial_quote_snapshots")
    op.drop_table("stock_snapshots")
    op.drop_table("price_snapshots")
    op.drop_table("price_stock_applications")

"""product snapshot application and immutable product versions

Revision ID: 002_product_snapshot_application
Revises: 001_initial_record_spine
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002_product_snapshot_application"
down_revision = "001_record_spine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_snapshot_applications",
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
        "product_versions",
        sa.Column("version_id", sa.String(), primary_key=True),
        sa.Column("product_id", sa.String(), sa.ForeignKey("products.product_id"), nullable=False),
        sa.Column("application_id", sa.String(), sa.ForeignKey("product_snapshot_applications.application_id"), nullable=False),
        sa.Column("snapshot_id", sa.String(), sa.ForeignKey("product_data_snapshots.snapshot_id"), nullable=False),
        sa.Column("source_row_hash_sha256", sa.String(), nullable=False),
        sa.Column("product_payload_hash_sha256", sa.String(), nullable=False),
        sa.Column("product_payload", sa.JSON(), nullable=False),
        sa.Column("revision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("product_id", "application_id", name="uq_product_version_product_application"),
    )


def downgrade() -> None:
    op.drop_table("product_versions")
    op.drop_table("product_snapshot_applications")

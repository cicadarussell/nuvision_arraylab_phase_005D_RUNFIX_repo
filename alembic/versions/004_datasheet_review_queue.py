"""phase002A datasheet archive and review queue

Revision ID: 004_datasheet_review_queue
Revises: 003_commercial_snapshots
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "004_datasheet_review_queue"
down_revision = "003_commercial_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasheet_files",
        sa.Column("datasheet_id", sa.String(), primary_key=True),
        sa.Column("product_id", sa.String(), nullable=True, index=True),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(), nullable=False, server_default="manufacturer_datasheet"),
        sa.Column("file_hash_sha256", sa.String(), nullable=False, index=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text_hash_sha256", sa.String(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="archived"),
        sa.Column("extraction_report", sa.JSON(), nullable=False),
        sa.Column("uploaded_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "datasheet_candidate_specs",
        sa.Column("candidate_id", sa.String(), primary_key=True),
        sa.Column("datasheet_id", sa.String(), sa.ForeignKey("datasheet_files.datasheet_id"), nullable=False, index=True),
        sa.Column("product_id", sa.String(), nullable=True, index=True),
        sa.Column("field_name", sa.String(), nullable=False, index=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_number", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("normalized_value_si", sa.Float(), nullable=True),
        sa.Column("normalized_unit", sa.String(), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_text_quote", sa.Text(), nullable=True),
        sa.Column("extraction_method", sa.String(), nullable=False, server_default="regex_text"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="candidate"),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("created_spec_id", sa.String(), nullable=True),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "datasheet_review_records",
        sa.Column("review_id", sa.String(), primary_key=True),
        sa.Column("candidate_id", sa.String(), sa.ForeignKey("datasheet_candidate_specs.candidate_id"), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("reviewer", sa.String(), nullable=False),
        sa.Column("corrected_value_text", sa.Text(), nullable=True),
        sa.Column("corrected_value_number", sa.Float(), nullable=True),
        sa.Column("corrected_unit", sa.String(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_spec_id", sa.String(), nullable=True),
        sa.Column("review_payload_hash_sha256", sa.String(), nullable=False),
        sa.Column("review_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("datasheet_review_records")
    op.drop_table("datasheet_candidate_specs")
    op.drop_table("datasheet_files")

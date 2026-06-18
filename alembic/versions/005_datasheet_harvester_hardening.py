"""datasheet harvester hardening

Revision ID: 005_datasheet_hardening
Revises: 004_datasheet_review_queue
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "005_datasheet_hardening"
down_revision = "004_datasheet_review_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasheet_source_domains",
        sa.Column("domain_id", sa.String(), primary_key=True),
        sa.Column("domain", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_datasheet_source_domains_domain", "datasheet_source_domains", ["domain"])
    op.create_table(
        "datasheet_download_jobs",
        sa.Column("job_id", sa.String(), primary_key=True),
        sa.Column("product_id", sa.String(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_domain", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=True),
        sa.Column("datasheet_id", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_datasheet_download_jobs_product_id", "datasheet_download_jobs", ["product_id"])
    op.create_index("ix_datasheet_download_jobs_source_domain", "datasheet_download_jobs", ["source_domain"])


def downgrade() -> None:
    op.drop_index("ix_datasheet_download_jobs_source_domain", table_name="datasheet_download_jobs")
    op.drop_index("ix_datasheet_download_jobs_product_id", table_name="datasheet_download_jobs")
    op.drop_table("datasheet_download_jobs")
    op.drop_index("ix_datasheet_source_domains_domain", table_name="datasheet_source_domains")
    op.drop_table("datasheet_source_domains")

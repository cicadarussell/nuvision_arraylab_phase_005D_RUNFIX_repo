"""Phase 002D datasheet downloader worker and OCR queue.

Revision ID: 006_datasheet_downloader_worker_and_ocr_queue
Revises: 005_datasheet_harvester_hardening
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "006_datasheet_downloader_worker_and_ocr_queue"
down_revision = "005_datasheet_harvester_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("datasheet_download_jobs") as batch:
        batch.add_column(sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "datasheet_ocr_jobs",
        sa.Column("ocr_job_id", sa.String(), primary_key=True),
        sa.Column("datasheet_id", sa.String(), sa.ForeignKey("datasheet_files.datasheet_id"), nullable=False, index=True),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("engine", sa.String(), nullable=True),
        sa.Column("output_text_hash_sha256", sa.String(), nullable=True),
        sa.Column("validation_report", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("requested_by", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("datasheet_ocr_jobs")
    with op.batch_alter_table("datasheet_download_jobs") as batch:
        batch.drop_column("last_attempt_at")
        batch.drop_column("retry_count")

"""pvgis request cache

Revision ID: 010_pvgis_request_cache
Revises: 009_yield_assumption_sets
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "010_pvgis_request_cache"
down_revision = "009_yield_assumption_sets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pvgis_request_cache",
        sa.Column("request_hash_sha256", sa.String(), primary_key=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("adapter_version", sa.String(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("annual_kwh", sa.Float(), nullable=True),
        sa.Column("parsed_monthly", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("response_hash_sha256", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("url_preview", sa.Text(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("pvgis_request_cache")

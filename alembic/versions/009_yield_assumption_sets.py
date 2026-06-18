"""yield assumption sets

Revision ID: 009_yield_assumption_sets
Revises: 008_panel_packing_override_governance
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "009_yield_assumption_sets"
down_revision = "008_panel_packing_override_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "yield_assumption_sets",
        sa.Column("assumption_set_id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("model_tier", sa.String(), nullable=False, server_default="T0_rough_kwh_per_kwp"),
        sa.Column("specific_yield_kwh_per_kwp_year", sa.Float(), nullable=False),
        sa.Column("system_loss_pct", sa.Float(), nullable=False, server_default="14.0"),
        sa.Column("shade_loss_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("degradation_year1_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("albedo", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("review_status", sa.String(), nullable=False, server_default="preview_default"),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("yield_assumption_sets")

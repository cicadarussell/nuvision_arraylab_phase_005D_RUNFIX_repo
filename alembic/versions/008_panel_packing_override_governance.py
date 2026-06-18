"""panel packing override governance

Revision ID: 008_panel_packing_override_governance
Revises: 007_project_site_roof_geometry
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "008_panel_packing_override_governance"
down_revision = "007_project_site_roof_geometry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "panel_packing_overrides",
        sa.Column("override_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False, index=True),
        sa.Column("calculation_run_id", sa.String(), sa.ForeignKey("calculation_runs.run_id"), nullable=False, index=True),
        sa.Column("selected_candidate_id", sa.String(), nullable=False, index=True),
        sa.Column("selected_candidate_hash_sha256", sa.String(), nullable=False),
        sa.Column("selected_layout_export_hash_sha256", sa.String(), nullable=False),
        sa.Column("intended_use", sa.String(), nullable=False, server_default="preview"),
        sa.Column("reviewer", sa.String(), nullable=False),
        sa.Column("reviewer_role", sa.String(), nullable=False),
        sa.Column("override_reason", sa.Text(), nullable=False),
        sa.Column("override_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_panel_packing_overrides_project_id", "panel_packing_overrides", ["project_id"])
    op.create_index("ix_panel_packing_overrides_calculation_run_id", "panel_packing_overrides", ["calculation_run_id"])
    op.create_index("ix_panel_packing_overrides_selected_candidate_id", "panel_packing_overrides", ["selected_candidate_id"])


def downgrade() -> None:
    op.drop_index("ix_panel_packing_overrides_selected_candidate_id", table_name="panel_packing_overrides")
    op.drop_index("ix_panel_packing_overrides_calculation_run_id", table_name="panel_packing_overrides")
    op.drop_index("ix_panel_packing_overrides_project_id", table_name="panel_packing_overrides")
    op.drop_table("panel_packing_overrides")

"""project site roof geometry spine

Revision ID: 007_project_geometry
Revises: 006_datasheet_downloader_worker_and_ocr_queue
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "007_project_geometry"
down_revision = "006_datasheet_downloader_worker_and_ocr_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("customer_ref", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "project_version_snapshots",
        sa.Column("project_snapshot_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("snapshot_kind", sa.String(), nullable=False, server_default="geometry"),
        sa.Column("snapshot_hash_sha256", sa.String(), nullable=False),
        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_project_snapshots_project_id", "project_version_snapshots", ["project_id"])
    op.create_index("ix_project_snapshots_hash", "project_version_snapshots", ["snapshot_hash_sha256"])
    op.create_table(
        "sites",
        sa.Column("site_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("postcode", sa.String(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=False, server_default="Europe/London"),
        sa.Column("source_type", sa.String(), nullable=False, server_default="manual"),
        sa.Column("source_confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sites_project_id", "sites", ["project_id"])
    op.create_table(
        "roof_planes",
        sa.Column("roof_plane_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("roof_type", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("pitch_deg", sa.Float(), nullable=False),
        sa.Column("azimuth_deg", sa.Float(), nullable=False),
        sa.Column("height_m", sa.Float(), nullable=True),
        sa.Column("polygon_local_m", sa.JSON(), nullable=True),
        sa.Column("area_m2", sa.Float(), nullable=True),
        sa.Column("edge_zone_depth_m", sa.Float(), nullable=True),
        sa.Column("source_type", sa.String(), nullable=False, server_default="manual"),
        sa.Column("source_confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_roof_planes_project_id", "roof_planes", ["project_id"])
    op.create_table(
        "obstructions",
        sa.Column("obstruction_id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("roof_plane_id", sa.String(), nullable=True),
        sa.Column("obstruction_type", sa.String(), nullable=False, server_default="manual_block"),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("height_m", sa.Float(), nullable=True),
        sa.Column("polygon_local_m", sa.JSON(), nullable=True),
        sa.Column("centre_local_m", sa.JSON(), nullable=True),
        sa.Column("source_type", sa.String(), nullable=False, server_default="manual"),
        sa.Column("source_confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_obstructions_project_id", "obstructions", ["project_id"])
    op.create_index("ix_obstructions_roof_plane_id", "obstructions", ["roof_plane_id"])


def downgrade() -> None:
    op.drop_index("ix_obstructions_roof_plane_id", table_name="obstructions")
    op.drop_index("ix_obstructions_project_id", table_name="obstructions")
    op.drop_table("obstructions")
    op.drop_index("ix_roof_planes_project_id", table_name="roof_planes")
    op.drop_table("roof_planes")
    op.drop_index("ix_sites_project_id", table_name="sites")
    op.drop_table("sites")
    op.drop_index("ix_project_snapshots_hash", table_name="project_version_snapshots")
    op.drop_index("ix_project_snapshots_project_id", table_name="project_version_snapshots")
    op.drop_table("project_version_snapshots")
    op.drop_table("projects")

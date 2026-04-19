"""Add pipeline_stages table for resumable pipeline (PIP-1).

Revision ID: 003_pipeline_stages
Revises: 002_structured_minutes
Create Date: 2026-04-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "003_pipeline_stages"
down_revision = "002_structured_minutes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_stages",
        sa.Column(
            "meeting_id",
            sa.String,
            sa.ForeignKey("meetings.meeting_id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("stage", sa.String, primary_key=True, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("last_error_at", sa.DateTime, nullable=True),
        sa.Column("artifact_path", sa.Text, nullable=True),
        sa.CheckConstraint(
            "stage IN ('capture','transcribe','diarize','generate','ingest','embed','export')",
            name="ck_pipeline_stages_stage",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','succeeded','failed','skipped')",
            name="ck_pipeline_stages_status",
        ),
    )
    op.create_index(
        "idx_pipeline_stages_status",
        "pipeline_stages",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("idx_pipeline_stages_status", table_name="pipeline_stages")
    op.drop_table("pipeline_stages")

"""Add person_voice_samples table for passive speaker centroid learning (SPK-1).

Revision ID: 004_voice_samples
Revises: 003_pipeline_stages
Create Date: 2026-04-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "004_voice_samples"
down_revision = "003_pipeline_stages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "person_voice_samples",
        sa.Column("sample_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "person_id",
            sa.String,
            sa.ForeignKey("persons.person_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "meeting_id",
            sa.String,
            sa.ForeignKey("meetings.meeting_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cluster_id", sa.String, nullable=False),
        sa.Column("embedding", sa.LargeBinary, nullable=False),
        sa.Column("embedding_dim", sa.Integer, nullable=False),
        sa.Column(
            "confirmed",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint(
            "meeting_id",
            "cluster_id",
            "person_id",
            name="uq_voice_samples_meeting_cluster_person",
        ),
    )
    op.create_index(
        "idx_voice_samples_person",
        "person_voice_samples",
        ["person_id", "confirmed"],
    )


def downgrade() -> None:
    op.drop_index("idx_voice_samples_person", table_name="person_voice_samples")
    op.drop_table("person_voice_samples")

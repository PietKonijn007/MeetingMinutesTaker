"""Add meeting_series + meeting_series_members + topic_clusters_cache (REC-1, ANA-1).

Revision ID: 005_meeting_series
Revises: 004_voice_samples
Create Date: 2026-04-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "005_meeting_series"
down_revision = "004_voice_samples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_series",
        sa.Column("series_id", sa.String, primary_key=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("meeting_type", sa.String, nullable=False),
        sa.Column("cadence", sa.String, nullable=True),
        sa.Column("attendee_hash", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("last_detected_at", sa.DateTime, nullable=False),
    )
    op.create_index(
        "idx_series_signature",
        "meeting_series",
        ["attendee_hash", "meeting_type"],
        unique=True,
    )

    op.create_table(
        "meeting_series_members",
        sa.Column(
            "series_id",
            sa.String,
            sa.ForeignKey("meeting_series.series_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "meeting_id",
            sa.String,
            sa.ForeignKey("meetings.meeting_id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # ANA-1 topic clusters cache. Rebuilt by `mm stats rebuild` or on demand.
    op.create_table(
        "topic_clusters_cache",
        sa.Column("cluster_id", sa.String, nullable=False),
        sa.Column("chunk_id", sa.Integer, nullable=False),
        sa.Column("meeting_id", sa.String, nullable=False),
        sa.Column("topic_summary", sa.Text, nullable=False),
        sa.Column("rebuilt_at", sa.DateTime, nullable=False),
        sa.PrimaryKeyConstraint("cluster_id", "chunk_id"),
    )
    op.create_index(
        "idx_topic_clusters_meeting",
        "topic_clusters_cache",
        ["meeting_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_topic_clusters_meeting", table_name="topic_clusters_cache")
    op.drop_table("topic_clusters_cache")
    op.drop_table("meeting_series_members")
    op.drop_index("idx_series_signature", table_name="meeting_series")
    op.drop_table("meeting_series")

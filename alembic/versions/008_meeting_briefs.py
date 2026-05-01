"""Add meeting_briefs table (BRF-2).

Revision ID: 008_meeting_briefs
Revises: 007_attachments
Create Date: 2026-05-01

Per spec/10-meeting-prep-brief.md — single table that caches generated
prep briefs so identical re-requests skip retrieval and the LLM.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "008_meeting_briefs"
down_revision = "007_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_briefs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("attendee_set_hash", sa.String, nullable=False),
        sa.Column("topic", sa.Text, nullable=True),
        sa.Column("topic_hash", sa.String, nullable=True),
        sa.Column("focus_items", sa.Text, nullable=True),
        sa.Column("focus_items_hash", sa.String, nullable=False),
        sa.Column("meeting_type", sa.String, nullable=True),
        sa.Column("markdown_path", sa.Text, nullable=False),
        sa.Column("json_path", sa.Text, nullable=False),
        sa.Column("generated_at", sa.DateTime, nullable=False),
        sa.Column("model", sa.String, nullable=True),
        sa.Column("source_meeting_ids", sa.Text, nullable=True),
        sa.Column(
            "superseded_by",
            sa.Integer,
            sa.ForeignKey("meeting_briefs.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_meeting_briefs_attendee_set",
        "meeting_briefs",
        ["attendee_set_hash"],
    )
    op.create_index(
        "idx_meeting_briefs_cache_key",
        "meeting_briefs",
        [
            "attendee_set_hash",
            "topic_hash",
            "focus_items_hash",
            "generated_at",
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_meeting_briefs_cache_key", table_name="meeting_briefs")
    op.drop_index("idx_meeting_briefs_attendee_set", table_name="meeting_briefs")
    op.drop_table("meeting_briefs")

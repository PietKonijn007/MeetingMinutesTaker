"""Add annotations table for MCP agent notes.

Revision ID: 009_annotations
Revises: 008_meeting_briefs
Create Date: 2026-05-16

Stores free-form notes/annotations attached to meetings by users or
MCP agents. Inspired by Mindle's document annotation model.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "009_annotations"
down_revision = "008_meeting_briefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "annotations",
        sa.Column("annotation_id", sa.String, primary_key=True),
        sa.Column(
            "meeting_id",
            sa.String,
            sa.ForeignKey("meetings.meeting_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("label", sa.String, nullable=True),
        sa.Column("author", sa.String, nullable=False, server_default="mcp"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default="0"),
    )
    op.create_index("idx_annotations_meeting", "annotations", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("idx_annotations_meeting")
    op.drop_table("annotations")

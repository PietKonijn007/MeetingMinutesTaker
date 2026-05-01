"""Add attachments table.

Revision ID: 007_attachments
Revises: 006_action_proposal_state
Create Date: 2026-05-01

Per spec/09-attachments.md — single table holding lightweight metadata
for files / links / images attached to a meeting. Heavy content (extracted
text, LLM summary) lives in a sidecar markdown file at
``data/attachments/{meeting_id}/{attachment_id}.md`` so the DB row stays
small and the markdown survives DB rebuilds.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "007_attachments"
down_revision = "006_action_proposal_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("attachment_id", sa.String, primary_key=True),
        sa.Column(
            "meeting_id",
            sa.String,
            sa.ForeignKey("meetings.meeting_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("original_filename", sa.String, nullable=True),
        sa.Column("mime_type", sa.String, nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=True),
        sa.Column("sha256", sa.String, nullable=True),
        sa.Column("url", sa.String, nullable=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.String,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.CheckConstraint(
            "kind IN ('file','link','image')",
            name="ck_attachments_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending','extracting','summarizing','ready','error')",
            name="ck_attachments_status",
        ),
    )
    op.create_index(
        "idx_attachments_meeting",
        "attachments",
        ["meeting_id"],
    )
    # Same (meeting, sha256) is silently rejected as a duplicate upload by
    # the API; enforcing it at the DB level keeps that guarantee even when
    # other callers (CLI, tests) bypass the handler.
    op.create_index(
        "idx_attachments_meeting_sha",
        "attachments",
        ["meeting_id", "sha256"],
        unique=True,
        sqlite_where=sa.text("sha256 IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_attachments_meeting_sha", table_name="attachments")
    op.drop_index("idx_attachments_meeting", table_name="attachments")
    op.drop_table("attachments")

"""Initial schema — all tables and FTS5 virtual table.

Revision ID: 001
Revises:
Create Date: 2025-01-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # meetings table
    op.create_table(
        "meetings",
        sa.Column("meeting_id", sa.String, primary_key=True),
        sa.Column("title", sa.String, nullable=True),
        sa.Column("date", sa.DateTime, nullable=True),
        sa.Column("duration", sa.String, nullable=True),
        sa.Column("platform", sa.String, nullable=True),
        sa.Column("meeting_type", sa.String, nullable=True),
        sa.Column("organizer", sa.String, nullable=True),
        sa.Column("status", sa.String, default="draft"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    # persons table
    op.create_table(
        "persons",
        sa.Column("person_id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=True),
        sa.Column("email", sa.String, unique=True, nullable=True),
    )

    # meeting_attendees association table
    op.create_table(
        "meeting_attendees",
        sa.Column("meeting_id", sa.String, sa.ForeignKey("meetings.meeting_id")),
        sa.Column("person_id", sa.String, sa.ForeignKey("persons.person_id")),
    )

    # transcripts table
    op.create_table(
        "transcripts",
        sa.Column("meeting_id", sa.String, sa.ForeignKey("meetings.meeting_id"), primary_key=True),
        sa.Column("full_text", sa.Text, nullable=True),
        sa.Column("language", sa.String, nullable=True),
        sa.Column("audio_file_path", sa.String, nullable=True),
    )

    # minutes table
    op.create_table(
        "minutes",
        sa.Column("meeting_id", sa.String, sa.ForeignKey("meetings.meeting_id"), primary_key=True),
        sa.Column("minutes_id", sa.String, unique=True, nullable=True),
        sa.Column("markdown_content", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("generated_at", sa.DateTime, nullable=True),
        sa.Column("llm_model", sa.String, nullable=True),
        sa.Column("review_status", sa.String, default="draft"),
    )

    # action_items table
    op.create_table(
        "action_items",
        sa.Column("action_item_id", sa.String, primary_key=True),
        sa.Column("meeting_id", sa.String, sa.ForeignKey("meetings.meeting_id")),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("owner", sa.String, nullable=True),
        sa.Column("due_date", sa.String, nullable=True),
        sa.Column("status", sa.String, default="open"),
        sa.Column("mentioned_at_seconds", sa.Float, nullable=True),
    )

    # decisions table
    op.create_table(
        "decisions",
        sa.Column("decision_id", sa.String, primary_key=True),
        sa.Column("meeting_id", sa.String, sa.ForeignKey("meetings.meeting_id")),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("made_by", sa.String, nullable=True),
        sa.Column("mentioned_at_seconds", sa.Float, nullable=True),
    )

    # FTS5 virtual table (raw SQL, not supported by Alembic autogenerate)
    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS meetings_fts USING fts5(
            meeting_id UNINDEXED,
            title,
            transcript_text,
            minutes_text,
            tokenize='porter unicode61'
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS meetings_fts")
    op.drop_table("decisions")
    op.drop_table("action_items")
    op.drop_table("minutes")
    op.drop_table("transcripts")
    op.drop_table("meeting_attendees")
    op.drop_table("persons")
    op.drop_table("meetings")

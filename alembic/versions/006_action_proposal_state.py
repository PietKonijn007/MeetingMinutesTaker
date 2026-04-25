"""Add proposal_state to action_items (proposed → confirmed review workflow).

Revision ID: 006_action_proposal_state
Revises: 005_meeting_series
Create Date: 2026-04-25

Existing rows are backfilled to ``proposed`` so the user can re-review
historical action items in the new workflow.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "006_action_proposal_state"
down_revision = "005_meeting_series"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "action_items",
        sa.Column(
            "proposal_state",
            sa.String,
            nullable=False,
            server_default="proposed",
        ),
    )
    op.execute("UPDATE action_items SET proposal_state = 'proposed'")


def downgrade() -> None:
    op.drop_column("action_items", "proposal_state")

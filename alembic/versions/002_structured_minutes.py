"""Add structured minutes columns.

Revision ID: 002_structured_minutes
Revises: 001_initial_schema
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "002_structured_minutes"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("minutes", sa.Column("sentiment", sa.String(), nullable=True))
    op.add_column("minutes", sa.Column("structured_json", sa.Text(), nullable=True))
    op.add_column("action_items", sa.Column("priority", sa.String(), nullable=True))
    op.add_column("decisions", sa.Column("rationale", sa.Text(), nullable=True))
    op.add_column("decisions", sa.Column("confidence", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("decisions", "confidence")
    op.drop_column("decisions", "rationale")
    op.drop_column("action_items", "priority")
    op.drop_column("minutes", "structured_json")
    op.drop_column("minutes", "sentiment")

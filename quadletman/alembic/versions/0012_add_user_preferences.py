"""add_user_preferences

Revision ID: 0012
Revises: 0011
Create Date: 2026-03-31

Adds a user_preferences table for per-user UI settings (theme, etc.).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("theme", sa.Text(), nullable=False, server_default="dark"),
        sa.PrimaryKeyConstraint("username"),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")

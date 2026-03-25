"""add_agent_last_seen

Revision ID: 0010
Revises: 0009
Create Date: 2026-03-25

Adds agent_last_seen column to compartments table to track when the
per-user monitoring agent last reported successfully.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("compartments") as batch_op:
        batch_op.add_column(sa.Column("agent_last_seen", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("compartments") as batch_op:
        batch_op.drop_column("agent_last_seen")

"""add_operations_table

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-13

Adds an operations table for queued lifecycle operations (start/stop/restart/resync).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operations",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("compartment_id", sa.Text(), nullable=False),
        sa.Column("op_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("submitted_by", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"),
        ),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["compartment_id"], ["compartments.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_operations_compartment", "operations", ["compartment_id"])
    op.create_index("ix_operations_status", "operations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_operations_status")
    op.drop_index("ix_operations_compartment")
    op.drop_table("operations")

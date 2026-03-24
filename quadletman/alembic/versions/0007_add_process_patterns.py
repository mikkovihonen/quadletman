"""add_process_patterns

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-24

Adds a process_patterns table for regex-based process cmdline matching,
and a pattern_id FK + segments_json column on the processes table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "process_patterns",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("process_name", sa.Text, nullable=False),
        sa.Column("cmdline_pattern", sa.Text, nullable=False),
        sa.Column("segments_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
        ),
        sa.UniqueConstraint("compartment_id", "process_name", "cmdline_pattern"),
    )

    with op.batch_alter_table("processes") as batch_op:
        batch_op.add_column(sa.Column("pattern_id", sa.Text, nullable=True))
        batch_op.create_foreign_key(
            "fk_processes_pattern_id",
            "process_patterns",
            ["pattern_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("processes") as batch_op:
        batch_op.drop_constraint("fk_processes_pattern_id", type_="foreignkey")
        batch_op.drop_column("pattern_id")

    op.drop_table("process_patterns")

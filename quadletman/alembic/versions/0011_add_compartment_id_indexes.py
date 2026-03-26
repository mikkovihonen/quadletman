"""add_compartment_id_indexes

Revision ID: 0011
Revises: 0010
Create Date: 2026-03-26

Adds indexes on compartment_id foreign key columns for tables that were
missing them.  Processes and connections already had indexes; all others
performed full table scans on compartment_id WHERE clauses.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables that need a compartment_id index.
# Excluded: processes, connections (already indexed), kubes, artifacts (indexed in 0002).
_TABLES = [
    "networks",
    "containers",
    "volumes",
    "pods",
    "images",
    "builds",
    "system_events",
    "secrets",
    "timers",
    "notification_hooks",
    "metrics_history",
    "process_patterns",
    "connection_allowlist_rules",
]


def upgrade() -> None:
    conn = op.get_bind()
    for table in _TABLES:
        idx_name = f"ix_{table}_compartment_id"
        exists = conn.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='index' AND name=:name"),
            {"name": idx_name},
        ).scalar()
        if not exists:
            with op.batch_alter_table(table) as batch_op:
                batch_op.create_index(idx_name, ["compartment_id"])


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_compartment_id")

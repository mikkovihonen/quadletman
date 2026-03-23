"""rename_whitelist_to_allowlist

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-23

Renames the connection_whitelist_rules table to connection_allowlist_rules
to align with inclusive terminology.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("connection_whitelist_rules", "connection_allowlist_rules")


def downgrade() -> None:
    op.rename_table("connection_allowlist_rules", "connection_whitelist_rules")

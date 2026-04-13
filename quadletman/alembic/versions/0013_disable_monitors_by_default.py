"""disable_monitors_by_default

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-13

Change the server_default for connection_monitor_enabled and
process_monitor_enabled from "1" (True) to "0" (False) so new
compartments have monitoring disabled by default.  Existing rows
are not modified — only the default for future INSERTs changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("compartments") as batch_op:
        batch_op.alter_column("connection_monitor_enabled", server_default="0")
        batch_op.alter_column("process_monitor_enabled", server_default="0")


def downgrade() -> None:
    with op.batch_alter_table("compartments") as batch_op:
        batch_op.alter_column("connection_monitor_enabled", server_default="1")
        batch_op.alter_column("process_monitor_enabled", server_default="1")

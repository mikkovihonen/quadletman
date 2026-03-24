"""add_missing_quadlet_keys

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-24

Adds missing upstream Quadlet keys:
- artifacts: auth_file, cert_dir, containers_conf_module, creds, decryption_key,
  global_args, podman_args, quiet, retry, retry_delay, tls_verify
- containers: container_name (ContainerName override)
- pods: pod_name_override (PodName override)
- volumes: volume_name (VolumeName override)
- networks: network_name (NetworkName override)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- artifacts: full field set ---
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.add_column(sa.Column("auth_file", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("cert_dir", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("containers_conf_module", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("creds", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("decryption_key", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("global_args", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("podman_args", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("quiet", sa.Boolean, nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("retry", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("retry_delay", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("tls_verify", sa.Boolean, nullable=False, server_default="1"))

    # --- containers: ContainerName override ---
    with op.batch_alter_table("containers") as batch_op:
        batch_op.add_column(sa.Column("container_name", sa.Text, nullable=False, server_default=""))

    # --- pods: PodName override ---
    with op.batch_alter_table("pods") as batch_op:
        batch_op.add_column(
            sa.Column("pod_name_override", sa.Text, nullable=False, server_default="")
        )

    # --- volumes: VolumeName override ---
    with op.batch_alter_table("volumes") as batch_op:
        batch_op.add_column(sa.Column("volume_name", sa.Text, nullable=False, server_default=""))

    # --- networks: NetworkName override ---
    with op.batch_alter_table("networks") as batch_op:
        batch_op.add_column(sa.Column("network_name", sa.Text, nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("networks") as batch_op:
        batch_op.drop_column("network_name")

    with op.batch_alter_table("volumes") as batch_op:
        batch_op.drop_column("volume_name")

    with op.batch_alter_table("pods") as batch_op:
        batch_op.drop_column("pod_name_override")

    with op.batch_alter_table("containers") as batch_op:
        batch_op.drop_column("container_name")

    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.drop_column("tls_verify")
        batch_op.drop_column("retry_delay")
        batch_op.drop_column("retry")
        batch_op.drop_column("quiet")
        batch_op.drop_column("podman_args")
        batch_op.drop_column("global_args")
        batch_op.drop_column("decryption_key")
        batch_op.drop_column("creds")
        batch_op.drop_column("containers_conf_module")
        batch_op.drop_column("cert_dir")
        batch_op.drop_column("auth_file")

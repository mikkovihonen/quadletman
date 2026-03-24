"""extract_networks

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-24

Moves network configuration from compartment-level columns into a dedicated
networks table so that a compartment can define multiple named networks.

Data migration:
- For every compartment that has at least one container using a shared network
  (i.e. container.network == compartment.id), a row is created in the new
  networks table with name = compartment.id and all net_* values copied over.
- Container rows are not modified because the migrated network name matches
  the value already stored in container.network.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NET_TEXT_COLS = [
    "net_driver",
    "net_subnet",
    "net_gateway",
    "net_ip_range",
    "net_options",
    "net_containers_conf_module",
    "net_ipam_driver",
    "net_dns",
    "net_service_name",
    "net_interface_name",
]

_NET_BOOL_COLS = [
    "net_ipv6",
    "net_internal",
    "net_dns_enabled",
    "net_disable_dns",
    "net_delete_on_stop",
]

_NET_JSON_COLS = [
    ("net_label", "{}"),
    ("net_global_args", "[]"),
    ("net_podman_args", "[]"),
]


def upgrade() -> None:
    # 1. Create the networks table
    op.create_table(
        "networks",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("compartment_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        # Text columns
        sa.Column("net_driver", sa.Text(), nullable=False, server_default=""),
        sa.Column("net_subnet", sa.Text(), nullable=False, server_default=""),
        sa.Column("net_gateway", sa.Text(), nullable=False, server_default=""),
        sa.Column("net_ip_range", sa.Text(), nullable=False, server_default=""),
        sa.Column("net_options", sa.Text(), nullable=False, server_default=""),
        sa.Column("net_containers_conf_module", sa.Text(), nullable=False, server_default=""),
        sa.Column("net_ipam_driver", sa.Text(), nullable=False, server_default=""),
        sa.Column("net_dns", sa.Text(), nullable=False, server_default=""),
        sa.Column("net_service_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("net_interface_name", sa.Text(), nullable=False, server_default=""),
        # Boolean columns
        sa.Column("net_ipv6", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("net_internal", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("net_dns_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("net_disable_dns", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("net_delete_on_stop", sa.Boolean(), nullable=False, server_default="0"),
        # JSON columns (stored as Text)
        sa.Column("net_label", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("net_global_args", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("net_podman_args", sa.Text(), nullable=False, server_default="[]"),
        # Timestamp
        sa.Column(
            "created_at",
            sa.Text(),
            nullable=False,
            server_default=sa.func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
        ),
        sa.ForeignKeyConstraint(["compartment_id"], ["compartments.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("compartment_id", "name"),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2. Migrate existing network data from compartments to networks table.
    #    Create a network row for every compartment that has at least one
    #    container using the shared network (container.network == compartment.id).
    conn = op.get_bind()
    compartments_with_network = conn.execute(
        sa.text(
            "SELECT DISTINCT c.compartment_id "
            "FROM containers c "
            "JOIN compartments comp ON comp.id = c.compartment_id "
            "WHERE c.network = c.compartment_id"
        )
    ).fetchall()

    for (compartment_id,) in compartments_with_network:
        row = (
            conn.execute(
                sa.text("SELECT * FROM compartments WHERE id = :cid"),
                {"cid": compartment_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            continue

        # Generate a UUID for the new network row
        import uuid

        net_id = str(uuid.uuid4())

        col_names = ["id", "compartment_id", "name"]
        col_values = {"id": net_id, "compartment_id": compartment_id, "name": compartment_id}
        for col in _NET_TEXT_COLS:
            col_names.append(col)
            col_values[col] = row[col]
        for col in _NET_BOOL_COLS:
            col_names.append(col)
            col_values[col] = row[col]
        for col, _default in _NET_JSON_COLS:
            col_names.append(col)
            col_values[col] = row[col]

        placeholders = ", ".join(f":{c}" for c in col_names)
        columns = ", ".join(col_names)
        conn.execute(
            sa.text(f"INSERT INTO networks ({columns}) VALUES ({placeholders})"),  # noqa: S608
            col_values,
        )

    # 3. Drop net_* columns from compartments (SQLite requires batch mode)
    all_net_cols = _NET_TEXT_COLS + _NET_BOOL_COLS + [col for col, _ in _NET_JSON_COLS]
    with op.batch_alter_table("compartments") as batch_op:
        for col in all_net_cols:
            batch_op.drop_column(col)


def downgrade() -> None:
    # 1. Re-add net_* columns to compartments
    with op.batch_alter_table("compartments") as batch_op:
        for col in _NET_TEXT_COLS:
            batch_op.add_column(sa.Column(col, sa.Text(), nullable=False, server_default=""))
        for col in _NET_BOOL_COLS:
            batch_op.add_column(sa.Column(col, sa.Boolean(), nullable=False, server_default="0"))
        for col, default in _NET_JSON_COLS:
            batch_op.add_column(sa.Column(col, sa.Text(), nullable=False, server_default=default))

    # 2. Copy network data back to compartments
    conn = op.get_bind()
    networks = conn.execute(sa.text("SELECT * FROM networks")).mappings().fetchall()
    for net in networks:
        set_clauses = []
        values = {"cid": net["compartment_id"]}
        all_cols = _NET_TEXT_COLS + _NET_BOOL_COLS + [c for c, _ in _NET_JSON_COLS]
        for col in all_cols:
            set_clauses.append(f"{col} = :{col}")
            values[col] = net[col]
        set_sql = ", ".join(set_clauses)
        conn.execute(
            sa.text(f"UPDATE compartments SET {set_sql} WHERE id = :cid"),  # noqa: S608
            values,
        )

    # 3. Drop the networks table
    op.drop_table("networks")

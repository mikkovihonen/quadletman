"""extract_build_units

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-22

Extracts build-related fields from the containers table into a new build_units
table.  Containers that previously had inline Containerfile builds get a
corresponding build_units row, and the container's new build_unit_name column
references it.  The 28 build_* columns are dropped from containers.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# All build_* columns that will be dropped from containers after migration.
_BUILD_COLUMNS = [
    "build_context",
    "build_file",
    "containerfile_content",
    "build_annotation",
    "build_arch",
    "build_auth_file",
    "build_containers_conf_module",
    "build_dns",
    "build_dns_option",
    "build_dns_search",
    "build_env",
    "build_force_rm",
    "build_global_args",
    "build_group_add",
    "build_label",
    "build_network",
    "build_podman_args",
    "build_pull",
    "build_secret",
    "build_service_name",
    "build_target",
    "build_tls_verify",
    "build_variant",
    "build_volume",
    "build_retry",
    "build_retry_delay",
    "build_args",
    "build_ignore_file",
]


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def upgrade() -> None:
    # 1. Create build_units table
    op.create_table(
        "build_units",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("image_tag", sa.Text, nullable=False),
        sa.Column("containerfile_content", sa.Text, nullable=False, server_default=""),
        sa.Column("build_context", sa.Text, nullable=False, server_default=""),
        sa.Column("build_file", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
        ),
        sa.Column(
            "updated_at",
            sa.Text,
            nullable=False,
            server_default=sa.func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
        ),
        # Podman 5.2.0 fields
        sa.Column("annotation", sa.Text, nullable=False, server_default="[]"),
        sa.Column("arch", sa.Text, nullable=False, server_default=""),
        sa.Column("auth_file", sa.Text, nullable=False, server_default=""),
        sa.Column("containers_conf_module", sa.Text, nullable=False, server_default=""),
        sa.Column("dns", sa.Text, nullable=False, server_default="[]"),
        sa.Column("dns_option", sa.Text, nullable=False, server_default="[]"),
        sa.Column("dns_search", sa.Text, nullable=False, server_default="[]"),
        sa.Column("env", sa.Text, nullable=False, server_default="{}"),
        sa.Column("force_rm", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("global_args", sa.Text, nullable=False, server_default="[]"),
        sa.Column("group_add", sa.Text, nullable=False, server_default="[]"),
        sa.Column("label", sa.Text, nullable=False, server_default="{}"),
        sa.Column("network", sa.Text, nullable=False, server_default=""),
        sa.Column("podman_args", sa.Text, nullable=False, server_default="[]"),
        sa.Column("pull", sa.Text, nullable=False, server_default=""),
        sa.Column("secret", sa.Text, nullable=False, server_default="[]"),
        sa.Column("target", sa.Text, nullable=False, server_default=""),
        sa.Column("tls_verify", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("variant", sa.Text, nullable=False, server_default=""),
        sa.Column("volume", sa.Text, nullable=False, server_default="[]"),
        # Podman 5.3.0
        sa.Column("service_name", sa.Text, nullable=False, server_default=""),
        # Podman 5.5.0
        sa.Column("retry", sa.Text, nullable=False, server_default=""),
        sa.Column("retry_delay", sa.Text, nullable=False, server_default=""),
        # Podman 5.7.0
        sa.Column("build_args", sa.Text, nullable=False, server_default="{}"),
        sa.Column("ignore_file", sa.Text, nullable=False, server_default=""),
        sa.UniqueConstraint("compartment_id", "name"),
    )

    # 2. Add build_unit_name column to containers
    with op.batch_alter_table("containers") as batch_op:
        batch_op.add_column(
            sa.Column("build_unit_name", sa.Text, nullable=False, server_default="")
        )

    # 3. Migrate existing build containers → build_units rows
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, compartment_id, name, image, "
            "containerfile_content, build_context, build_file, "
            "build_annotation, build_arch, build_auth_file, "
            "build_containers_conf_module, build_dns, build_dns_option, "
            "build_dns_search, build_env, build_force_rm, "
            "build_global_args, build_group_add, build_label, "
            "build_network, build_podman_args, build_pull, "
            "build_secret, build_service_name, build_target, "
            "build_tls_verify, build_variant, build_volume, "
            "build_retry, build_retry_delay, build_args, build_ignore_file "
            "FROM containers "
            "WHERE containerfile_content != '' OR build_context != ''"
        )
    ).fetchall()

    now = _utcnow()
    for row in rows:
        build_name = f"{row[2]}-build"  # container.name + "-build"
        build_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO build_units "
                "(id, compartment_id, name, image_tag, "
                "containerfile_content, build_context, build_file, "
                "created_at, updated_at, "
                "annotation, arch, auth_file, containers_conf_module, "
                "dns, dns_option, dns_search, env, force_rm, "
                "global_args, group_add, label, network, podman_args, "
                "pull, secret, target, tls_verify, variant, volume, "
                "service_name, retry, retry_delay, build_args, ignore_file) "
                "VALUES "
                "(:id, :cid, :name, :image_tag, "
                ":containerfile_content, :build_context, :build_file, "
                ":created_at, :updated_at, "
                ":annotation, :arch, :auth_file, :containers_conf_module, "
                ":dns, :dns_option, :dns_search, :env, :force_rm, "
                ":global_args, :group_add, :label, :network, :podman_args, "
                ":pull, :secret, :target, :tls_verify, :variant, :volume, "
                ":service_name, :retry, :retry_delay, :build_args, :ignore_file)"
            ),
            {
                "id": build_id,
                "cid": row[1],  # compartment_id
                "name": build_name,
                "image_tag": row[3],  # container.image
                "containerfile_content": row[4],
                "build_context": row[5],
                "build_file": row[6],
                "created_at": now,
                "updated_at": now,
                "annotation": row[7],
                "arch": row[8],
                "auth_file": row[9],
                "containers_conf_module": row[10],
                "dns": row[11],
                "dns_option": row[12],
                "dns_search": row[13],
                "env": row[14],
                "force_rm": row[15],
                "global_args": row[16],
                "group_add": row[17],
                "label": row[18],
                "network": row[19],
                "podman_args": row[20],
                "pull": row[21],
                "secret": row[22],
                "service_name": row[23],
                "target": row[24],
                "tls_verify": row[25],
                "variant": row[26],
                "volume": row[27],
                "retry": row[28],
                "retry_delay": row[29],
                "build_args": row[30],
                "ignore_file": row[31],
            },
        )
        # Update the container to reference the new build unit
        conn.execute(
            sa.text("UPDATE containers SET build_unit_name = :bname WHERE id = :cid"),
            {"bname": build_name, "cid": row[0]},
        )

    # 4. Drop old build columns from containers
    with op.batch_alter_table("containers") as batch_op:
        for col in _BUILD_COLUMNS:
            batch_op.drop_column(col)


def downgrade() -> None:
    # Re-add build columns to containers
    with op.batch_alter_table("containers") as batch_op:
        batch_op.add_column(sa.Column("build_context", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("build_file", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("containerfile_content", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("build_annotation", sa.Text, nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("build_arch", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("build_auth_file", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("build_containers_conf_module", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("build_dns", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(
            sa.Column("build_dns_option", sa.Text, nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("build_dns_search", sa.Text, nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("build_env", sa.Text, nullable=False, server_default="{}"))
        batch_op.add_column(
            sa.Column("build_force_rm", sa.Boolean, nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("build_global_args", sa.Text, nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("build_group_add", sa.Text, nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("build_label", sa.Text, nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("build_network", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("build_podman_args", sa.Text, nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("build_pull", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("build_secret", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(
            sa.Column("build_service_name", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("build_target", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("build_tls_verify", sa.Boolean, nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("build_variant", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("build_volume", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("build_retry", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("build_retry_delay", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("build_args", sa.Text, nullable=False, server_default="{}"))
        batch_op.add_column(
            sa.Column("build_ignore_file", sa.Text, nullable=False, server_default="")
        )

    # Migrate build_units data back to containers
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT bu.build_context, bu.build_file, bu.containerfile_content, "
            "bu.annotation, bu.arch, bu.auth_file, bu.containers_conf_module, "
            "bu.dns, bu.dns_option, bu.dns_search, bu.env, bu.force_rm, "
            "bu.global_args, bu.group_add, bu.label, bu.network, bu.podman_args, "
            "bu.pull, bu.secret, bu.service_name, bu.target, bu.tls_verify, "
            "bu.variant, bu.volume, bu.retry, bu.retry_delay, bu.build_args, "
            "bu.ignore_file, c.id AS container_id "
            "FROM build_units bu "
            "JOIN containers c ON c.compartment_id = bu.compartment_id "
            "  AND c.build_unit_name = bu.name"
        )
    ).fetchall()
    for row in rows:
        conn.execute(
            sa.text(
                "UPDATE containers SET "
                "build_context=:bc, build_file=:bf, containerfile_content=:cc, "
                "build_annotation=:ba, build_arch=:bar, build_auth_file=:baf, "
                "build_containers_conf_module=:bcm, build_dns=:bd, "
                "build_dns_option=:bdo, build_dns_search=:bds, build_env=:be, "
                "build_force_rm=:bfr, build_global_args=:bga, build_group_add=:bgra, "
                "build_label=:bl, build_network=:bn, build_podman_args=:bpa, "
                "build_pull=:bp, build_secret=:bs, build_service_name=:bsn, "
                "build_target=:bt, build_tls_verify=:btv, build_variant=:bv, "
                "build_volume=:bvol, build_retry=:br, build_retry_delay=:brd, "
                "build_args=:bargs, build_ignore_file=:bif "
                "WHERE id=:cid"
            ),
            {
                "bc": row[0],
                "bf": row[1],
                "cc": row[2],
                "ba": row[3],
                "bar": row[4],
                "baf": row[5],
                "bcm": row[6],
                "bd": row[7],
                "bdo": row[8],
                "bds": row[9],
                "be": row[10],
                "bfr": row[11],
                "bga": row[12],
                "bgra": row[13],
                "bl": row[14],
                "bn": row[15],
                "bpa": row[16],
                "bp": row[17],
                "bs": row[18],
                "bsn": row[19],
                "bt": row[20],
                "btv": row[21],
                "bv": row[22],
                "bvol": row[23],
                "br": row[24],
                "brd": row[25],
                "bargs": row[26],
                "bif": row[27],
                "cid": row[28],
            },
        )

    # Drop build_unit_name from containers
    with op.batch_alter_table("containers") as batch_op:
        batch_op.drop_column("build_unit_name")

    op.drop_table("build_units")

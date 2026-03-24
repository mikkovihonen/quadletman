"""namespace_fields

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-24

Renames model fields to establish the qm_ namespace convention:
- qm_ prefix for quadletman-invented fields (identity, UI, host mgmt)
- No prefix for upstream fields (Podman Quadlet keys, systemd keys)
- Removes vol_ prefix from Volume Podman fields
- Removes net_ prefix from Network Podman fields
- Renames Container pod_name → pod (matches Quadlet Pod= key)
- Renames Network delete_on_stop → network_delete_on_stop (matches key)
- Drops Container privileged column (not a Quadlet concept)
- Drops ImageUnit pull_policy column (replaced by policy)
- Drops Artifact digest column (not an upstream key)
- Renames image_units → images, build_units → builds tables
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- containers: 3 qm_ + pod_name→pod + drop privileged ---
    with op.batch_alter_table("containers") as batch_op:
        batch_op.alter_column("name", new_column_name="qm_name")
        batch_op.alter_column("sort_order", new_column_name="qm_sort_order")
        batch_op.alter_column("build_unit_name", new_column_name="qm_build_unit_name")
        batch_op.alter_column("pod_name", new_column_name="pod")
        batch_op.drop_column("privileged")

    # --- volumes: 4 qm_ + 14 vol_ removals ---
    with op.batch_alter_table("volumes") as batch_op:
        batch_op.alter_column("name", new_column_name="qm_name")
        batch_op.alter_column("selinux_context", new_column_name="qm_selinux_context")
        batch_op.alter_column("owner_uid", new_column_name="qm_owner_uid")
        batch_op.alter_column("use_quadlet", new_column_name="qm_use_quadlet")
        batch_op.alter_column("vol_driver", new_column_name="driver")
        batch_op.alter_column("vol_device", new_column_name="device")
        batch_op.alter_column("vol_options", new_column_name="options")
        batch_op.alter_column("vol_copy", new_column_name="copy")
        batch_op.alter_column("vol_group", new_column_name="group")
        batch_op.alter_column("vol_gid", new_column_name="gid")
        batch_op.alter_column("vol_uid", new_column_name="uid")
        batch_op.alter_column("vol_user", new_column_name="user")
        batch_op.alter_column("vol_image", new_column_name="image")
        batch_op.alter_column("vol_label", new_column_name="label")
        batch_op.alter_column("vol_type", new_column_name="type")
        batch_op.alter_column(
            "vol_containers_conf_module", new_column_name="containers_conf_module"
        )
        batch_op.alter_column("vol_global_args", new_column_name="global_args")
        batch_op.alter_column("vol_podman_args", new_column_name="podman_args")

    # --- networks: 1 qm_ + 18 net_ removals ---
    with op.batch_alter_table("networks") as batch_op:
        batch_op.alter_column("name", new_column_name="qm_name")
        batch_op.alter_column("net_driver", new_column_name="driver")
        batch_op.alter_column("net_subnet", new_column_name="subnet")
        batch_op.alter_column("net_gateway", new_column_name="gateway")
        batch_op.alter_column("net_ipv6", new_column_name="ipv6")
        batch_op.alter_column("net_internal", new_column_name="internal")
        batch_op.alter_column("net_dns_enabled", new_column_name="dns_enabled")
        batch_op.alter_column("net_disable_dns", new_column_name="disable_dns")
        batch_op.alter_column("net_ip_range", new_column_name="ip_range")
        batch_op.alter_column("net_label", new_column_name="label")
        batch_op.alter_column("net_options", new_column_name="options")
        batch_op.alter_column(
            "net_containers_conf_module", new_column_name="containers_conf_module"
        )
        batch_op.alter_column("net_global_args", new_column_name="global_args")
        batch_op.alter_column("net_podman_args", new_column_name="podman_args")
        batch_op.alter_column("net_ipam_driver", new_column_name="ipam_driver")
        batch_op.alter_column("net_dns", new_column_name="dns")
        batch_op.alter_column("net_service_name", new_column_name="service_name")
        batch_op.alter_column("net_delete_on_stop", new_column_name="network_delete_on_stop")
        batch_op.alter_column("net_interface_name", new_column_name="interface_name")

    # --- pods ---
    with op.batch_alter_table("pods") as batch_op:
        batch_op.alter_column("name", new_column_name="qm_name")

    # --- image_units → images: rename table + qm_name + drop pull_policy ---
    op.rename_table("image_units", "images")
    with op.batch_alter_table("images") as batch_op:
        batch_op.alter_column("name", new_column_name="qm_name")
        batch_op.drop_column("pull_policy")

    # --- build_units → builds: rename table ---
    op.rename_table("build_units", "builds")
    with op.batch_alter_table("builds") as batch_op:
        batch_op.alter_column("name", new_column_name="qm_name")
        batch_op.alter_column("containerfile_content", new_column_name="qm_containerfile_content")

    # --- kubes ---
    with op.batch_alter_table("kubes") as batch_op:
        batch_op.alter_column("name", new_column_name="qm_name")
        batch_op.alter_column("yaml_content", new_column_name="qm_yaml_content")
        batch_op.add_column(sa.Column("yaml", sa.Text, nullable=False, server_default=""))

    # --- artifacts: qm_name + image→artifact + drop digest ---
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.alter_column("name", new_column_name="qm_name")
        batch_op.alter_column("image", new_column_name="artifact")
        batch_op.drop_column("digest")

    # --- timers ---
    with op.batch_alter_table("timers") as batch_op:
        batch_op.alter_column("name", new_column_name="qm_name")
        batch_op.alter_column("container_id", new_column_name="qm_container_id")
        batch_op.alter_column("enabled", new_column_name="qm_enabled")

    # --- notification_hooks ---
    with op.batch_alter_table("notification_hooks") as batch_op:
        batch_op.alter_column("container_name", new_column_name="qm_container_name")


def downgrade() -> None:
    with op.batch_alter_table("notification_hooks") as batch_op:
        batch_op.alter_column("qm_container_name", new_column_name="container_name")

    with op.batch_alter_table("timers") as batch_op:
        batch_op.alter_column("qm_enabled", new_column_name="enabled")
        batch_op.alter_column("qm_container_id", new_column_name="container_id")
        batch_op.alter_column("qm_name", new_column_name="name")

    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.alter_column("qm_name", new_column_name="name")
        batch_op.alter_column("artifact", new_column_name="image")
        batch_op.add_column(sa.Column("digest", sa.Text, nullable=False, server_default=""))

    with op.batch_alter_table("kubes") as batch_op:
        batch_op.alter_column("qm_yaml_content", new_column_name="yaml_content")
        batch_op.alter_column("qm_name", new_column_name="name")
        batch_op.drop_column("yaml")

    with op.batch_alter_table("builds") as batch_op:
        batch_op.alter_column("qm_containerfile_content", new_column_name="containerfile_content")
        batch_op.alter_column("qm_name", new_column_name="name")
    op.rename_table("builds", "build_units")

    with op.batch_alter_table("images") as batch_op:
        batch_op.alter_column("qm_name", new_column_name="name")
        batch_op.add_column(sa.Column("pull_policy", sa.Text, nullable=False, server_default=""))
    op.rename_table("images", "image_units")

    with op.batch_alter_table("pods") as batch_op:
        batch_op.alter_column("qm_name", new_column_name="name")

    with op.batch_alter_table("networks") as batch_op:
        batch_op.alter_column("interface_name", new_column_name="net_interface_name")
        batch_op.alter_column("network_delete_on_stop", new_column_name="net_delete_on_stop")
        batch_op.alter_column("service_name", new_column_name="net_service_name")
        batch_op.alter_column("dns", new_column_name="net_dns")
        batch_op.alter_column("ipam_driver", new_column_name="net_ipam_driver")
        batch_op.alter_column("podman_args", new_column_name="net_podman_args")
        batch_op.alter_column("global_args", new_column_name="net_global_args")
        batch_op.alter_column(
            "containers_conf_module", new_column_name="net_containers_conf_module"
        )
        batch_op.alter_column("options", new_column_name="net_options")
        batch_op.alter_column("label", new_column_name="net_label")
        batch_op.alter_column("ip_range", new_column_name="net_ip_range")
        batch_op.alter_column("disable_dns", new_column_name="net_disable_dns")
        batch_op.alter_column("dns_enabled", new_column_name="net_dns_enabled")
        batch_op.alter_column("internal", new_column_name="net_internal")
        batch_op.alter_column("ipv6", new_column_name="net_ipv6")
        batch_op.alter_column("gateway", new_column_name="net_gateway")
        batch_op.alter_column("subnet", new_column_name="net_subnet")
        batch_op.alter_column("driver", new_column_name="net_driver")
        batch_op.alter_column("qm_name", new_column_name="name")

    with op.batch_alter_table("volumes") as batch_op:
        batch_op.alter_column("podman_args", new_column_name="vol_podman_args")
        batch_op.alter_column("global_args", new_column_name="vol_global_args")
        batch_op.alter_column(
            "containers_conf_module", new_column_name="vol_containers_conf_module"
        )
        batch_op.alter_column("type", new_column_name="vol_type")
        batch_op.alter_column("label", new_column_name="vol_label")
        batch_op.alter_column("image", new_column_name="vol_image")
        batch_op.alter_column("user", new_column_name="vol_user")
        batch_op.alter_column("uid", new_column_name="vol_uid")
        batch_op.alter_column("gid", new_column_name="vol_gid")
        batch_op.alter_column("group", new_column_name="vol_group")
        batch_op.alter_column("copy", new_column_name="vol_copy")
        batch_op.alter_column("options", new_column_name="vol_options")
        batch_op.alter_column("device", new_column_name="vol_device")
        batch_op.alter_column("driver", new_column_name="vol_driver")
        batch_op.alter_column("qm_use_quadlet", new_column_name="use_quadlet")
        batch_op.alter_column("qm_owner_uid", new_column_name="owner_uid")
        batch_op.alter_column("qm_selinux_context", new_column_name="selinux_context")
        batch_op.alter_column("qm_name", new_column_name="name")

    with op.batch_alter_table("containers") as batch_op:
        batch_op.alter_column("pod", new_column_name="pod_name")
        batch_op.alter_column("qm_build_unit_name", new_column_name="build_unit_name")
        batch_op.alter_column("qm_sort_order", new_column_name="sort_order")
        batch_op.alter_column("qm_name", new_column_name="name")
        batch_op.add_column(sa.Column("privileged", sa.Boolean, nullable=False, server_default="0"))

"""add_extended_quadlet_fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-21

Adds columns for Podman 4.4–5.7 Quadlet keys to containers, pods, image_units,
volumes, and compartments tables.  Creates new kubes and artifacts tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ── timestamp helper reused for created_at server defaults ──
_TS_DEFAULT = sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # New tables
    # ------------------------------------------------------------------
    op.create_table(
        "kubes",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("yaml_content", sa.Text, nullable=False),
        sa.Column("config_map", sa.Text, nullable=False, server_default="[]"),
        sa.Column("network", sa.Text, nullable=False, server_default=""),
        sa.Column("publish_ports", sa.Text, nullable=False, server_default="[]"),
        sa.Column("log_driver", sa.Text, nullable=False, server_default=""),
        sa.Column("user_ns", sa.Text, nullable=False, server_default=""),
        sa.Column("auto_update", sa.Text, nullable=False, server_default=""),
        sa.Column("exit_code_propagation", sa.Text, nullable=False, server_default=""),
        sa.Column("containers_conf_module", sa.Text, nullable=False, server_default=""),
        sa.Column("global_args", sa.Text, nullable=False, server_default="[]"),
        sa.Column("podman_args", sa.Text, nullable=False, server_default="[]"),
        sa.Column("kube_down_force", sa.Integer, nullable=False, server_default="0"),
        sa.Column("set_working_directory", sa.Text, nullable=False, server_default=""),
        sa.Column("service_name", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.Text, nullable=False, server_default=_TS_DEFAULT),
    )
    op.create_index("ix_kubes_compartment_id", "kubes", ["compartment_id"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("image", sa.Text, nullable=False),
        sa.Column("digest", sa.Text, nullable=False, server_default=""),
        sa.Column("service_name", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.Text, nullable=False, server_default=_TS_DEFAULT),
    )
    op.create_index("ix_artifacts_compartment_id", "artifacts", ["compartment_id"])

    # ------------------------------------------------------------------
    # compartments — network fields from CompartmentNetworkUpdate
    # ------------------------------------------------------------------
    with op.batch_alter_table("compartments") as batch_op:
        batch_op.add_column(
            sa.Column("net_disable_dns", sa.Integer, nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("net_ip_range", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("net_options", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("net_label", sa.Text, nullable=False, server_default="{}"))
        batch_op.add_column(
            sa.Column("net_containers_conf_module", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("net_global_args", sa.Text, nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("net_podman_args", sa.Text, nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("net_ipam_driver", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("net_dns", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("net_service_name", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("net_delete_on_stop", sa.Integer, nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("net_interface_name", sa.Text, nullable=False, server_default="")
        )

    # ------------------------------------------------------------------
    # containers — Podman 4.4–5.7 extended fields
    # ------------------------------------------------------------------
    with op.batch_alter_table("containers") as batch_op:
        # Podman 4.4.0 (base Quadlet keys)
        batch_op.add_column(sa.Column("annotation", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(
            sa.Column("expose_host_port", sa.Text, nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("group", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("security_label_disable", sa.Integer, nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("security_label_file_type", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("security_label_level", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("security_label_type", sa.Text, nullable=False, server_default="")
        )
        # Podman 4.5.0
        batch_op.add_column(sa.Column("tmpfs", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("ip", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("ip6", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("mount", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("rootfs", sa.Text, nullable=False, server_default=""))
        # Podman 4.6.0
        batch_op.add_column(sa.Column("pull", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("security_label_nested", sa.Integer, nullable=False, server_default="0")
        )
        # Podman 4.7.0
        batch_op.add_column(sa.Column("pids_limit", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("ulimits", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("shm_size", sa.Text, nullable=False, server_default=""))
        # Podman 4.8.0
        batch_op.add_column(
            sa.Column("read_only_tmpfs", sa.Integer, nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("sub_uid_map", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("sub_gid_map", sa.Text, nullable=False, server_default=""))
        # Podman 5.0.0
        batch_op.add_column(
            sa.Column("containers_conf_module", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("global_args", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("stop_timeout", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("run_init", sa.Integer, nullable=False, server_default="0"))
        # Podman 5.1.0
        batch_op.add_column(sa.Column("group_add", sa.Text, nullable=False, server_default="[]"))
        # Podman 5.2.0
        batch_op.add_column(sa.Column("stop_signal", sa.Text, nullable=False, server_default=""))
        # Podman 5.3.0
        batch_op.add_column(sa.Column("service_name", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("default_dependencies", sa.Integer, nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("add_host", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("cgroups_mode", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("start_with_pod", sa.Integer, nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("timezone", sa.Text, nullable=False, server_default=""))
        # Podman 5.5.0
        batch_op.add_column(
            sa.Column("environment_host", sa.Integer, nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("memory", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("reload_cmd", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("reload_signal", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("retry", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("retry_delay", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("health_log_destination", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("health_max_log_count", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("health_max_log_size", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("health_startup_cmd", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("health_startup_interval", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("health_startup_retries", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("health_startup_success", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("health_startup_timeout", sa.Text, nullable=False, server_default="")
        )
        # Podman 5.7.0
        batch_op.add_column(sa.Column("http_proxy", sa.Integer, nullable=False, server_default="0"))
        # Build fields (Podman 5.2.0+)
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
            sa.Column("build_force_rm", sa.Integer, nullable=False, server_default="0")
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
            sa.Column("build_tls_verify", sa.Integer, nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("build_variant", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("build_volume", sa.Text, nullable=False, server_default="[]"))
        # Podman 5.5.0 build
        batch_op.add_column(sa.Column("build_retry", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("build_retry_delay", sa.Text, nullable=False, server_default="")
        )
        # Podman 5.7.0 build
        batch_op.add_column(sa.Column("build_args", sa.Text, nullable=False, server_default="{}"))
        batch_op.add_column(
            sa.Column("build_ignore_file", sa.Text, nullable=False, server_default="")
        )

    # ------------------------------------------------------------------
    # pods — Podman 5.0–5.7 extended fields
    # ------------------------------------------------------------------
    with op.batch_alter_table("pods") as batch_op:
        # Podman 5.0.0
        batch_op.add_column(
            sa.Column("containers_conf_module", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("global_args", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("podman_args", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("volumes", sa.Text, nullable=False, server_default="[]"))
        # Podman 5.3.0
        batch_op.add_column(sa.Column("service_name", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("dns", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("dns_search", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("dns_option", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("ip", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("ip6", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("user_ns", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("add_host", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("uid_map", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("gid_map", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("sub_uid_map", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("sub_gid_map", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("network_aliases", sa.Text, nullable=False, server_default="[]")
        )
        # Podman 5.4.0
        batch_op.add_column(sa.Column("shm_size", sa.Text, nullable=False, server_default=""))
        # Podman 5.5.0
        batch_op.add_column(sa.Column("hostname", sa.Text, nullable=False, server_default=""))
        # Podman 5.6.0
        batch_op.add_column(sa.Column("labels", sa.Text, nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("exit_policy", sa.Text, nullable=False, server_default=""))
        # Podman 5.7.0
        batch_op.add_column(sa.Column("stop_timeout", sa.Text, nullable=False, server_default=""))

    # ------------------------------------------------------------------
    # image_units — Podman 4.8–5.6 extended fields
    # ------------------------------------------------------------------
    with op.batch_alter_table("image_units") as batch_op:
        # Podman 4.8.0
        batch_op.add_column(sa.Column("all_tags", sa.Integer, nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("arch", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("cert_dir", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("creds", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("decryption_key", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("os", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("tls_verify", sa.Integer, nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("variant", sa.Text, nullable=False, server_default=""))
        # Podman 5.0.0
        batch_op.add_column(
            sa.Column("containers_conf_module", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("global_args", sa.Text, nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("podman_args", sa.Text, nullable=False, server_default="[]"))
        # Podman 5.3.0
        batch_op.add_column(sa.Column("service_name", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("image_tags", sa.Text, nullable=False, server_default="[]"))
        # Podman 5.5.0
        batch_op.add_column(sa.Column("retry", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("retry_delay", sa.Text, nullable=False, server_default=""))
        # Podman 5.6.0
        batch_op.add_column(sa.Column("policy", sa.Text, nullable=False, server_default=""))

    # ------------------------------------------------------------------
    # volumes — Podman 4.4–5.3 extended fields
    # ------------------------------------------------------------------
    with op.batch_alter_table("volumes") as batch_op:
        # Podman 4.4.0
        batch_op.add_column(sa.Column("vol_gid", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("vol_uid", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("vol_user", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("vol_image", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("vol_type", sa.Text, nullable=False, server_default=""))
        batch_op.add_column(sa.Column("vol_label", sa.Text, nullable=False, server_default="{}"))
        # Podman 5.0.0
        batch_op.add_column(
            sa.Column("vol_containers_conf_module", sa.Text, nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("vol_global_args", sa.Text, nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("vol_podman_args", sa.Text, nullable=False, server_default="[]")
        )
        # Podman 5.3.0
        batch_op.add_column(sa.Column("service_name", sa.Text, nullable=False, server_default=""))


def downgrade() -> None:
    # ------------------------------------------------------------------
    # volumes — drop added columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("volumes") as batch_op:
        batch_op.drop_column("service_name")
        batch_op.drop_column("vol_podman_args")
        batch_op.drop_column("vol_global_args")
        batch_op.drop_column("vol_containers_conf_module")
        batch_op.drop_column("vol_label")
        batch_op.drop_column("vol_type")
        batch_op.drop_column("vol_image")
        batch_op.drop_column("vol_user")
        batch_op.drop_column("vol_uid")
        batch_op.drop_column("vol_gid")

    # ------------------------------------------------------------------
    # image_units — drop added columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("image_units") as batch_op:
        batch_op.drop_column("policy")
        batch_op.drop_column("retry_delay")
        batch_op.drop_column("retry")
        batch_op.drop_column("image_tags")
        batch_op.drop_column("service_name")
        batch_op.drop_column("podman_args")
        batch_op.drop_column("global_args")
        batch_op.drop_column("containers_conf_module")
        batch_op.drop_column("variant")
        batch_op.drop_column("tls_verify")
        batch_op.drop_column("os")
        batch_op.drop_column("decryption_key")
        batch_op.drop_column("creds")
        batch_op.drop_column("cert_dir")
        batch_op.drop_column("arch")
        batch_op.drop_column("all_tags")

    # ------------------------------------------------------------------
    # pods — drop added columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("pods") as batch_op:
        batch_op.drop_column("stop_timeout")
        batch_op.drop_column("exit_policy")
        batch_op.drop_column("labels")
        batch_op.drop_column("hostname")
        batch_op.drop_column("shm_size")
        batch_op.drop_column("network_aliases")
        batch_op.drop_column("sub_gid_map")
        batch_op.drop_column("sub_uid_map")
        batch_op.drop_column("gid_map")
        batch_op.drop_column("uid_map")
        batch_op.drop_column("add_host")
        batch_op.drop_column("user_ns")
        batch_op.drop_column("ip6")
        batch_op.drop_column("ip")
        batch_op.drop_column("dns_option")
        batch_op.drop_column("dns_search")
        batch_op.drop_column("dns")
        batch_op.drop_column("service_name")
        batch_op.drop_column("volumes")
        batch_op.drop_column("podman_args")
        batch_op.drop_column("global_args")
        batch_op.drop_column("containers_conf_module")

    # ------------------------------------------------------------------
    # containers — drop added columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("containers") as batch_op:
        batch_op.drop_column("build_ignore_file")
        batch_op.drop_column("build_args")
        batch_op.drop_column("build_retry_delay")
        batch_op.drop_column("build_retry")
        batch_op.drop_column("build_volume")
        batch_op.drop_column("build_variant")
        batch_op.drop_column("build_tls_verify")
        batch_op.drop_column("build_target")
        batch_op.drop_column("build_service_name")
        batch_op.drop_column("build_secret")
        batch_op.drop_column("build_pull")
        batch_op.drop_column("build_podman_args")
        batch_op.drop_column("build_network")
        batch_op.drop_column("build_label")
        batch_op.drop_column("build_group_add")
        batch_op.drop_column("build_global_args")
        batch_op.drop_column("build_force_rm")
        batch_op.drop_column("build_env")
        batch_op.drop_column("build_dns_search")
        batch_op.drop_column("build_dns_option")
        batch_op.drop_column("build_dns")
        batch_op.drop_column("build_containers_conf_module")
        batch_op.drop_column("build_auth_file")
        batch_op.drop_column("build_arch")
        batch_op.drop_column("build_annotation")
        batch_op.drop_column("http_proxy")
        batch_op.drop_column("health_startup_timeout")
        batch_op.drop_column("health_startup_success")
        batch_op.drop_column("health_startup_retries")
        batch_op.drop_column("health_startup_interval")
        batch_op.drop_column("health_startup_cmd")
        batch_op.drop_column("health_max_log_size")
        batch_op.drop_column("health_max_log_count")
        batch_op.drop_column("health_log_destination")
        batch_op.drop_column("retry_delay")
        batch_op.drop_column("retry")
        batch_op.drop_column("reload_signal")
        batch_op.drop_column("reload_cmd")
        batch_op.drop_column("memory")
        batch_op.drop_column("environment_host")
        batch_op.drop_column("timezone")
        batch_op.drop_column("start_with_pod")
        batch_op.drop_column("cgroups_mode")
        batch_op.drop_column("add_host")
        batch_op.drop_column("default_dependencies")
        batch_op.drop_column("service_name")
        batch_op.drop_column("stop_signal")
        batch_op.drop_column("group_add")
        batch_op.drop_column("run_init")
        batch_op.drop_column("stop_timeout")
        batch_op.drop_column("global_args")
        batch_op.drop_column("containers_conf_module")
        batch_op.drop_column("sub_gid_map")
        batch_op.drop_column("sub_uid_map")
        batch_op.drop_column("read_only_tmpfs")
        batch_op.drop_column("shm_size")
        batch_op.drop_column("ulimits")
        batch_op.drop_column("pids_limit")
        batch_op.drop_column("security_label_nested")
        batch_op.drop_column("pull")
        batch_op.drop_column("rootfs")
        batch_op.drop_column("mount")
        batch_op.drop_column("ip6")
        batch_op.drop_column("ip")
        batch_op.drop_column("tmpfs")
        batch_op.drop_column("security_label_type")
        batch_op.drop_column("security_label_level")
        batch_op.drop_column("security_label_file_type")
        batch_op.drop_column("security_label_disable")
        batch_op.drop_column("group")
        batch_op.drop_column("expose_host_port")
        batch_op.drop_column("annotation")

    # ------------------------------------------------------------------
    # compartments — drop added columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("compartments") as batch_op:
        batch_op.drop_column("net_interface_name")
        batch_op.drop_column("net_delete_on_stop")
        batch_op.drop_column("net_service_name")
        batch_op.drop_column("net_dns")
        batch_op.drop_column("net_ipam_driver")
        batch_op.drop_column("net_podman_args")
        batch_op.drop_column("net_global_args")
        batch_op.drop_column("net_containers_conf_module")
        batch_op.drop_column("net_label")
        batch_op.drop_column("net_options")
        batch_op.drop_column("net_ip_range")
        batch_op.drop_column("net_disable_dns")

    # ------------------------------------------------------------------
    # Drop new tables
    # ------------------------------------------------------------------
    op.drop_index("ix_artifacts_compartment_id", "artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_kubes_compartment_id", "kubes")
    op.drop_table("kubes")

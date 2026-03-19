"""baseline_schema_from_migration_009

Revision ID: 0001
Revises:
Create Date: 2026-03-19

This is a baseline revision that creates the complete schema as it existed after
all 9 numbered SQL migrations (001–009). New databases get the full schema in one
step. Existing databases that were managed by the old migration runner should be
stamped to this revision with ``alembic stamp 0001`` rather than run through this
upgrade, because the tables already exist.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the complete baseline schema."""
    op.create_table(
        "compartments",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("linux_user", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.Column(
            "updated_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.Column("net_driver", sa.Text, nullable=False, server_default=""),
        sa.Column("net_subnet", sa.Text, nullable=False, server_default=""),
        sa.Column("net_gateway", sa.Text, nullable=False, server_default=""),
        sa.Column("net_ipv6", sa.Integer, nullable=False, server_default="0"),
        sa.Column("net_internal", sa.Integer, nullable=False, server_default="0"),
        sa.Column("net_dns_enabled", sa.Integer, nullable=False, server_default="0"),
        sa.Column("connection_monitor_enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column("process_monitor_enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column("connection_history_retention_days", sa.Integer, nullable=True),
    )

    op.create_table(
        "containers",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("image", sa.Text, nullable=False, server_default=""),
        sa.Column("environment", sa.Text, nullable=False, server_default="{}"),
        sa.Column("ports", sa.Text, nullable=False, server_default="[]"),
        sa.Column("volumes", sa.Text, nullable=False, server_default="[]"),
        sa.Column("labels", sa.Text, nullable=False, server_default="{}"),
        sa.Column("network", sa.Text, nullable=False, server_default="host"),
        sa.Column("restart_policy", sa.Text, nullable=False, server_default="always"),
        sa.Column("exec_start_pre", sa.Text, nullable=False, server_default=""),
        sa.Column("memory_limit", sa.Text, nullable=False, server_default=""),
        sa.Column("cpu_quota", sa.Text, nullable=False, server_default=""),
        sa.Column("depends_on", sa.Text, nullable=False, server_default="[]"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.Column(
            "updated_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.Column("apparmor_profile", sa.Text, nullable=False, server_default=""),
        sa.Column("build_context", sa.Text, nullable=False, server_default=""),
        sa.Column("build_file", sa.Text, nullable=False, server_default=""),
        sa.Column("run_user", sa.Text, nullable=False, server_default=""),
        sa.Column("containerfile_content", sa.Text, nullable=False, server_default=""),
        sa.Column("bind_mounts", sa.Text, nullable=False, server_default="[]"),
        sa.Column("user_ns", sa.Text, nullable=False, server_default=""),
        sa.Column("uid_map", sa.Text, nullable=False, server_default="[]"),
        sa.Column("gid_map", sa.Text, nullable=False, server_default="[]"),
        sa.Column("health_cmd", sa.Text, nullable=False, server_default=""),
        sa.Column("health_interval", sa.Text, nullable=False, server_default=""),
        sa.Column("health_timeout", sa.Text, nullable=False, server_default=""),
        sa.Column("health_retries", sa.Text, nullable=False, server_default=""),
        sa.Column("health_start_period", sa.Text, nullable=False, server_default=""),
        sa.Column("health_on_failure", sa.Text, nullable=False, server_default=""),
        sa.Column("notify_healthy", sa.Integer, nullable=False, server_default="0"),
        sa.Column("auto_update", sa.Text, nullable=False, server_default=""),
        sa.Column("environment_file", sa.Text, nullable=False, server_default=""),
        sa.Column("exec_cmd", sa.Text, nullable=False, server_default=""),
        sa.Column("entrypoint", sa.Text, nullable=False, server_default=""),
        sa.Column("no_new_privileges", sa.Integer, nullable=False, server_default="0"),
        sa.Column("read_only", sa.Integer, nullable=False, server_default="0"),
        sa.Column("working_dir", sa.Text, nullable=False, server_default=""),
        sa.Column("drop_caps", sa.Text, nullable=False, server_default="[]"),
        sa.Column("add_caps", sa.Text, nullable=False, server_default="[]"),
        sa.Column("sysctl", sa.Text, nullable=False, server_default="{}"),
        sa.Column("seccomp_profile", sa.Text, nullable=False, server_default=""),
        sa.Column("mask_paths", sa.Text, nullable=False, server_default="[]"),
        sa.Column("unmask_paths", sa.Text, nullable=False, server_default="[]"),
        sa.Column("privileged", sa.Integer, nullable=False, server_default="0"),
        sa.Column("hostname", sa.Text, nullable=False, server_default=""),
        sa.Column("dns", sa.Text, nullable=False, server_default="[]"),
        sa.Column("dns_search", sa.Text, nullable=False, server_default="[]"),
        sa.Column("dns_option", sa.Text, nullable=False, server_default="[]"),
        sa.Column("pod_name", sa.Text, nullable=False, server_default=""),
        sa.Column("log_driver", sa.Text, nullable=False, server_default=""),
        sa.Column("log_opt", sa.Text, nullable=False, server_default="{}"),
        sa.Column("exec_start_post", sa.Text, nullable=False, server_default=""),
        sa.Column("exec_stop", sa.Text, nullable=False, server_default=""),
        sa.Column("secrets", sa.Text, nullable=False, server_default="[]"),
        sa.Column("devices", sa.Text, nullable=False, server_default="[]"),
        sa.Column("runtime", sa.Text, nullable=False, server_default=""),
        sa.Column("service_extra", sa.Text, nullable=False, server_default=""),
        sa.Column("init", sa.Integer, nullable=False, server_default="0"),
        sa.Column("memory_reservation", sa.Text, nullable=False, server_default=""),
        sa.Column("cpu_weight", sa.Text, nullable=False, server_default=""),
        sa.Column("io_weight", sa.Text, nullable=False, server_default=""),
        sa.Column("network_aliases", sa.Text, nullable=False, server_default="[]"),
        sa.UniqueConstraint("compartment_id", "name"),
    )

    op.create_table(
        "volumes",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("selinux_context", sa.Text, nullable=False, server_default="container_file_t"),
        sa.Column("owner_uid", sa.Integer, nullable=False, server_default="0"),
        sa.Column("use_quadlet", sa.Integer, nullable=False, server_default="0"),
        sa.Column("vol_driver", sa.Text, nullable=False, server_default=""),
        sa.Column("vol_device", sa.Text, nullable=False, server_default=""),
        sa.Column("vol_options", sa.Text, nullable=False, server_default=""),
        sa.Column("vol_copy", sa.Integer, nullable=False, server_default="1"),
        sa.Column("vol_group", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.UniqueConstraint("compartment_id", "name"),
    )

    op.create_table(
        "pods",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("network", sa.Text, nullable=False, server_default=""),
        sa.Column("publish_ports", sa.Text, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
    )

    op.create_table(
        "image_units",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("image", sa.Text, nullable=False),
        sa.Column("auth_file", sa.Text, nullable=False, server_default=""),
        sa.Column("pull_policy", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
    )

    op.create_table(
        "system_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("compartment_id", sa.Text, nullable=True),
        sa.Column("container_id", sa.Text, nullable=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
    )

    op.create_table(
        "secrets",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.UniqueConstraint("compartment_id", "name"),
    )

    op.create_table(
        "timers",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "container_id",
            sa.Text,
            sa.ForeignKey("containers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("on_calendar", sa.Text, nullable=False, server_default=""),
        sa.Column("on_boot_sec", sa.Text, nullable=False, server_default=""),
        sa.Column("random_delay_sec", sa.Text, nullable=False, server_default=""),
        sa.Column("persistent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.UniqueConstraint("compartment_id", "name"),
    )

    op.create_table(
        "templates",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("config_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
    )

    op.create_table(
        "notification_hooks",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("container_name", sa.Text, nullable=False, server_default=""),
        sa.Column("event_type", sa.Text, nullable=False, server_default="on_failure"),
        sa.Column("webhook_url", sa.Text, nullable=False),
        sa.Column("webhook_secret", sa.Text, nullable=False, server_default=""),
        sa.Column("enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
    )

    op.create_table(
        "metrics_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.Column("cpu_percent", sa.Float, nullable=False, server_default="0"),
        sa.Column("memory_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("disk_bytes", sa.Integer, nullable=False, server_default="0"),
    )

    op.create_table(
        "container_restart_stats",
        sa.Column("compartment_id", sa.Text, primary_key=True),
        sa.Column("container_name", sa.Text, primary_key=True),
        sa.Column("restart_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_failure_at", sa.Text, nullable=True),
        sa.Column("last_restart_at", sa.Text, nullable=True),
    )

    op.create_table(
        "processes",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("process_name", sa.Text, nullable=False),
        sa.Column("cmdline", sa.Text, nullable=False, server_default=""),
        sa.Column("known", sa.Integer, nullable=False, server_default="0"),
        sa.Column("times_seen", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "first_seen_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.Column(
            "last_seen_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.UniqueConstraint("compartment_id", "process_name", "cmdline"),
    )

    op.create_table(
        "connections",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("container_name", sa.Text, nullable=False, server_default=""),
        sa.Column("proto", sa.Text, nullable=False),
        sa.Column("dst_ip", sa.Text, nullable=False),
        sa.Column("dst_port", sa.Integer, nullable=False),
        sa.Column("direction", sa.Text, nullable=False, server_default="outbound"),
        sa.Column("times_seen", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "first_seen_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.Column(
            "last_seen_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.UniqueConstraint(
            "compartment_id", "container_name", "proto", "dst_ip", "dst_port", "direction"
        ),
    )

    op.create_table(
        "connection_whitelist_rules",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "compartment_id",
            sa.Text,
            sa.ForeignKey("compartments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("container_name", sa.Text, nullable=True),
        sa.Column("proto", sa.Text, nullable=True),
        sa.Column("dst_ip", sa.Text, nullable=True),
        sa.Column("dst_port", sa.Integer, nullable=True),
        sa.Column("direction", sa.Text, nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
    )


def downgrade() -> None:
    """Drop all baseline tables (in reverse dependency order)."""
    op.drop_table("connection_whitelist_rules")
    op.drop_table("connections")
    op.drop_table("processes")
    op.drop_table("container_restart_stats")
    op.drop_table("metrics_history")
    op.drop_table("notification_hooks")
    op.drop_table("templates")
    op.drop_table("timers")
    op.drop_table("secrets")
    op.drop_table("system_events")
    op.drop_table("image_units")
    op.drop_table("pods")
    op.drop_table("volumes")
    op.drop_table("containers")
    op.drop_table("compartments")

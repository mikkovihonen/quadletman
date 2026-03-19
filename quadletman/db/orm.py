"""SQLAlchemy ORM table definitions — single source of truth for the database schema.

These are plain Table/Mapped classes used exclusively by the persistence layer.
All user-visible data is returned as Pydantic models (quadletman/models/db.py) via
the service layer; ORM objects are never exposed to routers directly.

JSON columns are declared as Text here (matching SQLite's wire type) so that
from_row() in models/db.py can call json.loads() on them without double-deserialisation.
Service layer code is responsible for json.dumps() on insert/update.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> str:
    """ISO-8601 UTC timestamp string matching SQLite DEFAULT format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# compartments
# ---------------------------------------------------------------------------


class CompartmentRow(Base):
    __tablename__ = "compartments"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    linux_user: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )
    net_driver: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    net_subnet: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    net_gateway: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    net_ipv6: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    net_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    net_dns_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    connection_monitor_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    process_monitor_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    connection_history_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    containers: Mapped[list["ContainerRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )
    volumes: Mapped[list["VolumeRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )
    pods: Mapped[list["PodRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )
    image_units: Mapped[list["ImageUnitRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# containers
# ---------------------------------------------------------------------------


class ContainerRow(Base):
    __tablename__ = "containers"
    __table_args__ = (UniqueConstraint("compartment_id", "name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    image: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    environment: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    ports: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    volumes: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    labels: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    network: Mapped[str] = mapped_column(
        Text, nullable=False, default="host", server_default="host"
    )
    restart_policy: Mapped[str] = mapped_column(
        Text, nullable=False, default="always", server_default="always"
    )
    exec_start_pre: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    memory_limit: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    cpu_quota: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    depends_on: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )
    apparmor_profile: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    build_context: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    build_file: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    run_user: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    containerfile_content: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    bind_mounts: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    user_ns: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    uid_map: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    gid_map: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    health_cmd: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    health_interval: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    health_timeout: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    health_retries: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    health_start_period: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    health_on_failure: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    notify_healthy: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    auto_update: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    environment_file: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    exec_cmd: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    entrypoint: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    no_new_privileges: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    read_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    working_dir: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    drop_caps: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    add_caps: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    sysctl: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    seccomp_profile: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    mask_paths: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    unmask_paths: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    privileged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    hostname: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    dns: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    dns_search: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    dns_option: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    pod_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    log_driver: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    log_opt: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    exec_start_post: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    exec_stop: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # migration 002
    secrets: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    # migration 003
    devices: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    runtime: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    service_extra: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    init: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    memory_reservation: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    cpu_weight: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    io_weight: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    network_aliases: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="containers")


# ---------------------------------------------------------------------------
# volumes
# ---------------------------------------------------------------------------


class VolumeRow(Base):
    __tablename__ = "volumes"
    __table_args__ = (UniqueConstraint("compartment_id", "name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    selinux_context: Mapped[str] = mapped_column(
        Text, nullable=False, default="container_file_t", server_default="container_file_t"
    )
    owner_uid: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    use_quadlet: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    vol_driver: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    vol_device: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    vol_options: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    vol_copy: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    vol_group: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="volumes")


# ---------------------------------------------------------------------------
# pods
# ---------------------------------------------------------------------------


class PodRow(Base):
    __tablename__ = "pods"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    network: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    publish_ports: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="pods")


# ---------------------------------------------------------------------------
# image_units
# ---------------------------------------------------------------------------


class ImageUnitRow(Base):
    __tablename__ = "image_units"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    image: Mapped[str] = mapped_column(Text, nullable=False)
    auth_file: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    pull_policy: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="image_units")


# ---------------------------------------------------------------------------
# system_events
# ---------------------------------------------------------------------------


class SystemEventRow(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    compartment_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    container_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------


class SecretRow(Base):
    __tablename__ = "secrets"
    __table_args__ = (UniqueConstraint("compartment_id", "name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )


# ---------------------------------------------------------------------------
# timers
# ---------------------------------------------------------------------------


class TimerRow(Base):
    __tablename__ = "timers"
    __table_args__ = (UniqueConstraint("compartment_id", "name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    container_id: Mapped[str] = mapped_column(
        Text, ForeignKey("containers.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    on_calendar: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    on_boot_sec: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    random_delay_sec: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    persistent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------


class TemplateRow(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    config_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )


# ---------------------------------------------------------------------------
# notification_hooks
# ---------------------------------------------------------------------------


class NotificationHookRow(Base):
    __tablename__ = "notification_hooks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    container_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    event_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="on_failure", server_default="on_failure"
    )
    webhook_url: Mapped[str] = mapped_column(Text, nullable=False)
    webhook_secret: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )


# ---------------------------------------------------------------------------
# metrics_history
# ---------------------------------------------------------------------------


class MetricsHistoryRow(Base):
    __tablename__ = "metrics_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    recorded_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )
    cpu_percent: Mapped[float] = mapped_column(nullable=False, default=0.0)
    memory_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    disk_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


# ---------------------------------------------------------------------------
# container_restart_stats
# ---------------------------------------------------------------------------


class ContainerRestartStatsRow(Base):
    __tablename__ = "container_restart_stats"

    compartment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    container_name: Mapped[str] = mapped_column(Text, primary_key=True)
    restart_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_failure_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_restart_at: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# processes
# ---------------------------------------------------------------------------


class ProcessRow(Base):
    __tablename__ = "processes"
    __table_args__ = (UniqueConstraint("compartment_id", "process_name", "cmdline"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    process_name: Mapped[str] = mapped_column(Text, nullable=False)
    cmdline: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    known: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )
    last_seen_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )


# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------


class ConnectionRow(Base):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint(
            "compartment_id", "container_name", "proto", "dst_ip", "dst_port", "direction"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    container_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    proto: Mapped[str] = mapped_column(Text, nullable=False)
    dst_ip: Mapped[str] = mapped_column(Text, nullable=False)
    dst_port: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(
        Text, nullable=False, default="outbound", server_default="outbound"
    )
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )
    last_seen_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )


# ---------------------------------------------------------------------------
# connection_whitelist_rules
# ---------------------------------------------------------------------------


class WhitelistRuleRow(Base):
    __tablename__ = "connection_whitelist_rules"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    container_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    proto: Mapped[str | None] = mapped_column(Text, nullable=True)
    dst_ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    dst_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )


# ---------------------------------------------------------------------------
# Automatically update updated_at on compartments and containers
# (mirrors the SQLite TRIGGER in the original schema)
# ---------------------------------------------------------------------------


@event.listens_for(CompartmentRow, "before_update")
def _compartment_updated_at(mapper, connection, target):
    target.updated_at = _utcnow()


@event.listens_for(ContainerRow, "before_update")
def _container_updated_at(mapper, connection, target):
    target.updated_at = _utcnow()

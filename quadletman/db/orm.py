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
    connection_monitor_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    process_monitor_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    connection_history_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_last_seen: Mapped[str | None] = mapped_column(Text, nullable=True)

    containers: Mapped[list["ContainerRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )
    volumes: Mapped[list["VolumeRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )
    pods: Mapped[list["PodRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )
    images: Mapped[list["ImageRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )
    builds: Mapped[list["BuildRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )
    kubes: Mapped[list["KubeRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["ArtifactRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )
    networks: Mapped[list["NetworkRow"]] = relationship(
        back_populates="compartment", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# networks
# ---------------------------------------------------------------------------


class NetworkRow(Base):
    __tablename__ = "networks"
    __table_args__ = (UniqueConstraint("compartment_id", "qm_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qm_name: Mapped[str] = mapped_column(Text, nullable=False)
    driver: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    subnet: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    gateway: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    ipv6: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    dns_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    disable_dns: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    ip_range: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    options: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    label: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    containers_conf_module: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    global_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    podman_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    ipam_driver: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    dns: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    service_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    network_delete_on_stop: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    interface_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # migration 0008 — NetworkName override
    network_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="networks")


# ---------------------------------------------------------------------------
# containers
# ---------------------------------------------------------------------------


class ContainerRow(Base):
    __tablename__ = "containers"
    __table_args__ = (UniqueConstraint("compartment_id", "qm_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qm_name: Mapped[str] = mapped_column(Text, nullable=False)
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
    qm_sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
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
    qm_build_unit_name: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    run_user: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
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
    hostname: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    dns: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    dns_search: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    dns_option: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    pod: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
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
    # migration 0002 — Podman 4.4.0+ container fields
    annotation: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    expose_host_port: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    group: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    security_label_disable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    security_label_file_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    security_label_level: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    security_label_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    # Podman 4.5.0
    tmpfs: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    ip: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    ip6: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    mount: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    rootfs: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 4.6.0
    pull: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    security_label_nested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # Podman 4.7.0
    pids_limit: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    ulimits: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    shm_size: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 4.8.0
    read_only_tmpfs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    sub_uid_map: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    sub_gid_map: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 5.0.0
    containers_conf_module: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    global_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    stop_timeout: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    run_init: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # Podman 5.1.0
    group_add: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    # Podman 5.2.0
    stop_signal: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 5.3.0
    service_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    default_dependencies: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    add_host: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    cgroups_mode: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    start_with_pod: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 5.5.0
    environment_host: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    memory: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    reload_cmd: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    reload_signal: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    retry: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    retry_delay: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    health_log_destination: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    health_max_log_count: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    health_max_log_size: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    health_startup_cmd: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    health_startup_interval: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    health_startup_retries: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    health_startup_success: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    health_startup_timeout: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    # Podman 5.7.0
    http_proxy: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # migration 0008 — ContainerName override
    container_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="containers")


# ---------------------------------------------------------------------------
# volumes
# ---------------------------------------------------------------------------


class VolumeRow(Base):
    __tablename__ = "volumes"
    __table_args__ = (UniqueConstraint("compartment_id", "qm_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qm_name: Mapped[str] = mapped_column(Text, nullable=False)
    qm_selinux_context: Mapped[str] = mapped_column(
        Text, nullable=False, default="container_file_t", server_default="container_file_t"
    )
    qm_owner_uid: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    qm_use_quadlet: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    driver: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    device: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    options: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    copy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    group: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )
    # migration 0002 — Podman 4.4.0+ volume fields
    gid: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    uid: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    user: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    image: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    type: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    label: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    # Podman 5.0.0
    containers_conf_module: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    global_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    podman_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    # Podman 5.3.0
    service_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # migration 0008 — VolumeName override
    volume_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="volumes")


# ---------------------------------------------------------------------------
# pods
# ---------------------------------------------------------------------------


class PodRow(Base):
    __tablename__ = "pods"
    __table_args__ = (UniqueConstraint("compartment_id", "qm_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qm_name: Mapped[str] = mapped_column(Text, nullable=False)
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
    # migration 0002 — Podman 5.0.0+ pod fields
    containers_conf_module: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    global_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    podman_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    volumes: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    # Podman 5.3.0
    service_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    dns: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    dns_search: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    dns_option: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    ip: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    ip6: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    user_ns: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    add_host: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    uid_map: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    gid_map: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    sub_uid_map: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    sub_gid_map: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    network_aliases: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    # Podman 5.4.0
    shm_size: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 5.5.0
    hostname: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 5.6.0
    labels: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    exit_policy: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 5.7.0
    stop_timeout: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # migration 0008 — PodName override
    pod_name_override: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="pods")


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------


class ImageRow(Base):
    __tablename__ = "images"
    __table_args__ = (UniqueConstraint("compartment_id", "qm_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qm_name: Mapped[str] = mapped_column(Text, nullable=False)
    image: Mapped[str] = mapped_column(Text, nullable=False)
    auth_file: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )
    # migration 0002 — Podman 4.8.0+ image unit fields
    all_tags: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    arch: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    cert_dir: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    creds: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    decryption_key: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    os: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    tls_verify: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    variant: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 5.0.0
    containers_conf_module: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    global_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    podman_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    # Podman 5.3.0
    service_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    image_tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    # Podman 5.5.0
    retry: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    retry_delay: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 5.6.0
    policy: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="images")


# ---------------------------------------------------------------------------
# builds
# ---------------------------------------------------------------------------


class BuildRow(Base):
    __tablename__ = "builds"
    __table_args__ = (UniqueConstraint("compartment_id", "qm_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qm_name: Mapped[str] = mapped_column(Text, nullable=False)
    image_tag: Mapped[str] = mapped_column(Text, nullable=False)
    qm_containerfile_content: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    build_context: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    build_file: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
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
    # Podman 5.2.0 — .build unit fields
    annotation: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    arch: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    auth_file: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    containers_conf_module: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    dns: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    dns_option: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    dns_search: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    env: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    force_rm: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    global_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    group_add: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    label: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    network: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    podman_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    pull: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    secret: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    target: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    tls_verify: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    variant: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    volume: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    # Podman 5.3.0
    service_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 5.5.0
    retry: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    retry_delay: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Podman 5.7.0
    build_args: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    ignore_file: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="builds")


# ---------------------------------------------------------------------------
# kubes
# ---------------------------------------------------------------------------


class KubeRow(Base):
    __tablename__ = "kubes"
    __table_args__ = (UniqueConstraint("compartment_id", "qm_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qm_name: Mapped[str] = mapped_column(Text, nullable=False)
    qm_yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    yaml: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    config_map: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    network: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    publish_ports: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    log_driver: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    user_ns: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    auto_update: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    exit_code_propagation: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    containers_conf_module: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    global_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    podman_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    kube_down_force: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    set_working_directory: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    service_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="kubes")


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("compartment_id", "qm_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qm_name: Mapped[str] = mapped_column(Text, nullable=False)
    artifact: Mapped[str] = mapped_column(Text, nullable=False)
    service_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # migration 0008 — full artifact fields
    auth_file: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    cert_dir: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    containers_conf_module: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    creds: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    decryption_key: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    global_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    podman_args: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    quiet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    retry: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    retry_delay: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    tls_verify: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )

    compartment: Mapped["CompartmentRow"] = relationship(back_populates="artifacts")


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
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
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
    __table_args__ = (UniqueConstraint("compartment_id", "qm_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qm_container_id: Mapped[str] = mapped_column(
        Text, ForeignKey("containers.id", ondelete="CASCADE"), nullable=False
    )
    qm_name: Mapped[str] = mapped_column(Text, nullable=False)
    on_calendar: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    on_boot_sec: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    random_delay_sec: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    persistent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    qm_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
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
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qm_container_name: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
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
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
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

    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), primary_key=True
    )
    container_name: Mapped[str] = mapped_column(Text, primary_key=True)
    restart_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_failure_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_restart_at: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# process_patterns
# ---------------------------------------------------------------------------


class ProcessPatternRow(Base):
    __tablename__ = "process_patterns"
    __table_args__ = (UniqueConstraint("compartment_id", "process_name", "cmdline_pattern"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    process_name: Mapped[str] = mapped_column(Text, nullable=False)
    cmdline_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    segments_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utcnow,
        server_default=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
    )


# ---------------------------------------------------------------------------
# processes
# ---------------------------------------------------------------------------


class ProcessRow(Base):
    __tablename__ = "processes"
    __table_args__ = (UniqueConstraint("compartment_id", "process_name", "cmdline"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    process_name: Mapped[str] = mapped_column(Text, nullable=False)
    cmdline: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    known: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    pattern_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("process_patterns.id", ondelete="SET NULL"), nullable=True
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
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
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
# connection_allowlist_rules
# ---------------------------------------------------------------------------


class AllowlistRuleRow(Base):
    __tablename__ = "connection_allowlist_rules"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    compartment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("compartments.id", ondelete="CASCADE"), nullable=False, index=True
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

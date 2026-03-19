import json as _json
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .sanitized import (
    SafeAbsPath,
    SafeImageRef,
    SafeIpAddress,
    SafeMultilineStr,
    SafePortMapping,
    SafeResourceName,
    SafeSecretName,
    SafeSELinuxContext,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUUID,
    SafeWebhookUrl,
    enforce_model,
)

# Keep the compiled regex accessible under the old private name for internal use.
_CONTROL_CHARS_RE = re.compile(r"[\r\n\x00]")

# Host path prefixes that must not be bind-mounted into containers
_BIND_MOUNT_DENYLIST = (
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/root",
    "/var/lib/quadletman",
    "/run/dbus",
)

_EventType = Literal[
    "on_failure",
    "on_restart",
    "on_start",
    "on_stop",
    "on_unexpected_process",
    "on_unexpected_connection",
]
_Proto = Literal["tcp", "udp"]
_Direction = Literal["outbound", "inbound"]


def _no_control_chars(v: str, field_name: str = "value") -> SafeStr:
    """Reject strings containing control chars and return a ``SafeStr`` instance.

    Returning ``SafeStr`` (a branded ``str`` subclass) is the proof that this
    check has been performed.  Downstream service functions that accept
    ``SafeStr`` parameters can verify with ``sanitized.require()``.
    """
    return SafeStr.of(v, field_name)


@enforce_model
class VolumeCreate(BaseModel):
    name: SafeResourceName
    selinux_context: SafeSELinuxContext = SafeSELinuxContext.trusted("container_file_t", "default")
    owner_uid: int = Field(default=0, ge=0)
    """Container UID that should own this volume directory.

    0 (default) = compartment root (host UID).  Any other value N causes the directory
    to be owned by the helper user qm-{compartment_id}-N (host UID = subuid_start + N),
    so that container processes running as UID N have direct ownership access.
    """
    # Quadlet-managed volume (generates a .volume unit instead of a host directory)
    use_quadlet: bool = False
    vol_driver: SafeStr = SafeStr.trusted("", "default")  # e.g. "local", "overlay"
    vol_device: SafeStr = SafeStr.trusted("", "default")  # device path for local driver
    vol_options: SafeStr = SafeStr.trusted("", "default")  # mount options string
    vol_copy: bool = True  # Copy=true/false (default true — copy image data on first use)
    vol_group: SafeStr = SafeStr.trusted("", "default")  # optional GID for volume group ownership


@enforce_model
class VolumeMount(BaseModel):
    """A managed service volume mounted into a container."""

    volume_id: SafeUUID  # references volumes.id
    container_path: SafeAbsPath
    options: SafeStr = SafeStr.trusted("Z", "default")  # SELinux relabeling by default


@enforce_model
class BindMount(BaseModel):
    """An arbitrary host path mounted into a container."""

    host_path: SafeStr
    container_path: SafeStr
    options: SafeStr = SafeStr.trusted("", "default")

    @field_validator("host_path", "container_path")
    @classmethod
    def validate_absolute_path(cls, v: str, info) -> SafeStr:
        safe = SafeStr.of(v, info.field_name)
        if safe and not safe.startswith("/"):
            raise ValueError(f"{info.field_name} must be an absolute path")
        return safe

    @field_validator("host_path")
    @classmethod
    def validate_host_path_not_sensitive(cls, v: SafeStr) -> SafeStr:
        if not v:
            return v
        # Normalise away trailing slashes before checking
        normalised = v.rstrip("/")
        for denied in _BIND_MOUNT_DENYLIST:
            if normalised == denied or normalised.startswith(denied + "/"):
                raise ValueError(f"host_path '{v}' is within a restricted directory ({denied})")
        return v


@enforce_model
class ContainerCreate(BaseModel):
    name: SafeResourceName
    image: SafeImageRef
    environment: dict[SafeStr, SafeStr] = {}
    ports: list[SafePortMapping] = []
    volumes: list[VolumeMount] = []
    labels: dict[SafeStr, SafeStr] = {}
    network: SafeStr = SafeStr.trusted("host", "default")
    restart_policy: SafeStr = SafeStr.trusted("always", "default")
    exec_start_pre: SafeStr = SafeStr.trusted("", "default")
    memory_limit: SafeStr = SafeStr.trusted("", "default")
    cpu_quota: SafeStr = SafeStr.trusted("", "default")
    depends_on: list[SafeResourceName] = []
    sort_order: int = 0
    apparmor_profile: SafeStr = SafeStr.trusted("", "default")
    build_context: SafeStr = SafeStr.trusted("", "default")
    build_file: SafeStr = SafeStr.trusted("", "default")
    containerfile_content: SafeMultilineStr = SafeMultilineStr.trusted("", "default")
    bind_mounts: list[BindMount] = []
    run_user: SafeStr = SafeStr.trusted("", "default")
    user_ns: SafeStr = SafeStr.trusted(
        "", "default"
    )  # kept for DB compat, superseded by uid_map/gid_map
    uid_map: list[SafeStr] = []
    gid_map: list[SafeStr] = []
    # Health checks
    health_cmd: SafeStr = SafeStr.trusted("", "default")
    health_interval: SafeStr = SafeStr.trusted("", "default")
    health_timeout: SafeStr = SafeStr.trusted("", "default")
    health_retries: SafeStr = SafeStr.trusted("", "default")
    health_start_period: SafeStr = SafeStr.trusted("", "default")
    health_on_failure: SafeStr = SafeStr.trusted("", "default")  # none | kill | restart | stop
    notify_healthy: bool = False
    # Image auto-update
    auto_update: SafeStr = SafeStr.trusted("", "default")  # registry | local
    # Environment file
    environment_file: SafeStr = SafeStr.trusted("", "default")
    # Command/entrypoint overrides
    exec_cmd: SafeStr = SafeStr.trusted("", "default")
    entrypoint: SafeStr = SafeStr.trusted("", "default")
    # Security options
    no_new_privileges: bool = False
    read_only: bool = False
    privileged: bool = False
    drop_caps: list[SafeStr] = []
    add_caps: list[SafeStr] = []
    seccomp_profile: SafeStr = SafeStr.trusted("", "default")
    mask_paths: list[SafeStr] = []
    unmask_paths: list[SafeStr] = []
    sysctl: dict[SafeStr, SafeStr] = {}
    # Runtime
    working_dir: SafeStr = SafeStr.trusted("", "default")
    # Networking
    hostname: SafeStr = SafeStr.trusted("", "default")
    dns: list[SafeStr] = []
    dns_search: list[SafeStr] = []
    dns_option: list[SafeStr] = []
    # Pod assignment (P2)
    pod_name: SafeStr = SafeStr.trusted("", "default")
    # Logging (P3)
    log_driver: SafeStr = SafeStr.trusted("", "default")  # e.g. "journald", "json-file", "none"
    log_opt: dict[SafeStr, SafeStr] = {}
    # Additional service lifecycle hooks (P3)
    exec_start_post: SafeStr = SafeStr.trusted("", "default")
    exec_stop: SafeStr = SafeStr.trusted("", "default")
    # Feature 1: host device passthrough
    devices: list[SafeStr] = []
    # Feature 2: OCI runtime (e.g. "crun", "kata", "gvisor")
    runtime: SafeStr = SafeStr.trusted("", "default")
    # Feature 3: raw extra [Service] directives (multi-line freeform)
    service_extra: SafeMultilineStr = SafeMultilineStr.trusted("", "default")
    # Feature 5: run an init process as PID 1
    init: bool = False
    # Feature 6: soft memory reservation and cgroup fair-share weights
    memory_reservation: SafeStr = SafeStr.trusted("", "default")
    cpu_weight: SafeStr = SafeStr.trusted("", "default")
    io_weight: SafeStr = SafeStr.trusted("", "default")
    # Feature 15: additional network aliases
    network_aliases: list[SafeStr] = []

    # Secrets referenced in the container unit (Secret= key)
    secrets: list[SafeSecretName] = []


@enforce_model
class ContainerUpdate(ContainerCreate):
    pass


@enforce_model
class PodCreate(BaseModel):
    name: SafeResourceName
    network: SafeStr = SafeStr.trusted("", "default")  # empty = use service default network
    publish_ports: list[SafePortMapping] = []


@enforce_model
class ImageUnitCreate(BaseModel):
    name: SafeResourceName
    image: SafeImageRef | Literal[""] = SafeStr.trusted("", "default")
    auth_file: SafeStr = SafeStr.trusted("", "default")
    pull_policy: SafeStr = SafeStr.trusted(
        "", "default"
    )  # "always" | "missing" | "never" | "newer"

    @field_validator("image")
    @classmethod
    def validate_image(cls, v: str) -> SafeImageRef | Literal[""]:
        if not v:
            return v
        return SafeImageRef.of(v, "image")


@enforce_model
class CompartmentCreate(BaseModel):
    id: SafeSlug = Field(..., description="Slug used as compartment ID and user suffix")
    description: SafeStr = SafeStr.trusted("", "default")

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> SafeSlug:
        slug = SafeSlug.of(v, "id")
        if slug.startswith("qm-"):
            raise ValueError("Compartment ID must not start with 'qm-'")
        return slug


@enforce_model
class CompartmentUpdate(BaseModel):
    description: SafeStr | None = None

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str | None) -> SafeStr | None:
        if v is None:
            return v
        return SafeStr.of(v, "description")


@enforce_model
class CompartmentNetworkUpdate(BaseModel):
    """Configures the optional shared Podman network unit for a compartment."""

    net_driver: SafeStr = SafeStr.trusted(
        "", "default"
    )  # bridge | macvlan | ipvlan (empty = Podman default)
    net_subnet: SafeStr = SafeStr.trusted("", "default")  # CIDR, e.g. 10.89.1.0/24
    net_gateway: SafeStr = SafeStr.trusted("", "default")  # gateway IP within subnet
    net_ipv6: bool = False
    net_internal: bool = False  # isolate from external routing
    net_dns_enabled: bool = False  # enable Podman DNS plugin (name resolution)


@enforce_model
class CompartmentStatus(BaseModel):
    compartment_id: SafeSlug
    containers: list[dict[SafeStr, SafeStr]] = []


@enforce_model
class SecretCreate(BaseModel):
    name: SafeSecretName


@enforce_model
class TimerCreate(BaseModel):
    name: SafeResourceName
    container_id: SafeUUID
    on_calendar: SafeStr = SafeStr.trusted("", "default")
    on_boot_sec: SafeStr = SafeStr.trusted("", "default")
    random_delay_sec: SafeStr = SafeStr.trusted("", "default")
    persistent: bool = False
    enabled: bool = True


@enforce_model
class TemplateCreate(BaseModel):
    name: SafeStr
    description: SafeStr = SafeStr.trusted("", "default")
    source_compartment_id: SafeSlug


@enforce_model
class TemplateInstantiate(BaseModel):
    """Body for POST /api/compartments/from-template/{template_id}."""

    compartment_id: SafeSlug = Field(..., description="New compartment ID (slug)")
    description: SafeStr = SafeStr.trusted("", "default")

    @field_validator("compartment_id")
    @classmethod
    def validate_id(cls, v: str) -> SafeSlug:
        slug = SafeSlug.of(v, "compartment_id")
        if slug.startswith("qm-"):
            raise ValueError("Compartment ID must not start with 'qm-'")
        return slug


@enforce_model
class NotificationHookCreate(BaseModel):
    container_name: SafeStr = SafeStr.trusted("", "default")  # empty = any container in compartment
    event_type: _EventType = "on_failure"
    webhook_url: SafeWebhookUrl
    webhook_secret: SafeStr = SafeStr.trusted("", "default")
    enabled: bool = True


# ---------------------------------------------------------------------------
# DB response models (formerly models/db.py)
# These replace from_row() — use Model.model_validate(dict(row)) at call sites.
# Branded-type coercion is handled automatically by __get_pydantic_core_schema__.
# JSON columns (stored as TEXT) are decoded by the model_validator below.
# ---------------------------------------------------------------------------


def _loads(d: dict, *fields: str) -> None:
    """In-place JSON-decode string values for the given fields."""
    for f in fields:
        v = d.get(f)
        if isinstance(v, str):
            d[f] = _json.loads(v)


@enforce_model
class Volume(VolumeCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    host_path: SafeStr = SafeStr.trusted("", "default")
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("use_quadlet", 0)
        d.setdefault("vol_driver", "")
        d.setdefault("vol_device", "")
        d.setdefault("vol_options", "")
        d.setdefault("vol_copy", 1)
        d.setdefault("vol_group", "")
        d.setdefault("host_path", "")
        return d


@enforce_model
class Container(ContainerCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp
    updated_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        _loads(
            d,
            "environment",
            "ports",
            "volumes",
            "labels",
            "depends_on",
            "bind_mounts",
            "uid_map",
            "gid_map",
            "drop_caps",
            "add_caps",
            "mask_paths",
            "unmask_paths",
            "dns",
            "dns_search",
            "dns_option",
            "sysctl",
            "log_opt",
            "secrets",
            "devices",
            "network_aliases",
        )
        for f in (
            "health_cmd",
            "health_interval",
            "health_timeout",
            "health_retries",
            "health_start_period",
            "health_on_failure",
            "auto_update",
            "environment_file",
            "exec_cmd",
            "entrypoint",
            "seccomp_profile",
            "working_dir",
            "hostname",
            "runtime",
            "service_extra",
            "memory_reservation",
            "cpu_weight",
            "io_weight",
            "pod_name",
            "log_driver",
            "exec_start_post",
            "exec_stop",
        ):
            d.setdefault(f, "")
        d.setdefault("notify_healthy", 0)
        d.setdefault("no_new_privileges", 0)
        d.setdefault("read_only", 0)
        d.setdefault("privileged", 0)
        d.setdefault("init", 0)
        return d


@enforce_model
class Pod(PodCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        _loads(d, "publish_ports")
        return d


@enforce_model
class ImageUnit(ImageUnitCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp


@enforce_model
class Compartment(BaseModel):
    id: SafeSlug
    description: SafeStr
    linux_user: SafeStr
    created_at: SafeTimestamp
    updated_at: SafeTimestamp
    containers: list[Container] = []
    volumes: list[Volume] = []
    pods: list[Pod] = []
    image_units: list[ImageUnit] = []
    net_driver: SafeStr = SafeStr.trusted("", "default")
    net_subnet: SafeStr = SafeStr.trusted("", "default")
    net_gateway: SafeStr = SafeStr.trusted("", "default")
    net_ipv6: bool = False
    net_internal: bool = False
    net_dns_enabled: bool = False
    connection_monitor_enabled: bool = True
    process_monitor_enabled: bool = True
    connection_history_retention_days: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("net_ipv6", 0)
        d.setdefault("net_internal", 0)
        d.setdefault("net_dns_enabled", 0)
        for f in ("net_driver", "net_subnet", "net_gateway"):
            d.setdefault(f, "")
        d.setdefault("connection_monitor_enabled", 1)
        d.setdefault("process_monitor_enabled", 1)
        d.setdefault("connection_history_retention_days", None)
        d.setdefault("containers", [])
        d.setdefault("volumes", [])
        d.setdefault("pods", [])
        d.setdefault("image_units", [])
        return d


@enforce_model
class SystemEvent(BaseModel):
    id: int
    compartment_id: SafeSlug | None
    container_id: SafeStr | None
    event_type: _EventType
    message: SafeMultilineStr
    created_at: SafeTimestamp


@enforce_model
class Secret(SecretCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp


@enforce_model
class Timer(TimerCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    container_name: SafeResourceName = SafeResourceName.trusted("", "default")
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("container_name", "")
        return d


@enforce_model
class Template(BaseModel):
    id: SafeUUID
    name: SafeStr
    description: SafeStr
    config_json: SafeMultilineStr
    created_at: SafeTimestamp


@enforce_model
class NotificationHook(NotificationHookCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp


@enforce_model
class Process(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    process_name: SafeStr
    cmdline: SafeMultilineStr
    known: bool
    times_seen: int
    first_seen_at: SafeTimestamp
    last_seen_at: SafeTimestamp


@enforce_model
class Connection(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    container_name: SafeResourceName
    proto: _Proto
    dst_ip: SafeIpAddress
    dst_port: int
    direction: _Direction
    times_seen: int
    first_seen_at: SafeTimestamp
    last_seen_at: SafeTimestamp
    whitelisted: bool = False

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.pop("known", None)
        d.setdefault("direction", "outbound")
        d.setdefault("whitelisted", False)
        return d


@enforce_model
class WhitelistRule(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    description: SafeStr
    container_name: SafeResourceName | None
    proto: _Proto | None
    dst_ip: SafeIpAddress | None
    dst_port: int | None
    direction: _Direction | None
    sort_order: int
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("direction", None)
        return d

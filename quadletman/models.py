import json
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$|^[a-z0-9]$")
_CONTROL_CHARS_RE = re.compile(r"[\r\n\x00]")
_IMAGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-/:@]*$")

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


def _no_control_chars(v: str, field_name: str = "value") -> str:
    """Reject strings containing newlines, carriage returns, or null bytes.

    These characters could allow injection of extra directives into systemd
    unit files rendered from Jinja2 templates.
    """
    if _CONTROL_CHARS_RE.search(v):
        raise ValueError(
            f"{field_name} must not contain newline, carriage return, or null byte characters"
        )
    return v


def new_id() -> str:
    return str(uuid.uuid4())


class VolumeCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$")
    selinux_context: str = Field(default="container_file_t", pattern=r"^[a-zA-Z0-9_]+$")
    owner_uid: int = Field(default=0, ge=0)
    """Container UID that should own this volume directory.

    0 (default) = compartment root (host UID).  Any other value N causes the directory
    to be owned by the helper user qm-{compartment_id}-N (host UID = subuid_start + N),
    so that container processes running as UID N have direct ownership access.
    """
    # Quadlet-managed volume (generates a .volume unit instead of a host directory)
    use_quadlet: bool = False
    vol_driver: str = ""  # e.g. "local", "overlay"
    vol_device: str = ""  # device path for local driver
    vol_options: str = ""  # mount options string
    vol_copy: bool = True  # Copy=true/false (default true — copy image data on first use)
    vol_group: str = ""  # optional GID for volume group ownership

    @field_validator("vol_driver", "vol_device", "vol_options", "vol_group")
    @classmethod
    def validate_no_control_chars(cls, v: str, info) -> str:
        return _no_control_chars(v, info.field_name)


class Volume(VolumeCreate):
    id: str
    compartment_id: str
    host_path: str = ""  # populated by service layer; empty for quadlet-managed volumes
    created_at: str

    @classmethod
    def from_row(cls, row) -> "Volume":
        d = dict(row)
        d.setdefault("use_quadlet", 0)
        d.setdefault("vol_driver", "")
        d.setdefault("vol_device", "")
        d.setdefault("vol_options", "")
        d.setdefault("vol_copy", 1)
        d.setdefault("vol_group", "")
        d.setdefault("host_path", "")
        return cls(**d)


class VolumeMount(BaseModel):
    """A managed service volume mounted into a container."""

    volume_id: str  # references volumes.id
    container_path: str
    options: str = "Z"  # SELinux relabeling by default


class BindMount(BaseModel):
    """An arbitrary host path mounted into a container."""

    host_path: str
    container_path: str
    options: str = ""

    @field_validator("host_path", "container_path", "options")
    @classmethod
    def validate_no_control_chars(cls, v: str, info) -> str:
        return _no_control_chars(v, info.field_name)

    @field_validator("host_path", "container_path")
    @classmethod
    def validate_absolute_path(cls, v: str, info) -> str:
        if v and not v.startswith("/"):
            raise ValueError(f"{info.field_name} must be an absolute path")
        return v

    @field_validator("host_path")
    @classmethod
    def validate_host_path_not_sensitive(cls, v: str) -> str:
        if not v:
            return v
        # Normalise away trailing slashes before checking
        normalised = v.rstrip("/")
        for denied in _BIND_MOUNT_DENYLIST:
            if normalised == denied or normalised.startswith(denied + "/"):
                raise ValueError(f"host_path '{v}' is within a restricted directory ({denied})")
        return v


class ContainerCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$")
    image: str
    environment: dict[str, str] = {}
    ports: list[str] = []
    volumes: list[VolumeMount] = []
    labels: dict[str, str] = {}
    network: str = "host"
    restart_policy: str = "always"
    exec_start_pre: str = ""
    memory_limit: str = ""
    cpu_quota: str = ""
    depends_on: list[str] = []
    sort_order: int = 0
    apparmor_profile: str = ""
    build_context: str = ""
    build_file: str = ""
    containerfile_content: str = ""
    bind_mounts: list[BindMount] = []
    run_user: str = ""
    user_ns: str = ""  # kept for DB compat, superseded by uid_map/gid_map
    uid_map: list[str] = []
    gid_map: list[str] = []
    # Health checks
    health_cmd: str = ""
    health_interval: str = ""
    health_timeout: str = ""
    health_retries: str = ""
    health_start_period: str = ""
    health_on_failure: str = ""  # none | kill | restart | stop
    notify_healthy: bool = False
    # Image auto-update
    auto_update: str = ""  # registry | local
    # Environment file
    environment_file: str = ""
    # Command/entrypoint overrides
    exec_cmd: str = ""
    entrypoint: str = ""
    # Security options
    no_new_privileges: bool = False
    read_only: bool = False
    privileged: bool = False
    drop_caps: list[str] = []
    add_caps: list[str] = []
    seccomp_profile: str = ""
    mask_paths: list[str] = []
    unmask_paths: list[str] = []
    sysctl: dict[str, str] = {}
    # Runtime
    working_dir: str = ""
    # Networking
    hostname: str = ""
    dns: list[str] = []
    dns_search: list[str] = []
    dns_option: list[str] = []
    # Pod assignment (P2)
    pod_name: str = ""
    # Logging (P3)
    log_driver: str = ""  # e.g. "journald", "json-file", "none"
    log_opt: dict[str, str] = {}
    # Additional service lifecycle hooks (P3)
    exec_start_post: str = ""
    exec_stop: str = ""
    # Feature 1: host device passthrough
    devices: list[str] = []
    # Feature 2: OCI runtime (e.g. "crun", "kata", "gvisor")
    runtime: str = ""
    # Feature 3: raw extra [Service] directives (multi-line freeform)
    service_extra: str = ""
    # Feature 5: run an init process as PID 1
    init: bool = False
    # Feature 6: soft memory reservation and cgroup fair-share weights
    memory_reservation: str = ""
    cpu_weight: str = ""
    io_weight: str = ""
    # Feature 15: additional network aliases
    network_aliases: list[str] = []

    @field_validator("image")
    @classmethod
    def validate_image(cls, v: str) -> str:
        v = _no_control_chars(v, "image")
        if not v:
            raise ValueError("image is required")
        if not _IMAGE_RE.match(v) or len(v) > 255:
            raise ValueError(
                "image must be a valid container image reference "
                "(registry/name:tag format, max 255 chars)"
            )
        return v

    @field_validator(
        "network",
        "restart_policy",
        "exec_start_pre",
        "memory_limit",
        "cpu_quota",
        "apparmor_profile",
        "build_context",
        "build_file",
        "run_user",
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
        "pod_name",
        "log_driver",
        "exec_start_post",
        "exec_stop",
        "runtime",
        "memory_reservation",
        "cpu_weight",
        "io_weight",
    )
    @classmethod
    def validate_no_control_chars(cls, v: str, info) -> str:
        return _no_control_chars(v, info.field_name)

    @field_validator("service_extra")
    @classmethod
    def validate_service_extra(cls, v: str) -> str:
        """Allow newlines (needed for multi-line config) but reject null bytes and CR."""
        if "\x00" in v or "\r" in v:
            raise ValueError("service_extra must not contain null bytes or carriage returns")
        return v

    @field_validator("environment", "labels", "sysctl", "log_opt")
    @classmethod
    def validate_dict_no_control_chars(cls, v: dict, info) -> dict:
        for k, val in v.items():
            _no_control_chars(k, f"{info.field_name} key")
            _no_control_chars(val, f"{info.field_name} value")
        return v

    # Secrets referenced in the container unit (Secret= key)
    secrets: list[str] = []

    @field_validator(
        "uid_map",
        "gid_map",
        "depends_on",
        "drop_caps",
        "add_caps",
        "mask_paths",
        "unmask_paths",
        "dns",
        "dns_search",
        "dns_option",
        "secrets",
        "devices",
        "network_aliases",
    )
    @classmethod
    def validate_list_no_control_chars(cls, v: list, info) -> list:
        for item in v:
            _no_control_chars(str(item), f"{info.field_name} item")
        return v

    @field_validator("ports")
    @classmethod
    def validate_ports(cls, v: list[str]) -> list[str]:
        # Accepted forms (all optionally suffixed with /tcp or /udp):
        #   port                      e.g. 80
        #   host_port:container_port  e.g. 8080:80
        #   ip:host_port:container_port  e.g. 127.0.0.1:8080:80
        #   :container_port           e.g. :80  (OS picks host port)
        _PORT = r"\d{1,5}"
        _IP = r"[\d.:]+"  # IPv4 or IPv6
        _PROTO = r"(/tcp|/udp)?"
        pattern = re.compile(
            rf"^({_IP}:)?{_PORT}?:{_PORT}{_PROTO}$"
            rf"|^{_PORT}{_PROTO}$"
        )
        for port in v:
            if not pattern.match(port):
                raise ValueError(f"Invalid port mapping: {port!r}")
        return v


class ContainerUpdate(ContainerCreate):
    pass


class Container(ContainerCreate):
    id: str
    compartment_id: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Container":
        d = dict(row)
        d["environment"] = json.loads(d["environment"])
        d["ports"] = json.loads(d["ports"])
        d["volumes"] = json.loads(d["volumes"])
        d["labels"] = json.loads(d["labels"])
        d["depends_on"] = json.loads(d["depends_on"])
        d["bind_mounts"] = json.loads(d.get("bind_mounts") or "[]")
        d["uid_map"] = json.loads(d.get("uid_map") or "[]")
        d["gid_map"] = json.loads(d.get("gid_map") or "[]")
        # Boolean fields stored as INTEGER in SQLite
        d.setdefault("notify_healthy", 0)
        d.setdefault("no_new_privileges", 0)
        d.setdefault("read_only", 0)
        d.setdefault("privileged", 0)
        # String fields added in migration 010 (absent in old rows)
        for _f in (
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
        ):
            d.setdefault(_f, "")
        # JSON list/dict fields added in migration 011
        for _f in (
            "drop_caps",
            "add_caps",
            "mask_paths",
            "unmask_paths",
            "dns",
            "dns_search",
            "dns_option",
        ):
            d[_f] = json.loads(d.get(_f) or "[]")
        d["sysctl"] = json.loads(d.get("sysctl") or "{}")
        # String fields added in migration 012
        for _f in ("pod_name", "log_driver", "exec_start_post", "exec_stop"):
            d.setdefault(_f, "")
        d["log_opt"] = json.loads(d.get("log_opt") or "{}")
        # JSON list added in migration 002
        d["secrets"] = json.loads(d.get("secrets") or "[]")
        # Fields added in migration 003
        d["devices"] = json.loads(d.get("devices") or "[]")
        d["network_aliases"] = json.loads(d.get("network_aliases") or "[]")
        for _f in ("runtime", "service_extra", "memory_reservation", "cpu_weight", "io_weight"):
            d.setdefault(_f, "")
        d.setdefault("init", 0)
        return cls(**d)


class PodCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$")
    network: str = ""  # empty = use service default network
    publish_ports: list[str] = []

    @field_validator("network")
    @classmethod
    def validate_no_control_chars(cls, v: str, info) -> str:
        return _no_control_chars(v, info.field_name)

    @field_validator("publish_ports")
    @classmethod
    def validate_ports(cls, v: list[str]) -> list[str]:
        _PORT = r"\d{1,5}"
        _IP = r"[\d.:]+"
        _PROTO = r"(/tcp|/udp)?"
        pattern = re.compile(
            rf"^({_IP}:)?{_PORT}?:{_PORT}{_PROTO}$"
            rf"|^{_PORT}{_PROTO}$"
        )
        for port in v:
            if not pattern.match(port):
                raise ValueError(f"Invalid port mapping: {port!r}")
        return v


class Pod(PodCreate):
    id: str
    compartment_id: str
    created_at: str

    @classmethod
    def from_row(cls, row) -> "Pod":
        d = dict(row)
        d["publish_ports"] = json.loads(d.get("publish_ports") or "[]")
        return cls(**d)


class ImageUnitCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$")
    image: str
    auth_file: str = ""
    pull_policy: str = ""  # "always" | "missing" | "never" | "newer"

    @field_validator("image")
    @classmethod
    def validate_image(cls, v: str) -> str:
        v = _no_control_chars(v, "image")
        if v and (not _IMAGE_RE.match(v) or len(v) > 255):
            raise ValueError(
                "image must be a valid container image reference "
                "(registry/name:tag format, max 255 chars)"
            )
        return v

    @field_validator("auth_file", "pull_policy")
    @classmethod
    def validate_no_control_chars(cls, v: str, info) -> str:
        return _no_control_chars(v, info.field_name)


class ImageUnit(ImageUnitCreate):
    id: str
    compartment_id: str
    created_at: str

    @classmethod
    def from_row(cls, row) -> "ImageUnit":
        return cls(**dict(row))


class CompartmentCreate(BaseModel):
    id: str = Field(..., description="Slug used as compartment ID and user suffix")
    description: str = ""

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                "Compartment ID must be 1-32 lowercase alphanumeric chars and hyphens, "
                "start and end with alphanumeric"
            )
        if v.startswith("qm-"):
            raise ValueError("Compartment ID must not start with 'qm-'")
        return v


class CompartmentUpdate(BaseModel):
    description: str | None = None


class CompartmentNetworkUpdate(BaseModel):
    """Configures the optional shared Podman network unit for a compartment."""

    net_driver: str = ""  # bridge | macvlan | ipvlan (empty = Podman default)
    net_subnet: str = ""  # CIDR, e.g. 10.89.1.0/24
    net_gateway: str = ""  # gateway IP within subnet
    net_ipv6: bool = False
    net_internal: bool = False  # isolate from external routing
    net_dns_enabled: bool = False  # enable Podman DNS plugin (name resolution)

    @field_validator("net_driver", "net_subnet", "net_gateway")
    @classmethod
    def validate_no_control_chars(cls, v: str, info) -> str:
        return _no_control_chars(v, info.field_name)


class Compartment(BaseModel):
    id: str
    description: str
    linux_user: str
    created_at: str
    updated_at: str
    containers: list[Container] = []
    volumes: list[Volume] = []
    pods: list["Pod"] = []
    image_units: list["ImageUnit"] = []
    # Shared network unit configuration (P2)
    net_driver: str = ""
    net_subnet: str = ""
    net_gateway: str = ""
    net_ipv6: bool = False
    net_internal: bool = False
    net_dns_enabled: bool = False

    @classmethod
    def from_row(cls, row: Any) -> "Compartment":
        d = dict(row)
        # Boolean fields stored as INTEGER in SQLite (added in migration 011)
        d.setdefault("net_ipv6", 0)
        d.setdefault("net_internal", 0)
        d.setdefault("net_dns_enabled", 0)
        # String fields added in migration 011
        for _f in ("net_driver", "net_subnet", "net_gateway"):
            d.setdefault(_f, "")
        return cls(**d, containers=[], volumes=[], pods=[], image_units=[])


class CompartmentStatus(BaseModel):
    compartment_id: str
    containers: list[dict[str, str]] = []


class SystemEvent(BaseModel):
    id: int
    compartment_id: str | None
    container_id: str | None
    event_type: str
    message: str
    created_at: str


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

_SECRET_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


class SecretCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = _no_control_chars(v, "name")
        if not _SECRET_NAME_RE.match(v) or len(v) > 253:
            raise ValueError(
                "Secret name must start with alphanumeric and contain only "
                "alphanumeric, dot, underscore, or hyphen (max 253 chars)"
            )
        return v


class Secret(SecretCreate):
    id: str
    compartment_id: str
    created_at: str

    @classmethod
    def from_row(cls, row) -> "Secret":
        return cls(**dict(row))


# ---------------------------------------------------------------------------
# Timers
# ---------------------------------------------------------------------------


class TimerCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$")
    container_id: str
    on_calendar: str = ""
    on_boot_sec: str = ""
    random_delay_sec: str = ""
    persistent: bool = False
    enabled: bool = True

    @field_validator("on_calendar", "on_boot_sec", "random_delay_sec")
    @classmethod
    def validate_no_control_chars(cls, v: str, info) -> str:
        return _no_control_chars(v, info.field_name)


class Timer(TimerCreate):
    id: str
    compartment_id: str
    container_name: str = ""  # populated by service layer
    created_at: str

    @classmethod
    def from_row(cls, row) -> "Timer":
        d = dict(row)
        d.setdefault("container_name", "")
        return cls(**d)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class TemplateCreate(BaseModel):
    name: str
    description: str = ""
    source_compartment_id: str

    @field_validator("name", "description")
    @classmethod
    def validate_no_control_chars(cls, v: str, info) -> str:
        return _no_control_chars(v, info.field_name)

    @field_validator("source_compartment_id")
    @classmethod
    def validate_source_id(cls, v: str) -> str:
        return _no_control_chars(v, "source_compartment_id")


class Template(BaseModel):
    id: str
    name: str
    description: str
    config_json: str
    created_at: str

    @classmethod
    def from_row(cls, row) -> "Template":
        return cls(**dict(row))


class TemplateInstantiate(BaseModel):
    """Body for POST /api/compartments/from-template/{template_id}."""

    compartment_id: str = Field(..., description="New compartment ID (slug)")
    description: str = ""

    @field_validator("compartment_id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        _SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$|^[a-z0-9]$")
        if not _SLUG_RE.match(v):
            raise ValueError(
                "Compartment ID must be 1-32 lowercase alphanumeric chars and hyphens, "
                "start and end with alphanumeric"
            )
        if v.startswith("qm-"):
            raise ValueError("Compartment ID must not start with 'qm-'")
        return v


# ---------------------------------------------------------------------------
# Notification hooks
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"^https?://\S+$")


class NotificationHookCreate(BaseModel):
    container_name: str = ""  # empty = any container in compartment
    event_type: str = "on_failure"  # on_failure | on_restart
    webhook_url: str
    webhook_secret: str = ""
    enabled: bool = True

    @field_validator("container_name", "webhook_secret")
    @classmethod
    def validate_no_control_chars(cls, v: str, info) -> str:
        return _no_control_chars(v, info.field_name)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        if v not in ("on_failure", "on_restart", "on_start", "on_stop"):
            raise ValueError(
                "event_type must be 'on_failure', 'on_restart', 'on_start', or 'on_stop'"
            )
        return v

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, v: str) -> str:
        v = _no_control_chars(v, "webhook_url")
        if not _URL_RE.match(v) or len(v) > 2048:
            raise ValueError("webhook_url must be a valid http:// or https:// URL")
        return v


class NotificationHook(NotificationHookCreate):
    id: str
    compartment_id: str
    created_at: str

    @classmethod
    def from_row(cls, row) -> "NotificationHook":
        d = dict(row)
        return cls(**d)

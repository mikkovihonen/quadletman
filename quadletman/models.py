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


# ---------------------------------------------------------------------------
# Volume models
# ---------------------------------------------------------------------------


class VolumeCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$")
    selinux_context: str = Field(default="container_file_t", pattern=r"^[a-zA-Z0-9_]+$")
    owner_uid: int = Field(default=0, ge=0)
    """Container UID that should own this volume directory.

    0 (default) = service user (host UID).  Any other value N causes the directory
    to be owned by the helper user qm-{service_id}-N (host UID = subuid_start + N),
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
    service_id: str
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


# ---------------------------------------------------------------------------
# Container models
# ---------------------------------------------------------------------------


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
    )
    @classmethod
    def validate_no_control_chars(cls, v: str, info) -> str:
        return _no_control_chars(v, info.field_name)

    @field_validator("environment", "labels", "sysctl", "log_opt")
    @classmethod
    def validate_dict_no_control_chars(cls, v: dict, info) -> dict:
        for k, val in v.items():
            _no_control_chars(k, f"{info.field_name} key")
            _no_control_chars(val, f"{info.field_name} value")
        return v

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
    service_id: str
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
        return cls(**d)


# ---------------------------------------------------------------------------
# Pod models (P2)
# ---------------------------------------------------------------------------


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
    service_id: str
    created_at: str

    @classmethod
    def from_row(cls, row) -> "Pod":
        d = dict(row)
        d["publish_ports"] = json.loads(d.get("publish_ports") or "[]")
        return cls(**d)


# ---------------------------------------------------------------------------
# Image unit models (P2)
# ---------------------------------------------------------------------------


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
    service_id: str
    created_at: str

    @classmethod
    def from_row(cls, row) -> "ImageUnit":
        return cls(**dict(row))


# ---------------------------------------------------------------------------
# Service models
# ---------------------------------------------------------------------------


class ServiceCreate(BaseModel):
    id: str = Field(..., description="Slug used as service ID and user suffix")
    description: str = ""

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                "Service ID must be 1-32 lowercase alphanumeric chars and hyphens, "
                "start and end with alphanumeric"
            )
        if v.startswith("qm-"):
            raise ValueError("Service ID must not start with 'qm-'")
        return v


class ServiceUpdate(BaseModel):
    description: str | None = None


class ServiceNetworkUpdate(BaseModel):
    """Configures the optional shared Podman network unit for a service."""

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


class Service(BaseModel):
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
    def from_row(cls, row: Any) -> "Service":
        d = dict(row)
        # Boolean fields stored as INTEGER in SQLite (added in migration 011)
        d.setdefault("net_ipv6", 0)
        d.setdefault("net_internal", 0)
        d.setdefault("net_dns_enabled", 0)
        # String fields added in migration 011
        for _f in ("net_driver", "net_subnet", "net_gateway"):
            d.setdefault(_f, "")
        return cls(**d, containers=[], volumes=[], pods=[], image_units=[])


class ServiceStatus(BaseModel):
    service_id: str
    containers: list[dict[str, str]] = []


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


class SystemEvent(BaseModel):
    id: int
    service_id: str | None
    container_id: str | None
    event_type: str
    message: str
    created_at: str

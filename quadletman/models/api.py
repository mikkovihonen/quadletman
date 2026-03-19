import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .sanitized import (
    SafeAbsPath,
    SafeImageRef,
    SafeMultilineStr,
    SafePortMapping,
    SafeResourceName,
    SafeSecretName,
    SafeSELinuxContext,
    SafeSlug,
    SafeStr,
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

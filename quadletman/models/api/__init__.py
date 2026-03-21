import json as _json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..sanitized import (
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
from ..version_span import VersionSpan

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
    vol_driver: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 4, 0),
            quadlet_key="Driver",
            value_constraints={"image": (5, 0, 0)},
        ),
    ] = SafeStr.trusted("", "default")  # e.g. "local", "overlay"
    vol_device: SafeStr = SafeStr.trusted("", "default")  # device path for local driver
    vol_options: SafeStr = SafeStr.trusted("", "default")  # mount options string
    vol_copy: bool = True  # Copy=true/false (default true — copy image data on first use)
    vol_group: SafeStr = SafeStr.trusted("", "default")  # optional GID for volume group ownership
    # Podman 4.4.0 (base volume fields — gated by QUADLET feature flag)
    vol_gid: SafeStr = SafeStr.trusted("", "default")
    vol_uid: SafeStr = SafeStr.trusted("", "default")
    vol_user: SafeStr = SafeStr.trusted("", "default")
    vol_image: SafeStr = SafeStr.trusted("", "default")
    vol_label: dict[SafeStr, SafeStr] = {}
    vol_type: SafeStr = SafeStr.trusted("", "default")
    # Podman 5.0.0
    vol_containers_conf_module: Annotated[
        SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule")
    ] = SafeStr.trusted("", "default")
    vol_global_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="GlobalArgs")
    ] = []
    vol_podman_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="PodmanArgs")
    ] = []
    # Podman 5.3.0
    service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")


@enforce_model
class VolumeUpdate(BaseModel):
    owner_uid: int = 0


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
    apparmor_profile: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 8, 0),
            quadlet_key="AppArmor",
        ),
    ] = SafeStr.trusted("", "default")
    build_context: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="SetWorkingDirectory",
        ),
    ] = SafeStr.trusted("", "default")
    build_file: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="File",
        ),
    ] = SafeStr.trusted("", "default")
    containerfile_content: Annotated[
        SafeMultilineStr,
        VersionSpan(
            introduced=(5, 2, 0),
        ),
    ] = SafeMultilineStr.trusted("", "default")
    bind_mounts: list[BindMount] = []
    run_user: SafeStr = SafeStr.trusted("", "default")
    user_ns: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="UserNS",
        ),
    ] = SafeStr.trusted("", "default")  # kept for DB compat, superseded by uid_map/gid_map
    uid_map: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 8, 0),
            quadlet_key="UIDMap",
        ),
    ] = []
    gid_map: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 8, 0),
            quadlet_key="GIDMap",
        ),
    ] = []
    # Health checks
    health_cmd: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthCmd",
        ),
    ] = SafeStr.trusted("", "default")
    health_interval: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthInterval",
        ),
    ] = SafeStr.trusted("", "default")
    health_timeout: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthTimeout",
        ),
    ] = SafeStr.trusted("", "default")
    health_retries: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthRetries",
        ),
    ] = SafeStr.trusted("", "default")
    health_start_period: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthStartPeriod",
        ),
    ] = SafeStr.trusted("", "default")
    health_on_failure: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthOnFailure",
        ),
    ] = SafeStr.trusted("", "default")  # none | kill | restart | stop
    notify_healthy: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="Notify",
        ),
    ] = False
    # Image auto-update
    auto_update: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="AutoUpdate",
        ),
    ] = SafeStr.trusted("", "default")  # registry | local
    # Environment file
    environment_file: SafeStr = SafeStr.trusted("", "default")
    # Command/entrypoint overrides
    exec_cmd: SafeStr = SafeStr.trusted("", "default")
    entrypoint: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="Entrypoint",
        ),
    ] = SafeStr.trusted("", "default")
    # Security options
    no_new_privileges: bool = False
    read_only: bool = False
    privileged: bool = False
    drop_caps: list[SafeStr] = []
    add_caps: list[SafeStr] = []
    seccomp_profile: SafeStr = SafeStr.trusted("", "default")
    mask_paths: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="Mask",
        ),
    ] = []
    unmask_paths: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="Unmask",
        ),
    ] = []
    sysctl: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="Sysctl",
        ),
    ] = {}
    # Runtime
    working_dir: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="WorkingDir",
        ),
    ] = SafeStr.trusted("", "default")
    # Networking
    hostname: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="HostName",
        ),
    ] = SafeStr.trusted("", "default")
    dns: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="DNS",
        ),
    ] = []
    dns_search: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="DNSSearch",
        ),
    ] = []
    dns_option: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="DNSOption",
        ),
    ] = []
    # Pod assignment (P2)
    pod_name: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="Pod",
        ),
    ] = SafeStr.trusted("", "default")
    # Logging (P3)
    log_driver: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="LogDriver",
        ),
    ] = SafeStr.trusted("", "default")  # e.g. "journald", "json-file", "none"
    log_opt: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="LogOpt",
        ),
    ] = {}
    # Additional service lifecycle hooks (P3)
    exec_start_post: SafeStr = SafeStr.trusted("", "default")
    exec_stop: SafeStr = SafeStr.trusted("", "default")
    # Feature 1: host device passthrough
    devices: list[SafeStr] = []
    # Feature 2: OCI runtime (e.g. "crun", "kata", "gvisor")
    runtime: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="PodmanArgs",
        ),
    ] = SafeStr.trusted("", "default")
    # Feature 3: raw extra [Service] directives (multi-line freeform)
    service_extra: SafeMultilineStr = SafeMultilineStr.trusted("", "default")
    # Feature 5: run an init process as PID 1
    init: Annotated[
        bool,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="PodmanArgs",
        ),
    ] = False
    # Feature 6: soft memory reservation and cgroup fair-share weights
    memory_reservation: SafeStr = SafeStr.trusted("", "default")
    cpu_weight: SafeStr = SafeStr.trusted("", "default")
    io_weight: SafeStr = SafeStr.trusted("", "default")
    # Feature 15: additional network aliases
    network_aliases: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="NetworkAlias",
        ),
    ] = []

    # Secrets referenced in the container unit (Secret= key)
    secrets: Annotated[
        list[SafeSecretName],
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="Secret",
        ),
    ] = []

    # ------------------------------------------------------------------
    # Podman 4.4.0 (base Quadlet keys — no VersionSpan needed)
    # ------------------------------------------------------------------
    annotation: list[SafeStr] = []
    expose_host_port: list[SafeStr] = []
    group: SafeStr = SafeStr.trusted("", "default")  # GID for container process
    security_label_disable: bool = False
    security_label_file_type: SafeStr = SafeStr.trusted("", "default")
    security_label_level: SafeStr = SafeStr.trusted("", "default")
    security_label_type: SafeStr = SafeStr.trusted("", "default")

    # ------------------------------------------------------------------
    # Podman 4.5.0
    # ------------------------------------------------------------------
    tmpfs: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="Tmpfs",
        ),
    ] = []
    ip: Annotated[
        SafeIpAddress,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="IP",
        ),
    ] = SafeIpAddress.trusted("", "default")
    ip6: Annotated[
        SafeIpAddress,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="IP6",
        ),
    ] = SafeIpAddress.trusted("", "default")
    mount: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="Mount",
        ),
    ] = []
    rootfs: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="Rootfs",
        ),
    ] = SafeStr.trusted("", "default")

    # ------------------------------------------------------------------
    # Podman 4.6.0
    # ------------------------------------------------------------------
    pull: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="Pull",
        ),
    ] = SafeStr.trusted("", "default")
    security_label_nested: Annotated[
        bool,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="SecurityLabelNested",
        ),
    ] = False

    # ------------------------------------------------------------------
    # Podman 4.7.0
    # ------------------------------------------------------------------
    pids_limit: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="PidsLimit",
        ),
    ] = SafeStr.trusted("", "default")
    ulimits: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="Ulimit",
        ),
    ] = []
    shm_size: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="ShmSize",
        ),
    ] = SafeStr.trusted("", "default")

    # ------------------------------------------------------------------
    # Podman 4.8.0
    # ------------------------------------------------------------------
    read_only_tmpfs: Annotated[
        bool,
        VersionSpan(
            introduced=(4, 8, 0),
            quadlet_key="ReadOnlyTmpfs",
        ),
    ] = False
    sub_uid_map: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 8, 0),
            quadlet_key="SubUIDMap",
        ),
    ] = SafeStr.trusted("", "default")
    sub_gid_map: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 8, 0),
            quadlet_key="SubGIDMap",
        ),
    ] = SafeStr.trusted("", "default")

    # ------------------------------------------------------------------
    # Podman 5.0.0
    # ------------------------------------------------------------------
    containers_conf_module: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="ContainersConfModule",
        ),
    ] = SafeStr.trusted("", "default")
    global_args: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="GlobalArgs",
        ),
    ] = []
    stop_timeout: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="StopTimeout",
        ),
    ] = SafeStr.trusted("", "default")
    run_init: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="RunInit",
        ),
    ] = False

    # ------------------------------------------------------------------
    # Podman 5.1.0
    # ------------------------------------------------------------------
    group_add: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 1, 0),
            quadlet_key="GroupAdd",
        ),
    ] = []

    # ------------------------------------------------------------------
    # Podman 5.2.0
    # ------------------------------------------------------------------
    stop_signal: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="StopSignal",
        ),
    ] = SafeStr.trusted("", "default")

    # ------------------------------------------------------------------
    # Podman 5.3.0
    # ------------------------------------------------------------------
    service_name: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="ServiceName",
        ),
    ] = SafeStr.trusted("", "default")
    default_dependencies: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="DefaultDependencies",
        ),
    ] = True
    add_host: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="AddHost",
        ),
    ] = []
    cgroups_mode: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="CgroupsMode",
        ),
    ] = SafeStr.trusted("", "default")
    start_with_pod: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="StartWithPod",
        ),
    ] = False
    timezone: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="Timezone",
        ),
    ] = SafeStr.trusted("", "default")

    # ------------------------------------------------------------------
    # Podman 5.5.0
    # ------------------------------------------------------------------
    environment_host: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="EnvironmentHost",
        ),
    ] = False
    memory: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="Memory",
        ),
    ] = SafeStr.trusted("", "default")
    reload_cmd: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="ReloadCmd",
        ),
    ] = SafeStr.trusted("", "default")
    reload_signal: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="ReloadSignal",
        ),
    ] = SafeStr.trusted("", "default")
    retry: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="Retry",
        ),
    ] = SafeStr.trusted("", "default")
    retry_delay: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="RetryDelay",
        ),
    ] = SafeStr.trusted("", "default")
    health_log_destination: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthLogDestination",
        ),
    ] = SafeStr.trusted("", "default")
    health_max_log_count: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthMaxLogCount",
        ),
    ] = SafeStr.trusted("", "default")
    health_max_log_size: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthMaxLogSize",
        ),
    ] = SafeStr.trusted("", "default")
    health_startup_cmd: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupCmd",
        ),
    ] = SafeStr.trusted("", "default")
    health_startup_interval: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupInterval",
        ),
    ] = SafeStr.trusted("", "default")
    health_startup_retries: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupRetries",
        ),
    ] = SafeStr.trusted("", "default")
    health_startup_success: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupSuccess",
        ),
    ] = SafeStr.trusted("", "default")
    health_startup_timeout: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupTimeout",
        ),
    ] = SafeStr.trusted("", "default")

    # ------------------------------------------------------------------
    # Podman 5.7.0
    # ------------------------------------------------------------------
    http_proxy: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 7, 0),
            quadlet_key="HttpProxy",
        ),
    ] = False

    # ------------------------------------------------------------------
    # Build-related fields (gated by BUILD_UNITS at route level)
    # ------------------------------------------------------------------

    # Podman 5.2.0
    build_annotation: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="Annotation",
        ),
    ] = []
    build_arch: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="Arch",
        ),
    ] = SafeStr.trusted("", "default")
    build_auth_file: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="AuthFile",
        ),
    ] = SafeStr.trusted("", "default")
    build_containers_conf_module: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="ContainersConfModule",
        ),
    ] = SafeStr.trusted("", "default")
    build_dns: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="DNS",
        ),
    ] = []
    build_dns_option: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="DNSOption",
        ),
    ] = []
    build_dns_search: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="DNSSearch",
        ),
    ] = []
    build_env: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="Environment",
        ),
    ] = {}
    build_force_rm: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="ForceRM",
        ),
    ] = False
    build_global_args: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="GlobalArgs",
        ),
    ] = []
    build_group_add: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="GroupAdd",
        ),
    ] = []
    build_label: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="Label",
        ),
    ] = {}
    build_network: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="Network",
        ),
    ] = SafeStr.trusted("", "default")
    build_podman_args: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="PodmanArgs",
        ),
    ] = []
    build_pull: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="Pull",
        ),
    ] = SafeStr.trusted("", "default")
    build_secret: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="Secret",
        ),
    ] = []
    build_service_name: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="ServiceName",
        ),
    ] = SafeStr.trusted("", "default")
    build_target: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="Target",
        ),
    ] = SafeStr.trusted("", "default")
    build_tls_verify: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="TLSVerify",
        ),
    ] = True
    build_variant: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="Variant",
        ),
    ] = SafeStr.trusted("", "default")
    build_volume: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="Volume",
        ),
    ] = []

    # Podman 5.5.0
    build_retry: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="Retry",
        ),
    ] = SafeStr.trusted("", "default")
    build_retry_delay: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="RetryDelay",
        ),
    ] = SafeStr.trusted("", "default")

    # Podman 5.7.0
    build_args: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(
            introduced=(5, 7, 0),
            quadlet_key="BuildArg",
        ),
    ] = {}
    build_ignore_file: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 7, 0),
            quadlet_key="IgnoreFile",
        ),
    ] = SafeStr.trusted("", "default")


@enforce_model
class ContainerUpdate(ContainerCreate):
    pass


@enforce_model
class PodCreate(BaseModel):
    name: SafeResourceName
    network: SafeStr = SafeStr.trusted("", "default")  # empty = use service default network
    publish_ports: list[SafePortMapping] = []
    # Podman 5.0.0 (base pod fields — gated by POD_UNITS feature flag)
    containers_conf_module: SafeStr = SafeStr.trusted("", "default")
    global_args: list[SafeStr] = []
    podman_args: list[SafeStr] = []
    volumes: list[SafeStr] = []
    # Podman 5.3.0
    service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")
    dns: Annotated[list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="DNS")] = []
    dns_search: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="DNSSearch")
    ] = []
    dns_option: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="DNSOption")
    ] = []
    ip: Annotated[SafeIpAddress, VersionSpan(introduced=(5, 3, 0), quadlet_key="IP")] = (
        SafeIpAddress.trusted("", "default")
    )
    ip6: Annotated[SafeIpAddress, VersionSpan(introduced=(5, 3, 0), quadlet_key="IP6")] = (
        SafeIpAddress.trusted("", "default")
    )
    user_ns: Annotated[SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="UserNS")] = (
        SafeStr.trusted("", "default")
    )
    add_host: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="AddHost")
    ] = []
    uid_map: Annotated[list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="UIDMap")] = []
    gid_map: Annotated[list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="GIDMap")] = []
    sub_uid_map: Annotated[SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="SubUIDMap")] = (
        SafeStr.trusted("", "default")
    )
    sub_gid_map: Annotated[SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="SubGIDMap")] = (
        SafeStr.trusted("", "default")
    )
    network_aliases: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="NetworkAlias")
    ] = []
    # Podman 5.4.0
    shm_size: Annotated[SafeStr, VersionSpan(introduced=(5, 4, 0), quadlet_key="ShmSize")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 5.5.0
    hostname: Annotated[SafeStr, VersionSpan(introduced=(5, 5, 0), quadlet_key="HostName")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 5.6.0
    labels: Annotated[
        dict[SafeStr, SafeStr], VersionSpan(introduced=(5, 6, 0), quadlet_key="Label")
    ] = {}
    exit_policy: Annotated[SafeStr, VersionSpan(introduced=(5, 6, 0), quadlet_key="ExitPolicy")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 5.7.0
    stop_timeout: Annotated[
        SafeStr, VersionSpan(introduced=(5, 7, 0), quadlet_key="StopTimeout")
    ] = SafeStr.trusted("", "default")


@enforce_model
class ImageUnitCreate(BaseModel):
    name: SafeResourceName
    image: SafeImageRef | Literal[""] = SafeStr.trusted("", "default")
    auth_file: SafeStr = SafeStr.trusted("", "default")
    pull_policy: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="PullPolicy",
        ),
    ] = SafeStr.trusted("", "default")  # "always" | "missing" | "never" | "newer"
    # Podman 4.8.0 (base image unit fields — gated by IMAGE_UNITS feature flag)
    all_tags: bool = False
    arch: SafeStr = SafeStr.trusted("", "default")
    cert_dir: SafeStr = SafeStr.trusted("", "default")
    creds: SafeStr = SafeStr.trusted("", "default")
    decryption_key: SafeStr = SafeStr.trusted("", "default")
    os: SafeStr = SafeStr.trusted("", "default")
    tls_verify: bool = True
    variant: SafeStr = SafeStr.trusted("", "default")
    # Podman 5.0.0
    containers_conf_module: Annotated[
        SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule")
    ] = SafeStr.trusted("", "default")
    global_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="GlobalArgs")
    ] = []
    podman_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="PodmanArgs")
    ] = []
    # Podman 5.3.0
    service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")
    image_tags: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="ImageTag")
    ] = []
    # Podman 5.5.0
    retry: Annotated[SafeStr, VersionSpan(introduced=(5, 5, 0), quadlet_key="Retry")] = (
        SafeStr.trusted("", "default")
    )
    retry_delay: Annotated[SafeStr, VersionSpan(introduced=(5, 5, 0), quadlet_key="RetryDelay")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 5.6.0
    policy: Annotated[SafeStr, VersionSpan(introduced=(5, 6, 0), quadlet_key="Policy")] = (
        SafeStr.trusted("", "default")
    )

    @field_validator("image")
    @classmethod
    def validate_image(cls, v: str) -> SafeImageRef | Literal[""]:
        if not v:
            return v
        return SafeImageRef.of(v, "image")


@enforce_model
class KubeCreate(BaseModel):
    """Create a .kube Quadlet unit for Kubernetes YAML deployment."""

    name: SafeResourceName
    yaml_content: SafeMultilineStr
    # Podman 4.4.0 (base kube fields — gated by KUBE_UNITS feature flag)
    config_map: list[SafeStr] = []
    network: SafeStr = SafeStr.trusted("", "default")
    publish_ports: list[SafePortMapping] = []
    # Podman 4.5.0
    log_driver: Annotated[SafeStr, VersionSpan(introduced=(4, 5, 0), quadlet_key="LogDriver")] = (
        SafeStr.trusted("", "default")
    )
    user_ns: Annotated[SafeStr, VersionSpan(introduced=(4, 5, 0), quadlet_key="UserNS")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 4.7.0
    auto_update: Annotated[SafeStr, VersionSpan(introduced=(4, 7, 0), quadlet_key="AutoUpdate")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 4.8.0
    exit_code_propagation: Annotated[
        SafeStr, VersionSpan(introduced=(4, 8, 0), quadlet_key="ExitCodePropagation")
    ] = SafeStr.trusted("", "default")
    # Podman 5.0.0
    containers_conf_module: Annotated[
        SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule")
    ] = SafeStr.trusted("", "default")
    global_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="GlobalArgs")
    ] = []
    podman_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="PodmanArgs")
    ] = []
    kube_down_force: Annotated[
        bool, VersionSpan(introduced=(5, 0, 0), quadlet_key="KubeDownForce")
    ] = False
    # Podman 5.2.0
    set_working_directory: Annotated[
        SafeStr, VersionSpan(introduced=(5, 2, 0), quadlet_key="SetWorkingDirectory")
    ] = SafeStr.trusted("", "default")
    # Podman 5.3.0
    service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")


@enforce_model
class ArtifactCreate(BaseModel):
    """Create a .artifact Quadlet unit for OCI artifact management (Podman 5.7.0+)."""

    name: SafeResourceName
    image: SafeImageRef
    # Podman 5.7.0 (base artifact fields — gated by ARTIFACT_UNITS feature flag)
    digest: SafeStr = SafeStr.trusted("", "default")
    service_name: SafeStr = SafeStr.trusted("", "default")


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
    net_dns_enabled: Annotated[bool, VersionSpan(introduced=(4, 7, 0), quadlet_key="DNS")] = False
    # Podman 4.4.0 (base network fields — gated by QUADLET feature flag)
    net_disable_dns: bool = False
    net_ip_range: SafeStr = SafeStr.trusted("", "default")
    net_label: dict[SafeStr, SafeStr] = {}
    net_options: SafeStr = SafeStr.trusted("", "default")
    # Podman 5.0.0
    net_containers_conf_module: Annotated[
        SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule")
    ] = SafeStr.trusted("", "default")
    net_global_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="GlobalArgs")
    ] = []
    net_podman_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="PodmanArgs")
    ] = []
    net_ipam_driver: Annotated[
        SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="IPAMDriver")
    ] = SafeStr.trusted("", "default")
    net_dns: Annotated[SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="DNS")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 5.3.0
    net_service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")
    # Podman 5.5.0
    net_delete_on_stop: Annotated[
        bool, VersionSpan(introduced=(5, 5, 0), quadlet_key="NetworkDeleteOnStop")
    ] = False
    # Podman 5.6.0
    net_interface_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 6, 0), quadlet_key="InterfaceName")
    ] = SafeStr.trusted("", "default")


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


@enforce_model
class HostSettingUpdate(BaseModel):
    key: SafeStr
    value: SafeStr


@enforce_model
class SELinuxBooleanUpdate(BaseModel):
    name: SafeStr
    enabled: bool


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
        d.setdefault("vol_gid", "")
        d.setdefault("vol_uid", "")
        d.setdefault("vol_user", "")
        d.setdefault("vol_image", "")
        d.setdefault("vol_type", "")
        d.setdefault("vol_containers_conf_module", "")
        d.setdefault("service_name", "")
        _loads(d, "vol_label", "vol_global_args", "vol_podman_args")
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
            # New list/dict fields
            "annotation",
            "expose_host_port",
            "tmpfs",
            "mount",
            "ulimits",
            "global_args",
            "group_add",
            "add_host",
            "build_annotation",
            "build_dns",
            "build_dns_option",
            "build_dns_search",
            "build_env",
            "build_global_args",
            "build_group_add",
            "build_label",
            "build_podman_args",
            "build_secret",
            "build_volume",
            "build_args",
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
            # New string fields
            "group",
            "security_label_file_type",
            "security_label_level",
            "security_label_type",
            "ip",
            "ip6",
            "rootfs",
            "pull",
            "pids_limit",
            "shm_size",
            "sub_uid_map",
            "sub_gid_map",
            "containers_conf_module",
            "stop_timeout",
            "stop_signal",
            "service_name",
            "cgroups_mode",
            "timezone",
            "memory",
            "reload_cmd",
            "reload_signal",
            "retry",
            "retry_delay",
            "health_log_destination",
            "health_max_log_count",
            "health_max_log_size",
            "health_startup_cmd",
            "health_startup_interval",
            "health_startup_retries",
            "health_startup_success",
            "health_startup_timeout",
            "build_arch",
            "build_auth_file",
            "build_containers_conf_module",
            "build_network",
            "build_pull",
            "build_service_name",
            "build_target",
            "build_variant",
            "build_retry",
            "build_retry_delay",
            "build_ignore_file",
        ):
            d.setdefault(f, "")
        d.setdefault("notify_healthy", 0)
        d.setdefault("no_new_privileges", 0)
        d.setdefault("read_only", 0)
        d.setdefault("privileged", 0)
        d.setdefault("init", 0)
        d.setdefault("security_label_disable", 0)
        d.setdefault("security_label_nested", 0)
        d.setdefault("read_only_tmpfs", 0)
        d.setdefault("run_init", 0)
        d.setdefault("default_dependencies", 1)
        d.setdefault("start_with_pod", 0)
        d.setdefault("environment_host", 0)
        d.setdefault("http_proxy", 0)
        d.setdefault("build_force_rm", 0)
        d.setdefault("build_tls_verify", 1)
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
        _loads(
            d,
            "publish_ports",
            "global_args",
            "podman_args",
            "volumes",
            "dns",
            "dns_search",
            "dns_option",
            "add_host",
            "uid_map",
            "gid_map",
            "network_aliases",
            "labels",
        )
        for f in (
            "containers_conf_module",
            "service_name",
            "ip",
            "ip6",
            "user_ns",
            "sub_uid_map",
            "sub_gid_map",
            "shm_size",
            "hostname",
            "exit_policy",
            "stop_timeout",
            "network",
        ):
            d.setdefault(f, "")
        return d


@enforce_model
class ImageUnit(ImageUnitCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        _loads(d, "global_args", "podman_args", "image_tags")
        for f in (
            "auth_file",
            "pull_policy",
            "arch",
            "cert_dir",
            "creds",
            "decryption_key",
            "os",
            "variant",
            "containers_conf_module",
            "service_name",
            "retry",
            "retry_delay",
            "policy",
        ):
            d.setdefault(f, "")
        d.setdefault("all_tags", 0)
        d.setdefault("tls_verify", 1)
        return d


@enforce_model
class Kube(KubeCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        _loads(d, "config_map", "publish_ports", "global_args", "podman_args")
        for f in (
            "network",
            "log_driver",
            "user_ns",
            "auto_update",
            "exit_code_propagation",
            "containers_conf_module",
            "set_working_directory",
            "service_name",
        ):
            d.setdefault(f, "")
        d.setdefault("kube_down_force", 0)
        return d


@enforce_model
class Artifact(ArtifactCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for f in ("digest", "service_name"):
            d.setdefault(f, "")
        return d


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

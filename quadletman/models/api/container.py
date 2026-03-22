from typing import Annotated, Literal

from pydantic import BaseModel, field_validator, model_validator

from ..sanitized import (
    SafeAbsPath,
    SafeAutoUpdatePolicy,
    SafeByteSize,
    SafeHealthOnFailure,
    SafeImageRef,
    SafeIntOrEmpty,
    SafeIpAddress,
    SafeLinuxCapability,
    SafeMultilineStr,
    SafePortMapping,
    SafePullPolicy,
    SafeResourceName,
    SafeRestartPolicy,
    SafeSecretName,
    SafeSignalName,
    SafeSlug,
    SafeStr,
    SafeTimeDuration,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)
from ..version_span import VersionSpan, enforce_model_version_gating
from .common import _BIND_MOUNT_DENYLIST, _loads
from .volume import VolumeMount


@enforce_model_safety
class BindMount(BaseModel):
    """An arbitrary host path mounted into a container."""

    host_path: SafeAbsPath | Literal[""]
    container_path: SafeAbsPath | Literal[""]
    options: SafeStr = SafeStr.trusted("", "default")

    @field_validator("host_path", "container_path")
    @classmethod
    def validate_absolute_path(cls, v: str, info) -> SafeAbsPath | Literal[""]:
        if not v:
            return v
        return SafeAbsPath.of(v, info.field_name)

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


@enforce_model_version_gating(
    exempt={
        "name": "identity field — quadletman resource name, not a Quadlet key",
        "image": "container image reference — always required, not version-dependent",
        "sort_order": "quadletman-internal display ordering, not a Podman concept",
        "build_unit_name": "quadletman-internal reference to a build unit, not a Quadlet key",
    }
)
@enforce_model_safety
class ContainerCreate(BaseModel):
    name: SafeResourceName
    image: SafeImageRef
    environment: Annotated[
        dict[SafeStr, SafeStr], VersionSpan(introduced=(4, 4, 0), quadlet_key="Environment")
    ] = {}
    ports: Annotated[
        list[SafePortMapping], VersionSpan(introduced=(4, 4, 0), quadlet_key="PublishPort")
    ] = []
    volumes: Annotated[
        list[VolumeMount], VersionSpan(introduced=(4, 4, 0), quadlet_key="Volume")
    ] = []
    labels: Annotated[
        dict[SafeStr, SafeStr], VersionSpan(introduced=(4, 4, 0), quadlet_key="Label")
    ] = {}
    network: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="Network")] = (
        SafeStr.trusted("host", "default")
    )
    restart_policy: Annotated[
        SafeRestartPolicy, VersionSpan(introduced=(4, 4, 0), quadlet_key="")
    ] = SafeRestartPolicy.trusted("always", "default")
    exec_start_pre: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="")] = (
        SafeStr.trusted("", "default")
    )
    memory_limit: Annotated[SafeByteSize, VersionSpan(introduced=(4, 4, 0), quadlet_key="")] = (
        SafeByteSize.trusted("", "default")
    )
    cpu_quota: Annotated[SafeIntOrEmpty, VersionSpan(introduced=(4, 4, 0), quadlet_key="")] = (
        SafeIntOrEmpty.trusted("", "default")
    )
    depends_on: Annotated[
        list[SafeResourceName], VersionSpan(introduced=(4, 4, 0), quadlet_key="")
    ] = []
    sort_order: int = 0
    apparmor_profile: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 8, 0),
            quadlet_key="AppArmor",
        ),
    ] = SafeStr.trusted("", "default")
    build_unit_name: SafeResourceName | Literal[""] = SafeStr.trusted("", "default")
    bind_mounts: Annotated[
        list[BindMount], VersionSpan(introduced=(4, 4, 0), quadlet_key="Volume")
    ] = []
    run_user: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="User")] = (
        SafeStr.trusted("", "default")
    )
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
        SafeTimeDuration,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthInterval",
        ),
    ] = SafeTimeDuration.trusted("", "default")
    health_timeout: Annotated[
        SafeTimeDuration,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthTimeout",
        ),
    ] = SafeTimeDuration.trusted("", "default")
    health_retries: Annotated[
        SafeIntOrEmpty,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthRetries",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    health_start_period: Annotated[
        SafeTimeDuration,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthStartPeriod",
        ),
    ] = SafeTimeDuration.trusted("", "default")
    health_on_failure: Annotated[
        SafeHealthOnFailure,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthOnFailure",
        ),
    ] = SafeHealthOnFailure.trusted("", "default")
    notify_healthy: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="Notify",
        ),
    ] = False
    # Image auto-update
    auto_update: Annotated[
        SafeAutoUpdatePolicy,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="AutoUpdate",
        ),
    ] = SafeAutoUpdatePolicy.trusted("", "default")
    # Environment file
    environment_file: Annotated[
        SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="EnvironmentFile")
    ] = SafeStr.trusted("", "default")
    # Command/entrypoint overrides
    exec_cmd: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="Exec")] = (
        SafeStr.trusted("", "default")
    )
    entrypoint: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="Entrypoint",
        ),
    ] = SafeStr.trusted("", "default")
    # Security options
    no_new_privileges: Annotated[
        bool, VersionSpan(introduced=(4, 4, 0), quadlet_key="NoNewPrivileges")
    ] = False
    read_only: Annotated[bool, VersionSpan(introduced=(4, 4, 0), quadlet_key="ReadOnly")] = False
    privileged: Annotated[bool, VersionSpan(introduced=(4, 4, 0), quadlet_key="Privileged")] = False
    drop_caps: Annotated[
        list[SafeLinuxCapability], VersionSpan(introduced=(4, 4, 0), quadlet_key="DropCapability")
    ] = []
    add_caps: Annotated[
        list[SafeLinuxCapability], VersionSpan(introduced=(4, 4, 0), quadlet_key="AddCapability")
    ] = []
    seccomp_profile: Annotated[
        SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="SeccompProfile")
    ] = SafeStr.trusted("", "default")
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
        list[SafeIpAddress],
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
    exec_start_post: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="")] = (
        SafeStr.trusted("", "default")
    )
    exec_stop: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="")] = (
        SafeStr.trusted("", "default")
    )
    # Feature 1: host device passthrough
    devices: Annotated[
        list[SafeStr], VersionSpan(introduced=(4, 4, 0), quadlet_key="AddDevice")
    ] = []
    # Feature 2: OCI runtime (e.g. "crun", "kata", "gvisor")
    runtime: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="PodmanArgs",
        ),
    ] = SafeStr.trusted("", "default")
    # Feature 3: raw extra [Service] directives (multi-line freeform)
    service_extra: Annotated[
        SafeMultilineStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="")
    ] = SafeMultilineStr.trusted("", "default")
    # Feature 5: run an init process as PID 1
    init: Annotated[
        bool,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="PodmanArgs",
        ),
    ] = False
    # Feature 6: soft memory reservation and cgroup fair-share weights
    memory_reservation: Annotated[
        SafeByteSize, VersionSpan(introduced=(4, 4, 0), quadlet_key="")
    ] = SafeByteSize.trusted("", "default")
    cpu_weight: Annotated[SafeIntOrEmpty, VersionSpan(introduced=(4, 4, 0), quadlet_key="")] = (
        SafeIntOrEmpty.trusted("", "default")
    )
    io_weight: Annotated[SafeIntOrEmpty, VersionSpan(introduced=(4, 4, 0), quadlet_key="")] = (
        SafeIntOrEmpty.trusted("", "default")
    )
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
    # Podman 4.4.0 (base Quadlet keys)
    # ------------------------------------------------------------------
    annotation: Annotated[
        list[SafeStr], VersionSpan(introduced=(4, 4, 0), quadlet_key="Annotation")
    ] = []
    expose_host_port: Annotated[
        list[SafeStr], VersionSpan(introduced=(4, 4, 0), quadlet_key="ExposeHostPort")
    ] = []
    group: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="Group")] = (
        SafeStr.trusted("", "default")
    )  # GID for container process
    security_label_disable: Annotated[
        bool, VersionSpan(introduced=(4, 4, 0), quadlet_key="SecurityLabelDisable")
    ] = False
    security_label_file_type: Annotated[
        SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="SecurityLabelFileType")
    ] = SafeStr.trusted("", "default")
    security_label_level: Annotated[
        SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="SecurityLabelLevel")
    ] = SafeStr.trusted("", "default")
    security_label_type: Annotated[
        SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="SecurityLabelType")
    ] = SafeStr.trusted("", "default")

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
        SafePullPolicy,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="Pull",
        ),
    ] = SafePullPolicy.trusted("", "default")
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
        SafeIntOrEmpty,
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="PidsLimit",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    ulimits: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="Ulimit",
        ),
    ] = []
    shm_size: Annotated[
        SafeByteSize,
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="ShmSize",
        ),
    ] = SafeByteSize.trusted("", "default")

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
        SafeTimeDuration,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="StopTimeout",
        ),
    ] = SafeTimeDuration.trusted("", "default")
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
        SafeSignalName,
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="StopSignal",
        ),
    ] = SafeSignalName.trusted("", "default")

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
        SafeByteSize,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="Memory",
        ),
    ] = SafeByteSize.trusted("", "default")
    reload_cmd: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="ReloadCmd",
        ),
    ] = SafeStr.trusted("", "default")
    reload_signal: Annotated[
        SafeSignalName,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="ReloadSignal",
        ),
    ] = SafeSignalName.trusted("", "default")
    retry: Annotated[
        SafeIntOrEmpty,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="Retry",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    retry_delay: Annotated[
        SafeTimeDuration,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="RetryDelay",
        ),
    ] = SafeTimeDuration.trusted("", "default")
    health_log_destination: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthLogDestination",
        ),
    ] = SafeStr.trusted("", "default")
    health_max_log_count: Annotated[
        SafeIntOrEmpty,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthMaxLogCount",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    health_max_log_size: Annotated[
        SafeByteSize,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthMaxLogSize",
        ),
    ] = SafeByteSize.trusted("", "default")
    health_startup_cmd: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupCmd",
        ),
    ] = SafeStr.trusted("", "default")
    health_startup_interval: Annotated[
        SafeTimeDuration,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupInterval",
        ),
    ] = SafeTimeDuration.trusted("", "default")
    health_startup_retries: Annotated[
        SafeIntOrEmpty,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupRetries",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    health_startup_success: Annotated[
        SafeIntOrEmpty,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupSuccess",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    health_startup_timeout: Annotated[
        SafeTimeDuration,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupTimeout",
        ),
    ] = SafeTimeDuration.trusted("", "default")

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


@enforce_model_safety
class ContainerUpdate(ContainerCreate):
    pass


@enforce_model_safety
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
        ):
            d.setdefault(f, "")
        d.setdefault("build_unit_name", "")
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
        return d

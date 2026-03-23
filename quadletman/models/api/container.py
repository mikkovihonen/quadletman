from typing import Annotated

from pydantic import BaseModel, field_validator, model_validator

from ..constraints import (
    AUTO_UPDATE_POLICY_CHOICES,
    HEALTH_ON_FAILURE_CHOICES,
    N_,
    RESOURCE_NAME_CN,
    RESTART_POLICY_CHOICES,
    FieldChoices,
    FieldConstraints,
)
from ..sanitized import (
    SafeAbsPathOrEmpty,
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
    SafeResourceNameOrEmpty,
    SafeRestartPolicy,
    SafeSecretName,
    SafeSignalName,
    SafeSlug,
    SafeStr,
    SafeTimeDuration,
    SafeTimestamp,
    SafeUnitName,
    SafeUUID,
    enforce_model_safety,
)
from ..version_span import VersionSpan, enforce_model_version_gating
from .common import _BIND_MOUNT_DENYLIST, _loads, _sanitize_db_row
from .volume import VolumeMount


@enforce_model_safety
class BindMount(BaseModel):
    """An arbitrary host path mounted into a container."""

    host_path: SafeAbsPathOrEmpty
    container_path: SafeAbsPathOrEmpty
    options: SafeStr = SafeStr.trusted("", "default")

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
    name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Unique name for this container"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
        ),
    ]
    image: Annotated[
        SafeImageRef,
        FieldConstraints(
            description=N_("Container image to run"),
            label_hint=N_("e.g. docker.io/library/nginx:latest"),
        ),
    ]
    environment: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Environment"),
        FieldConstraints(
            description=N_("Environment variables passed to the container"),
            label_hint=N_("key=value pairs"),
        ),
    ] = {}
    ports: Annotated[
        list[SafePortMapping],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="PublishPort"),
        FieldConstraints(
            description=N_("Host-to-container port mappings"),
            label_hint=N_("e.g. 8080:80, 443:443/tcp"),
        ),
    ] = []
    volumes: Annotated[
        list[VolumeMount],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Volume"),
        FieldConstraints(description=N_("Managed volumes mounted into the container")),
    ] = []
    labels: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Label"),
        FieldConstraints(
            description=N_("OCI labels attached to the container"),
            label_hint=N_("key=value pairs"),
        ),
    ] = {}
    network: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Network"),
        FieldConstraints(
            description=N_("Network mode for the container"),
            label_hint=N_("e.g. host, none, or compartment name"),
        ),
    ] = SafeStr.trusted("host", "default")
    restart_policy: Annotated[
        SafeRestartPolicy,
        VersionSpan(introduced=(4, 4, 0), quadlet_key=""),
        RESTART_POLICY_CHOICES,
        FieldConstraints(description=N_("When to restart the container after exit")),
    ] = SafeRestartPolicy.trusted("always", "default")
    exec_start_pre: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key=""),
        FieldConstraints(
            description=N_("Command to run before starting the container"),
            label_hint=N_("shell command"),
        ),
    ] = SafeStr.trusted("", "default")
    memory_limit: Annotated[
        SafeByteSize,
        VersionSpan(introduced=(4, 4, 0), quadlet_key=""),
        FieldConstraints(
            description=N_("Maximum memory the container can use"),
            placeholder=N_("512m"),
            label_hint=N_("hard max, e.g. 512m"),
        ),
    ] = SafeByteSize.trusted("", "default")
    cpu_quota: Annotated[
        SafeIntOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key=""),
        FieldConstraints(
            description=N_("CPU time limit as percentage"),
            label_hint=N_("e.g. 50%"),
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    depends_on: Annotated[
        list[SafeResourceName],
        VersionSpan(introduced=(4, 4, 0), quadlet_key=""),
        FieldConstraints(description=N_("Containers that must start before this one")),
    ] = []
    sort_order: int = 0
    apparmor_profile: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 8, 0),
            quadlet_key="AppArmor",
        ),
        FieldConstraints(
            description=N_("AppArmor security profile name"),
            label_hint=N_("profile name"),
        ),
    ] = SafeStr.trusted("", "default")
    build_unit_name: Annotated[
        SafeResourceNameOrEmpty,
        FieldConstraints(
            description=N_("Build unit that produces this container's image"),
            label_hint=N_("build unit name"),
        ),
    ] = SafeResourceNameOrEmpty.trusted("", "default")
    bind_mounts: Annotated[
        list[BindMount],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Volume"),
        FieldConstraints(description=N_("Host paths mounted directly into the container")),
    ] = []
    run_user: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="User"),
        FieldConstraints(
            description=N_("User or UID to run the container process as"),
            label_hint=N_("username or UID"),
        ),
    ] = SafeStr.trusted("", "default")
    user_ns: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="UserNS",
        ),
        FieldConstraints(
            description=N_("User namespace mode"),
            label_hint=N_("e.g. auto, keep-id, host"),
        ),
    ] = SafeStr.trusted("", "default")  # kept for DB compat, superseded by uid_map/gid_map
    uid_map: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 8, 0),
            quadlet_key="UIDMap",
        ),
        FieldConstraints(
            description=N_("UID mapping between host and container"),
            label_hint=N_("e.g. 0:100000:65536"),
        ),
    ] = []
    gid_map: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 8, 0),
            quadlet_key="GIDMap",
        ),
        FieldConstraints(
            description=N_("GID mapping between host and container"),
            label_hint=N_("e.g. 0:100000:65536"),
        ),
    ] = []
    # Health checks
    health_cmd: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthCmd",
        ),
        FieldConstraints(
            description=N_("Command to check if the container is healthy"),
            label_hint=N_("shell command"),
        ),
    ] = SafeStr.trusted("", "default")
    health_interval: Annotated[
        SafeTimeDuration,
        VersionSpan(introduced=(4, 5, 0), quadlet_key="HealthInterval"),
        FieldConstraints(
            description=N_("Time between health checks"),
            placeholder=N_("30s"),
            label_hint=N_("e.g. 30s, 5min"),
        ),
    ] = SafeTimeDuration.trusted("", "default")
    health_timeout: Annotated[
        SafeTimeDuration,
        VersionSpan(introduced=(4, 5, 0), quadlet_key="HealthTimeout"),
        FieldConstraints(
            description=N_("Maximum time for a health check to complete"),
            placeholder=N_("30s"),
            label_hint=N_("e.g. 30s, 5min"),
        ),
    ] = SafeTimeDuration.trusted("", "default")
    health_retries: Annotated[
        SafeIntOrEmpty,
        VersionSpan(introduced=(4, 5, 0), quadlet_key="HealthRetries"),
        FieldConstraints(
            description=N_("Consecutive failures before marking unhealthy"),
            placeholder="3",
            label_hint=N_("integer"),
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    health_start_period: Annotated[
        SafeTimeDuration,
        VersionSpan(introduced=(4, 5, 0), quadlet_key="HealthStartPeriod"),
        FieldConstraints(
            description=N_("Grace period before health checks count"),
            placeholder=N_("0s"),
            label_hint=N_("e.g. 0s, 30s"),
        ),
    ] = SafeTimeDuration.trusted("", "default")
    health_on_failure: Annotated[
        SafeHealthOnFailure,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="HealthOnFailure",
        ),
        HEALTH_ON_FAILURE_CHOICES,
        FieldConstraints(description=N_("Action when health check fails")),
    ] = SafeHealthOnFailure.trusted("", "default")
    notify_healthy: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="Notify",
        ),
        FieldConstraints(description=N_("Notify systemd when container becomes healthy")),
    ] = False
    # Image auto-update
    auto_update: Annotated[
        SafeAutoUpdatePolicy,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="AutoUpdate",
        ),
        AUTO_UPDATE_POLICY_CHOICES,
        FieldConstraints(description=N_("Automatic image update policy")),
    ] = SafeAutoUpdatePolicy.trusted("", "default")
    # Environment file
    environment_file: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="EnvironmentFile"),
        FieldConstraints(
            description=N_("Host file with KEY=value environment variables"),
            label_hint=N_("absolute path"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    # Command/entrypoint overrides
    exec_cmd: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Exec"),
        FieldConstraints(
            description=N_("Overrides the image CMD"),
            label_hint=N_("optional override"),
        ),
    ] = SafeStr.trusted("", "default")
    entrypoint: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="Entrypoint",
        ),
        FieldConstraints(
            description=N_("Overrides the image ENTRYPOINT"),
            label_hint=N_("optional override"),
        ),
    ] = SafeStr.trusted("", "default")
    # Security options
    no_new_privileges: Annotated[
        bool,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="NoNewPrivileges"),
        FieldConstraints(description=N_("Prevent gaining new privileges via setuid/setgid")),
    ] = False
    read_only: Annotated[
        bool,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="ReadOnly"),
        FieldConstraints(description=N_("Mount the container's root filesystem as read-only")),
    ] = False
    privileged: Annotated[
        bool,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Privileged"),
        FieldConstraints(description=N_("Run with extended privileges")),
    ] = False
    drop_caps: Annotated[
        list[SafeLinuxCapability],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="DropCapability"),
        FieldConstraints(
            description=N_("Linux capabilities to remove from the container"),
            label_hint=N_("e.g. CAP_NET_ADMIN, ALL"),
        ),
    ] = []
    add_caps: Annotated[
        list[SafeLinuxCapability],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="AddCapability"),
        FieldConstraints(
            description=N_("Linux capabilities to add to the container"),
            label_hint=N_("e.g. CAP_NET_BIND_SERVICE"),
        ),
    ] = []
    seccomp_profile: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="SeccompProfile"),
        FieldConstraints(
            description=N_("Path to a custom seccomp security profile"),
            label_hint=N_("absolute path to JSON"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    mask_paths: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="Mask",
        ),
        FieldConstraints(
            description=N_("Paths hidden from the container process"),
            label_hint=N_("absolute paths"),
        ),
    ] = []
    unmask_paths: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="Unmask",
        ),
        FieldConstraints(
            description=N_("Paths re-exposed from Podman defaults"),
            label_hint=N_("absolute paths"),
        ),
    ] = []
    sysctl: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="Sysctl",
        ),
        FieldConstraints(
            description=N_("Kernel parameters set inside the container"),
            label_hint=N_("key=value pairs"),
        ),
    ] = {}
    # Runtime
    working_dir: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="WorkingDir",
        ),
        FieldConstraints(
            description=N_("Working directory inside the container"),
            label_hint=N_("optional"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    # Networking
    hostname: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="HostName",
        ),
        FieldConstraints(
            description=N_("Hostname of the container"),
            label_hint=N_("optional"),
        ),
    ] = SafeStr.trusted("", "default")
    dns: Annotated[
        list[SafeIpAddress],
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="DNS",
        ),
        FieldConstraints(
            description=N_("Custom DNS servers for the container"),
            label_hint=N_("IP addresses"),
        ),
    ] = []
    dns_search: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="DNSSearch",
        ),
        FieldConstraints(
            description=N_("DNS search domains"),
            label_hint=N_("domain names"),
        ),
    ] = []
    dns_option: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="DNSOption",
        ),
        FieldConstraints(
            description=N_("DNS resolver options"),
            label_hint=N_("resolver options"),
        ),
    ] = []
    # Pod assignment (P2)
    pod_name: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="Pod",
        ),
        FieldChoices(dynamic=True, empty_label="None — use Network setting"),
        FieldConstraints(description=N_("Pod to assign this container to")),
    ] = SafeStr.trusted("", "default")
    # Logging (P3)
    log_driver: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="LogDriver",
        ),
        FieldChoices(dynamic=True, empty_label="default"),
        FieldConstraints(description=N_("Logging driver for container output")),
    ] = SafeStr.trusted("", "default")  # e.g. "journald", "json-file", "none"
    log_opt: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="LogOpt",
        ),
        FieldConstraints(
            description=N_("Logging driver options"),
            label_hint=N_("key=value pairs"),
        ),
    ] = {}
    # Additional service lifecycle hooks (P3)
    exec_start_post: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key=""),
        FieldConstraints(
            description=N_("Command to run after the container starts"),
            label_hint=N_("run after container starts"),
        ),
    ] = SafeStr.trusted("", "default")
    exec_stop: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key=""),
        FieldConstraints(
            description=N_("Command to run when stopping the container"),
            label_hint=N_("run on stop"),
        ),
    ] = SafeStr.trusted("", "default")
    # Feature 1: host device passthrough
    devices: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="AddDevice"),
        FieldConstraints(
            description=N_("Host devices passed into the container"),
            label_hint=N_("e.g. /dev/dri/renderD128"),
        ),
    ] = []
    # Feature 2: OCI runtime (e.g. "crun", "kata", "gvisor")
    runtime: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="PodmanArgs",
        ),
        FieldConstraints(
            description=N_("e.g. crun, kata, gvisor"),
            label_hint=N_("empty = Podman default"),
        ),
    ] = SafeStr.trusted("", "default")
    # Feature 3: raw extra [Service] directives (multi-line freeform)
    service_extra: Annotated[
        SafeMultilineStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key=""),
        FieldConstraints(
            description=N_("Extra systemd [Service] directives"),
            label_hint=N_("one directive per line"),
        ),
    ] = SafeMultilineStr.trusted("", "default")
    # Feature 5: run an init process as PID 1
    init: Annotated[
        bool,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="PodmanArgs",
        ),
        FieldConstraints(description=N_("tini as PID 1 — reaps zombies and forwards signals")),
    ] = False
    # Feature 6: soft memory reservation and cgroup fair-share weights
    memory_reservation: Annotated[
        SafeByteSize,
        VersionSpan(introduced=(4, 4, 0), quadlet_key=""),
        FieldConstraints(
            description=N_("Soft memory limit for fair scheduling"),
            placeholder=N_("256m"),
            label_hint=N_("soft low watermark, e.g. 256m"),
        ),
    ] = SafeByteSize.trusted("", "default")
    cpu_weight: Annotated[
        SafeIntOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key=""),
        FieldConstraints(
            description=N_("Relative CPU share weight among containers"),
            placeholder="100",
            label_hint=N_("fair-share, 1–10000, default 100"),
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    io_weight: Annotated[
        SafeIntOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key=""),
        FieldConstraints(
            description=N_("Relative block I/O share weight"),
            placeholder="100",
            label_hint=N_("block I/O share, 1–10000, default 100"),
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    # Feature 15: additional network aliases
    network_aliases: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 2, 0),
            quadlet_key="NetworkAlias",
        ),
        FieldConstraints(
            description=N_("Additional DNS aliases on the shared network"),
            label_hint=N_("DNS alias names"),
        ),
    ] = []

    # Secrets referenced in the container unit (Secret= key)
    secrets: Annotated[
        list[SafeSecretName],
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="Secret",
        ),
        FieldConstraints(
            description=N_("Podman secrets available to the container"),
            label_hint=N_("secret names"),
        ),
    ] = []

    # ------------------------------------------------------------------
    # Podman 4.4.0 (base Quadlet keys)
    # ------------------------------------------------------------------
    annotation: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Annotation"),
        FieldConstraints(
            description=N_("OCI annotations on the container"),
            label_hint=N_("key=value pairs"),
        ),
    ] = []
    expose_host_port: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="ExposeHostPort"),
        FieldConstraints(
            description=N_("Ports to expose from the host"),
            label_hint=N_("port numbers"),
        ),
    ] = []
    group: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Group"),
        FieldConstraints(
            description=N_("Group or GID for the container process"),
            label_hint=N_("GID or group name"),
        ),
    ] = SafeStr.trusted("", "default")
    security_label_disable: Annotated[
        bool,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="SecurityLabelDisable"),
        FieldConstraints(description=N_("Disable SELinux labeling for the container")),
    ] = False
    security_label_file_type: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="SecurityLabelFileType"),
        FieldConstraints(
            description=N_("SELinux file type label"),
            label_hint=N_("SELinux type"),
        ),
    ] = SafeStr.trusted("", "default")
    security_label_level: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="SecurityLabelLevel"),
        FieldConstraints(
            description=N_("SELinux level label"),
            label_hint=N_("SELinux level"),
        ),
    ] = SafeStr.trusted("", "default")
    security_label_type: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="SecurityLabelType"),
        FieldConstraints(
            description=N_("SELinux process type label"),
            label_hint=N_("SELinux type"),
        ),
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
        FieldConstraints(
            description=N_("Tmpfs mounts inside the container"),
            label_hint=N_("e.g. /tmp:size=64m"),
        ),
    ] = []
    ip: Annotated[
        SafeIpAddress,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="IP",
        ),
        FieldConstraints(
            description=N_("Static IPv4 address for the container"),
            label_hint=N_("e.g. 10.88.0.5"),
        ),
    ] = SafeIpAddress.trusted("", "default")
    ip6: Annotated[
        SafeIpAddress,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="IP6",
        ),
        FieldConstraints(
            description=N_("Static IPv6 address for the container"),
            label_hint=N_("e.g. fd00::1"),
        ),
    ] = SafeIpAddress.trusted("", "default")
    mount: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="Mount",
        ),
        FieldConstraints(
            description=N_("Additional mount specifications"),
            label_hint=N_("mount specification"),
        ),
    ] = []
    rootfs: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(
            introduced=(4, 5, 0),
            quadlet_key="Rootfs",
        ),
        FieldConstraints(
            description=N_("Host rootfs directory instead of an image"),
            label_hint=N_("absolute path"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")

    # ------------------------------------------------------------------
    # Podman 4.6.0
    # ------------------------------------------------------------------
    pull: Annotated[
        SafePullPolicy,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="Pull",
        ),
        FieldConstraints(
            description=N_("Image pull policy"),
            label_hint=N_("e.g. always, missing, never"),
        ),
    ] = SafePullPolicy.trusted("", "default")
    security_label_nested: Annotated[
        bool,
        VersionSpan(
            introduced=(4, 6, 0),
            quadlet_key="SecurityLabelNested",
        ),
        FieldConstraints(description=N_("Enable nested SELinux labeling")),
    ] = False

    # ------------------------------------------------------------------
    # Podman 4.7.0
    # ------------------------------------------------------------------
    pids_limit: Annotated[
        SafeIntOrEmpty,
        VersionSpan(introduced=(4, 7, 0), quadlet_key="PidsLimit"),
        FieldConstraints(
            description=N_("Maximum number of processes in the container"),
            placeholder="100",
            label_hint=N_("integer"),
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    ulimits: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(4, 7, 0),
            quadlet_key="Ulimit",
        ),
        FieldConstraints(
            description=N_("Resource limits for the container process"),
            label_hint=N_("e.g. nofile=1024:2048"),
        ),
    ] = []
    shm_size: Annotated[
        SafeByteSize,
        VersionSpan(introduced=(4, 7, 0), quadlet_key="ShmSize"),
        FieldConstraints(
            description=N_("Size of /dev/shm shared memory"),
            placeholder=N_("64m"),
            label_hint=N_("e.g. 64m"),
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
        FieldConstraints(description=N_("Mount a tmpfs on /tmp in read-only containers")),
    ] = False
    sub_uid_map: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 8, 0),
            quadlet_key="SubUIDMap",
        ),
        FieldConstraints(
            description=N_("Subordinate UID mapping name"),
            label_hint=N_("mapping name"),
        ),
    ] = SafeStr.trusted("", "default")
    sub_gid_map: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 8, 0),
            quadlet_key="SubGIDMap",
        ),
        FieldConstraints(
            description=N_("Subordinate GID mapping name"),
            label_hint=N_("mapping name"),
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
        FieldConstraints(
            description=N_("containers.conf module to load"),
            label_hint=N_("module path"),
        ),
    ] = SafeStr.trusted("", "default")
    global_args: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="GlobalArgs",
        ),
        FieldConstraints(
            description=N_("Global Podman CLI arguments"),
            label_hint=N_("CLI arguments"),
        ),
    ] = []
    stop_timeout: Annotated[
        SafeTimeDuration,
        VersionSpan(introduced=(5, 0, 0), quadlet_key="StopTimeout"),
        FieldConstraints(
            description=N_("Seconds to wait before forcefully stopping"),
            placeholder=N_("10s"),
            label_hint=N_("e.g. 10s, 30s"),
        ),
    ] = SafeTimeDuration.trusted("", "default")
    run_init: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="RunInit",
        ),
        FieldConstraints(description=N_("Run an init process inside the container")),
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
        FieldConstraints(
            description=N_("Additional groups for the container process"),
            label_hint=N_("GID or group name"),
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
        FieldConstraints(
            description=N_("Signal sent to stop the container"),
            label_hint=N_("e.g. SIGTERM, 9"),
        ),
    ] = SafeSignalName.trusted("", "default")

    # ------------------------------------------------------------------
    # Podman 5.3.0
    # ------------------------------------------------------------------
    service_name: Annotated[
        SafeUnitName,
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="ServiceName",
        ),
        FieldConstraints(
            description=N_("Override the systemd service name"),
            label_hint=N_("systemd unit name"),
        ),
    ] = SafeUnitName.trusted("", "default")
    default_dependencies: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="DefaultDependencies",
        ),
        FieldConstraints(description=N_("Include default systemd unit dependencies")),
    ] = True
    add_host: Annotated[
        list[SafeStr],
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="AddHost",
        ),
        FieldConstraints(
            description=N_("Custom /etc/hosts entries"),
            label_hint=N_("e.g. hostname:IP"),
        ),
    ] = []
    cgroups_mode: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="CgroupsMode",
        ),
        FieldConstraints(
            description=N_("Cgroup management mode"),
            label_hint=N_("e.g. enabled, disabled"),
        ),
    ] = SafeStr.trusted("", "default")
    start_with_pod: Annotated[
        bool,
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="StartWithPod",
        ),
        FieldConstraints(description=N_("Start this container when its pod starts")),
    ] = False
    timezone: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 3, 0),
            quadlet_key="Timezone",
        ),
        FieldConstraints(
            description=N_("Container timezone"),
            label_hint=N_("e.g. UTC, Europe/Helsinki"),
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
        FieldConstraints(description=N_("Pass all host environment variables")),
    ] = False
    memory: Annotated[
        SafeByteSize,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="Memory",
        ),
        FieldConstraints(
            description=N_("Memory limit for the container"),
            label_hint=N_("e.g. 512m, 1G"),
        ),
    ] = SafeByteSize.trusted("", "default")
    reload_cmd: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="ReloadCmd",
        ),
        FieldConstraints(
            description=N_("Command to reload the container's configuration"),
            label_hint=N_("shell command"),
        ),
    ] = SafeStr.trusted("", "default")
    reload_signal: Annotated[
        SafeSignalName,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="ReloadSignal",
        ),
        FieldConstraints(
            description=N_("Signal sent to reload the container"),
            label_hint=N_("e.g. SIGHUP"),
        ),
    ] = SafeSignalName.trusted("", "default")
    retry: Annotated[
        SafeIntOrEmpty,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="Retry",
        ),
        FieldConstraints(
            description=N_("Number of pull retries on failure"),
            label_hint=N_("integer"),
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    retry_delay: Annotated[
        SafeTimeDuration,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="RetryDelay",
        ),
        FieldConstraints(
            description=N_("Delay between pull retries"),
            label_hint=N_("e.g. 5s, 1min"),
        ),
    ] = SafeTimeDuration.trusted("", "default")
    health_log_destination: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthLogDestination",
        ),
        FieldConstraints(
            description=N_("Path for health check log output"),
            label_hint=N_("absolute path"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    health_max_log_count: Annotated[
        SafeIntOrEmpty,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthMaxLogCount",
        ),
        FieldConstraints(
            description=N_("Maximum number of health check log entries"),
            label_hint=N_("integer"),
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    health_max_log_size: Annotated[
        SafeByteSize,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthMaxLogSize",
        ),
        FieldConstraints(
            description=N_("Maximum size of health check log"),
            label_hint=N_("e.g. 512k, 1m"),
        ),
    ] = SafeByteSize.trusted("", "default")
    health_startup_cmd: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupCmd",
        ),
        FieldConstraints(
            description=N_("Startup health check command"),
            label_hint=N_("shell command"),
        ),
    ] = SafeStr.trusted("", "default")
    health_startup_interval: Annotated[
        SafeTimeDuration,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupInterval",
        ),
        FieldConstraints(
            description=N_("Interval for startup health checks"),
            label_hint=N_("e.g. 10s"),
        ),
    ] = SafeTimeDuration.trusted("", "default")
    health_startup_retries: Annotated[
        SafeIntOrEmpty,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupRetries",
        ),
        FieldConstraints(
            description=N_("Retries for startup health checks"),
            label_hint=N_("integer"),
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    health_startup_success: Annotated[
        SafeIntOrEmpty,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupSuccess",
        ),
        FieldConstraints(
            description=N_("Successes needed to pass startup check"),
            label_hint=N_("integer"),
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    health_startup_timeout: Annotated[
        SafeTimeDuration,
        VersionSpan(
            introduced=(5, 5, 0),
            quadlet_key="HealthStartupTimeout",
        ),
        FieldConstraints(
            description=N_("Timeout for startup health checks"),
            label_hint=N_("e.g. 30s"),
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
        FieldConstraints(description=N_("Pass host HTTP proxy environment variables")),
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
        _sanitize_db_row(d, Container)
        return d

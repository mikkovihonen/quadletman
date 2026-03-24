"""Dataclass models used by service-layer modules.

These are internal data structures for bundle parsing, host settings, and
SELinux booleans.  They live here so all models are discoverable in one
place and can evolve into shared contracts if needed.
"""

from dataclasses import dataclass, field

from ..sanitized import SafeStr, enforce_model_safety

# ---------------------------------------------------------------------------
# Bundle parser models (Podman 5.8+ .quadlets files)
# ---------------------------------------------------------------------------


@enforce_model_safety
@dataclass
class ParsedContainer:
    qm_name: SafeStr
    image: SafeStr
    environment: dict[SafeStr, SafeStr] = field(default_factory=dict)
    ports: list[SafeStr] = field(default_factory=list)
    labels: dict[SafeStr, SafeStr] = field(default_factory=dict)
    network: SafeStr = SafeStr.trusted("host", "default")
    restart_policy: SafeStr = SafeStr.trusted("always", "default")
    exec_start_pre: SafeStr = SafeStr.trusted("", "default")
    exec_start_post: SafeStr = SafeStr.trusted("", "default")
    exec_stop: SafeStr = SafeStr.trusted("", "default")
    memory_limit: SafeStr = SafeStr.trusted("", "default")
    cpu_quota: SafeStr = SafeStr.trusted("", "default")
    depends_on: list[SafeStr] = field(default_factory=list)
    apparmor_profile: SafeStr = SafeStr.trusted("", "default")
    pod: SafeStr = SafeStr.trusted("", "default")
    log_driver: SafeStr = SafeStr.trusted("", "default")
    working_dir: SafeStr = SafeStr.trusted("", "default")
    hostname: SafeStr = SafeStr.trusted("", "default")
    no_new_privileges: bool = False
    read_only: bool = False
    skipped_volumes: list[SafeStr] = field(default_factory=list)


@enforce_model_safety
@dataclass
class ParsedPod:
    qm_name: SafeStr
    network: SafeStr = SafeStr.trusted("", "default")
    publish_ports: list[SafeStr] = field(default_factory=list)


@enforce_model_safety
@dataclass
class ParsedVolumeUnit:
    qm_name: SafeStr
    driver: SafeStr = SafeStr.trusted("", "default")
    device: SafeStr = SafeStr.trusted("", "default")
    options: SafeStr = SafeStr.trusted("", "default")
    copy: bool = True


@enforce_model_safety
@dataclass
class ParsedImageUnit:
    qm_name: SafeStr
    image: SafeStr
    auth_file: SafeStr = SafeStr.trusted("", "default")


@enforce_model_safety
@dataclass
class BundleParseResult:
    containers: list[ParsedContainer] = field(default_factory=list)
    pods: list[ParsedPod] = field(default_factory=list)
    volume_units: list[ParsedVolumeUnit] = field(default_factory=list)
    image_units: list[ParsedImageUnit] = field(default_factory=list)
    skipped_section_types: list[SafeStr] = field(default_factory=list)
    warnings: list[SafeStr] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Host settings (sysctl) models
# ---------------------------------------------------------------------------


@enforce_model_safety
@dataclass(frozen=True)
class SysctlSetting:
    key: SafeStr
    category: SafeStr
    description: SafeStr
    # "integer", "ping_range", or "boolean" (0/1 integer)
    value_type: SafeStr = SafeStr.trusted("integer", "default")
    # inclusive bounds for integer/ping_range types; None means unbounded
    min_val: int | None = None
    max_val: int | None = None


@enforce_model_safety
@dataclass
class SysctlEntry:
    key: SafeStr
    # Normalised value string (space-separated for ping_range)
    value: SafeStr
    category: SafeStr
    description: SafeStr
    value_type: SafeStr
    min_val: int | None
    max_val: int | None
    # For ping_range only: the two components [low, high]
    value_parts: list[SafeStr] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SELinux boolean models
# ---------------------------------------------------------------------------


@enforce_model_safety
@dataclass(frozen=True)
class BooleanDef:
    name: SafeStr
    category: SafeStr
    description: SafeStr


@enforce_model_safety
@dataclass
class BooleanEntry:
    name: SafeStr
    category: SafeStr
    description: SafeStr
    enabled: bool

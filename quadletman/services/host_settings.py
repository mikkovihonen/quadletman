"""Read and write host kernel (sysctl) settings relevant to Podman rootless containers.

Only settings declared in SETTINGS may be written — there is no facility for arbitrary
sysctl access. Values are applied immediately via `sysctl -w` and persisted to
/etc/sysctl.d/99-quadletman.conf so they survive reboots.
"""

import asyncio
import contextlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import host

_SYSCTL_D_PATH = Path("/etc/sysctl.d/99-quadletman.conf")
_PROC_SYS = Path("/proc/sys")

_CONTROL_CHARS_RE = re.compile(r"[\r\n\x00]")
_INTEGER_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class SysctlSetting:
    key: str
    category: str
    description: str
    # "integer", "ping_range", or "boolean" (0/1 integer)
    value_type: str = "integer"
    # inclusive bounds for integer/ping_range types; None means unbounded
    min_val: int | None = None
    max_val: int | None = None


SETTINGS: list[SysctlSetting] = [
    # Networking
    SysctlSetting(
        key="net.ipv4.ip_unprivileged_port_start",
        category="Networking",
        description="Lowest port number rootless containers can bind. Set to 80 to allow HTTP/HTTPS.",
        min_val=0,
        max_val=65535,
    ),
    SysctlSetting(
        key="net.ipv4.ip_forward",
        category="Networking",
        description="Enable IP forwarding between network interfaces. Required for inter-container routing.",
        value_type="boolean",
    ),
    SysctlSetting(
        key="net.ipv4.ping_group_range",
        category="Networking",
        description=(
            "GID range allowed to use ICMP sockets inside containers. "
            "Set low=0 / high=2147483647 to allow all groups. "
            'To disable: set low=1 / high=0 (inverted range signals "no groups").'
        ),
        value_type="ping_range",
        min_val=0,
        max_val=2147483647,
    ),
    # User Namespaces
    SysctlSetting(
        key="user.max_user_namespaces",
        category="User Namespaces",
        description="Maximum number of user namespaces. Must be > 0 for rootless Podman to function.",
        min_val=0,
        max_val=65536,
    ),
    SysctlSetting(
        key="kernel.unprivileged_userns_clone",
        category="User Namespaces",
        description="Allow unprivileged users to create user namespaces. Required on Debian/Ubuntu.",
        value_type="boolean",
    ),
    # Resources
    SysctlSetting(
        key="vm.max_map_count",
        category="Resources",
        description="Maximum virtual memory map areas per process. Elasticsearch/OpenSearch require ≥ 262144.",
        min_val=65530,
        max_val=2097152,
    ),
    SysctlSetting(
        key="fs.inotify.max_user_watches",
        category="Resources",
        description="Maximum inotify file watches per user. Increase for containers that watch many files.",
        min_val=8192,
        max_val=4194304,
    ),
    SysctlSetting(
        key="fs.inotify.max_user_instances",
        category="Resources",
        description="Maximum inotify instances per user. Increase when running many containers concurrently.",
        min_val=128,
        max_val=65536,
    ),
]

_SETTINGS_BY_KEY: dict[str, SysctlSetting] = {s.key: s for s in SETTINGS}


@dataclass
class SysctlEntry:
    key: str
    # Normalised value string (space-separated for ping_range)
    value: str
    category: str
    description: str
    value_type: str
    min_val: int | None
    max_val: int | None
    # For ping_range only: the two components [low, high]
    value_parts: list[str] = field(default_factory=list)


def _proc_path(key: str) -> Path:
    return _PROC_SYS / key.replace(".", "/")


def read_all() -> list[SysctlEntry]:
    """Read current values from /proc/sys for each known setting.

    Settings whose /proc path does not exist (e.g. kernel.unprivileged_userns_clone on RHEL)
    are silently omitted.
    """
    entries = []
    for setting in SETTINGS:
        path = _proc_path(setting.key)
        try:
            raw = path.read_text().strip()
        except (FileNotFoundError, PermissionError):
            continue

        # Normalise whitespace — the kernel may use tabs (e.g. ping_group_range)
        value = " ".join(raw.split())
        value_parts: list[str] = []
        if setting.value_type == "ping_range":
            value_parts = value.split()

        entries.append(
            SysctlEntry(
                key=setting.key,
                value=value,
                category=setting.category,
                description=setting.description,
                value_type=setting.value_type,
                min_val=setting.min_val,
                max_val=setting.max_val,
                value_parts=value_parts,
            )
        )
    return entries


def _validate_value(setting: SysctlSetting, value: str) -> str:
    """Validate and normalise a sysctl value. Returns the cleaned value or raises ValueError."""
    if _CONTROL_CHARS_RE.search(value):
        raise ValueError(f"Value for {setting.key} contains disallowed control characters")
    value = " ".join(value.split())  # normalise whitespace
    if not value:
        raise ValueError(f"Value for {setting.key} must not be empty")
    if len(value) > 64:
        raise ValueError(f"Value for {setting.key} is too long")

    if setting.value_type == "boolean":
        if value not in ("0", "1"):
            raise ValueError(f"Value for {setting.key} must be 0 or 1")

    elif setting.value_type == "integer":
        if not _INTEGER_RE.match(value):
            raise ValueError(f"Value for {setting.key} must be a non-negative integer")
        n = int(value)
        if setting.min_val is not None and n < setting.min_val:
            raise ValueError(f"Value for {setting.key} must be ≥ {setting.min_val} (got {n})")
        if setting.max_val is not None and n > setting.max_val:
            raise ValueError(f"Value for {setting.key} must be ≤ {setting.max_val} (got {n})")

    elif setting.value_type == "ping_range":
        parts = value.split()
        if len(parts) != 2 or not all(_INTEGER_RE.match(p) for p in parts):
            raise ValueError(
                f"Value for {setting.key} must be two space-separated non-negative integers"
            )
        low, high = int(parts[0]), int(parts[1])
        bound = setting.max_val if setting.max_val is not None else 2147483647
        if low > bound or high > bound:
            raise ValueError(f"Both values for {setting.key} must be ≤ {bound}")
        # Reconstruct normalised form
        value = f"{low} {high}"

    return value


def _persist(key: str, value: str) -> None:
    """Rewrite /etc/sysctl.d/99-quadletman.conf to include the updated key=value pair.

    Reads all currently managed keys from the file (if it exists), updates the given key,
    and writes the result atomically via a temp file + rename.
    """
    managed: dict[str, str] = {}

    if _SYSCTL_D_PATH.exists():
        for line in _SYSCTL_D_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                managed[k.strip()] = v.strip()

    managed[key] = value

    lines = ["# Managed by quadletman — do not edit manually\n"]
    for k, v in sorted(managed.items()):
        lines.append(f"{k} = {v}\n")

    dir_path = _SYSCTL_D_PATH.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, prefix=".99-quadletman-")
    try:
        with os.fdopen(fd, "w") as f:
            f.writelines(lines)
        host.rename(tmp_path, str(_SYSCTL_D_PATH))
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


async def apply(key: str, value: str) -> None:
    """Validate, apply, and persist a sysctl setting.

    Raises ValueError for unknown keys or invalid values.
    Raises RuntimeError if sysctl -w fails.
    """
    setting = _SETTINGS_BY_KEY.get(key)
    if setting is None:
        raise ValueError(f"Unknown sysctl key: {key!r}")

    value = _validate_value(setting, value)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _apply_sync, key, value)


@host.audit("SYSCTL_SET", lambda key, value, *_: f"{key}={value}")
def _apply_sync(key: str, value: str) -> None:
    result = host.run(
        ["sysctl", "-w", f"{key}={value}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"sysctl -w {key}={value} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    _persist(key, value)

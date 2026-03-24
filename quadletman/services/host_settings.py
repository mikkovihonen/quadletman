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
from pathlib import Path

from ..models import sanitized
from ..models.sanitized import SafeAbsPath, SafeStr
from ..models.service import SysctlEntry, SysctlSetting
from ..utils import cmd_token
from . import host

_SYSCTL_D_PATH = Path("/etc/sysctl.d/99-quadletman.conf")
_PROC_SYS = Path("/proc/sys")


_CONTROL_CHARS_RE = re.compile(r"[\r\n\x00]")
_INTEGER_RE = re.compile(r"^\d+$")


SETTINGS: list[SysctlSetting] = [
    # Networking
    SysctlSetting(
        key=SafeStr.trusted("net.ipv4.ip_unprivileged_port_start", "hardcoded"),
        category=SafeStr.trusted("Networking", "hardcoded"),
        description=SafeStr.trusted(
            "Lowest port number rootless containers can bind. Set to 80 to allow HTTP/HTTPS.",
            "hardcoded",
        ),
        min_val=0,
        max_val=65535,
    ),
    SysctlSetting(
        key=SafeStr.trusted("net.ipv4.ip_forward", "hardcoded"),
        category=SafeStr.trusted("Networking", "hardcoded"),
        description=SafeStr.trusted(
            "Enable IP forwarding between network interfaces. Required for inter-container routing.",
            "hardcoded",
        ),
        value_type=SafeStr.trusted("boolean", "hardcoded"),
    ),
    SysctlSetting(
        key=SafeStr.trusted("net.ipv4.ping_group_range", "hardcoded"),
        category=SafeStr.trusted("Networking", "hardcoded"),
        description=SafeStr.trusted(
            "GID range allowed to use ICMP sockets inside containers. "
            "Set low=0 / high=2147483647 to allow all groups. "
            'To disable: set low=1 / high=0 (inverted range signals "no groups").',
            "hardcoded",
        ),
        value_type=SafeStr.trusted("ping_range", "hardcoded"),
        min_val=0,
        max_val=2147483647,
    ),
    # User Namespaces
    SysctlSetting(
        key=SafeStr.trusted("user.max_user_namespaces", "hardcoded"),
        category=SafeStr.trusted("User Namespaces", "hardcoded"),
        description=SafeStr.trusted(
            "Maximum number of user namespaces. Must be > 0 for rootless Podman to function.",
            "hardcoded",
        ),
        min_val=0,
        max_val=65536,
    ),
    SysctlSetting(
        key=SafeStr.trusted("kernel.unprivileged_userns_clone", "hardcoded"),
        category=SafeStr.trusted("User Namespaces", "hardcoded"),
        description=SafeStr.trusted(
            "Allow unprivileged users to create user namespaces. Required on Debian/Ubuntu.",
            "hardcoded",
        ),
        value_type=SafeStr.trusted("boolean", "hardcoded"),
    ),
    # Resources
    SysctlSetting(
        key=SafeStr.trusted("vm.max_map_count", "hardcoded"),
        category=SafeStr.trusted("Resources", "hardcoded"),
        description=SafeStr.trusted(
            "Maximum virtual memory map areas per process. Elasticsearch/OpenSearch require ≥ 262144.",
            "hardcoded",
        ),
        min_val=65530,
        max_val=2097152,
    ),
    SysctlSetting(
        key=SafeStr.trusted("fs.inotify.max_user_watches", "hardcoded"),
        category=SafeStr.trusted("Resources", "hardcoded"),
        description=SafeStr.trusted(
            "Maximum inotify file watches per user. Increase for containers that watch many files.",
            "hardcoded",
        ),
        min_val=8192,
        max_val=4194304,
    ),
    SysctlSetting(
        key=SafeStr.trusted("fs.inotify.max_user_instances", "hardcoded"),
        category=SafeStr.trusted("Resources", "hardcoded"),
        description=SafeStr.trusted(
            "Maximum inotify instances per user. Increase when running many containers concurrently.",
            "hardcoded",
        ),
        min_val=128,
        max_val=65536,
    ),
]

_SETTINGS_BY_KEY: dict[str, SysctlSetting] = {s.key: s for s in SETTINGS}


@sanitized.enforce
def _proc_path(key: SafeStr) -> Path:
    return _PROC_SYS / key.replace(".", "/")


@sanitized.enforce
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
        value_parts: list[SafeStr] = []
        if setting.value_type == "ping_range":
            value_parts = [SafeStr.of(p, "proc_sys_value_part") for p in value.split()]

        entries.append(
            SysctlEntry(
                key=setting.key,
                value=SafeStr.of(value, "proc_sys_value"),
                category=setting.category,
                description=setting.description,
                value_type=setting.value_type,
                min_val=setting.min_val,
                max_val=setting.max_val,
                value_parts=value_parts,
            )
        )
    return entries


@sanitized.enforce
def _validate_value(setting: SysctlSetting, value: SafeStr) -> SafeStr:
    """Validate and normalise a sysctl value. Returns the cleaned value or raises ValueError."""
    if _CONTROL_CHARS_RE.search(value):
        raise ValueError(f"Value for {setting.key} contains disallowed control characters")
    cleaned = " ".join(value.split())  # normalise whitespace
    if not cleaned:
        raise ValueError(f"Value for {setting.key} must not be empty")
    if len(cleaned) > 64:
        raise ValueError(f"Value for {setting.key} is too long")

    if setting.value_type == "boolean":
        if cleaned not in ("0", "1"):
            raise ValueError(f"Value for {setting.key} must be 0 or 1")

    elif setting.value_type == "integer":
        if not _INTEGER_RE.match(cleaned):
            raise ValueError(f"Value for {setting.key} must be a non-negative integer")
        n = int(cleaned)
        if setting.min_val is not None and n < setting.min_val:
            raise ValueError(f"Value for {setting.key} must be ≥ {setting.min_val} (got {n})")
        if setting.max_val is not None and n > setting.max_val:
            raise ValueError(f"Value for {setting.key} must be ≤ {setting.max_val} (got {n})")

    elif setting.value_type == "ping_range":
        parts = cleaned.split()
        if len(parts) != 2 or not all(_INTEGER_RE.match(p) for p in parts):
            raise ValueError(
                f"Value for {setting.key} must be two space-separated non-negative integers"
            )
        low, high = int(parts[0]), int(parts[1])
        bound = setting.max_val if setting.max_val is not None else 2147483647
        if low > bound or high > bound:
            raise ValueError(f"Both values for {setting.key} must be ≤ {bound}")
        # Reconstruct normalised form
        cleaned = f"{low} {high}"

    return SafeStr.of(cleaned, "sysctl_value")


@sanitized.enforce
def _persist(key: SafeStr, value: SafeStr) -> None:
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
        host.rename(
            SafeAbsPath.of(tmp_path, "tmp_path"),
            SafeAbsPath.trusted(str(_SYSCTL_D_PATH), "hardcoded"),
        )
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


@host.audit("SYSCTL_SET", lambda key, value, *_: f"{key}={value}")
@sanitized.enforce
async def apply(key: SafeStr, value: SafeStr) -> None:
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


@sanitized.enforce
def _apply_sync(key: SafeStr, value: SafeStr) -> None:
    result = host.run(
        [cmd_token("sysctl"), cmd_token("-w"), cmd_token(f"{key}={value}")],
        admin=True,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"sysctl -w {key}={value} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    _persist(key, value)

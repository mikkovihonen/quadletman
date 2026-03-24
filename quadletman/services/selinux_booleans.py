"""Read and set SELinux boolean values relevant to Podman container workloads.

Only booleans declared in BOOLEANS may be written. Each boolean is probed at runtime via
`getsebool <name>`; booleans that do not exist on the current system are silently skipped,
making the feature cross-distribution. If SELinux is not active, read_all() returns None
and the UI shows an informational message.

Changes are applied persistently via `setsebool -P` so they survive reboots.
"""

import asyncio
import subprocess

from quadletman.models import sanitized
from quadletman.models.sanitized import SafeStr
from quadletman.models.service import BooleanDef, BooleanEntry
from quadletman.services import host
from quadletman.services.selinux import is_selinux_active
from quadletman.utils import cmd_token

BOOLEANS: list[BooleanDef] = [
    # Network Shares
    BooleanDef(
        name=SafeStr.trusted("virt_use_nfs", "hardcoded"),
        category=SafeStr.trusted("Network Shares", "hardcoded"),
        description=SafeStr.trusted(
            "Allow containers to mount NFS shares from the host.", "hardcoded"
        ),
    ),
    BooleanDef(
        name=SafeStr.trusted("virt_use_samba", "hardcoded"),
        category=SafeStr.trusted("Network Shares", "hardcoded"),
        description=SafeStr.trusted("Allow containers to access Samba/CIFS shares.", "hardcoded"),
    ),
    BooleanDef(
        name=SafeStr.trusted("virt_use_fusefs", "hardcoded"),
        category=SafeStr.trusted("Network Shares", "hardcoded"),
        description=SafeStr.trusted("Allow containers to use FUSE-based filesystems.", "hardcoded"),
    ),
    # Storage
    BooleanDef(
        name=SafeStr.trusted("container_use_cephfs", "hardcoded"),
        category=SafeStr.trusted("Storage", "hardcoded"),
        description=SafeStr.trusted("Allow containers to mount CephFS volumes.", "hardcoded"),
    ),
    # Networking
    BooleanDef(
        name=SafeStr.trusted("virt_sandbox_use_netlink", "hardcoded"),
        category=SafeStr.trusted("Networking", "hardcoded"),
        description=SafeStr.trusted(
            "Allow containers to open netlink sockets (needed by some network tools).", "hardcoded"
        ),
    ),
    BooleanDef(
        name=SafeStr.trusted("virt_use_rawip", "hardcoded"),
        category=SafeStr.trusted("Networking", "hardcoded"),
        description=SafeStr.trusted("Allow containers to create raw IP sockets.", "hardcoded"),
    ),
    # Capabilities
    BooleanDef(
        name=SafeStr.trusted("virt_use_execmem", "hardcoded"),
        category=SafeStr.trusted("Capabilities", "hardcoded"),
        description=SafeStr.trusted(
            "Allow containers to use executable memory (needed by some JIT runtimes).", "hardcoded"
        ),
    ),
    BooleanDef(
        name=SafeStr.trusted("virt_sandbox_use_all_caps", "hardcoded"),
        category=SafeStr.trusted("Capabilities", "hardcoded"),
        description=SafeStr.trusted(
            "Grant all Linux capabilities inside the container sandbox.", "hardcoded"
        ),
    ),
    # cgroups
    BooleanDef(
        name=SafeStr.trusted("container_manage_cgroup", "hardcoded"),
        category=SafeStr.trusted("cgroups", "hardcoded"),
        description=SafeStr.trusted(
            "Allow containers to manage their own cgroup hierarchy.", "hardcoded"
        ),
    ),
]

_BOOLEAN_NAMES: frozenset[str] = frozenset(b.name for b in BOOLEANS)
_BOOLEAN_BY_NAME: dict[str, BooleanDef] = {b.name: b for b in BOOLEANS}


@sanitized.enforce
def _read_all_sync() -> list[BooleanEntry] | None:
    """Return live boolean states, or None if SELinux is not active.

    Booleans that do not exist on this system are silently skipped.
    """
    if not is_selinux_active():
        return None

    entries: list[BooleanEntry] = []
    for bdef in BOOLEANS:
        try:
            result = subprocess.run(
                ["getsebool", bdef.name],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            continue
        if result.returncode != 0:
            continue
        # Output format: "virt_use_nfs --> on"
        parts = result.stdout.strip().split()
        if not parts:
            continue
        enabled = parts[-1] == "on"
        entries.append(
            BooleanEntry(
                name=bdef.name,
                category=bdef.category,
                description=bdef.description,
                enabled=enabled,
            )
        )
    return entries


@sanitized.enforce
async def read_all() -> list[BooleanEntry] | None:
    """Async wrapper for _read_all_sync."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_all_sync)


@sanitized.enforce
def _set_boolean_sync(name: SafeStr, enabled: bool) -> None:
    if name not in _BOOLEAN_NAMES:
        raise ValueError(f"Unknown SELinux boolean: {name!r}")
    try:
        result = host.run(
            [
                cmd_token("setsebool"),
                cmd_token("-P"),
                name,
                cmd_token("on") if enabled else cmd_token("off"),
            ],
            admin=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("setsebool not found — install policycoreutils") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"setsebool -P {name} failed: {result.stderr.strip() or result.stdout.strip()}"
        )


@host.audit("SELINUX_BOOL_SET", lambda name, enabled, *_: f"{name}={'on' if enabled else 'off'}")
@sanitized.enforce
async def set_boolean(name: SafeStr, enabled: bool) -> None:
    """Validate and persistently set an SELinux boolean.

    Raises ValueError for unknown names, RuntimeError if setsebool fails.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _set_boolean_sync, name, enabled)

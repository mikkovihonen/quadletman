"""Read and set SELinux boolean values relevant to Podman container workloads.

Only booleans declared in BOOLEANS may be written. Each boolean is probed at runtime via
`getsebool <name>`; booleans that do not exist on the current system are silently skipped,
making the feature cross-distribution. If SELinux is not active, read_all() returns None
and the UI shows an informational message.

Changes are applied persistently via `setsebool -P` so they survive reboots.
"""

import asyncio
import subprocess
from dataclasses import dataclass

from quadletman.services.selinux import is_selinux_active


@dataclass(frozen=True)
class BooleanDef:
    name: str
    category: str
    description: str


BOOLEANS: list[BooleanDef] = [
    # Network Shares
    BooleanDef(
        name="virt_use_nfs",
        category="Network Shares",
        description="Allow containers to mount NFS shares from the host.",
    ),
    BooleanDef(
        name="virt_use_samba",
        category="Network Shares",
        description="Allow containers to access Samba/CIFS shares.",
    ),
    BooleanDef(
        name="virt_use_fusefs",
        category="Network Shares",
        description="Allow containers to use FUSE-based filesystems.",
    ),
    # Storage
    BooleanDef(
        name="container_use_cephfs",
        category="Storage",
        description="Allow containers to mount CephFS volumes.",
    ),
    # Networking
    BooleanDef(
        name="virt_sandbox_use_netlink",
        category="Networking",
        description="Allow containers to open netlink sockets (needed by some network tools).",
    ),
    BooleanDef(
        name="virt_use_rawip",
        category="Networking",
        description="Allow containers to create raw IP sockets.",
    ),
    # Capabilities
    BooleanDef(
        name="virt_use_execmem",
        category="Capabilities",
        description="Allow containers to use executable memory (needed by some JIT runtimes).",
    ),
    BooleanDef(
        name="virt_sandbox_use_all_caps",
        category="Capabilities",
        description="Grant all Linux capabilities inside the container sandbox.",
    ),
    # cgroups
    BooleanDef(
        name="container_manage_cgroup",
        category="cgroups",
        description="Allow containers to manage their own cgroup hierarchy.",
    ),
]

_BOOLEAN_NAMES: frozenset[str] = frozenset(b.name for b in BOOLEANS)
_BOOLEAN_BY_NAME: dict[str, BooleanDef] = {b.name: b for b in BOOLEANS}


@dataclass
class BooleanEntry:
    name: str
    category: str
    description: str
    enabled: bool


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


async def read_all() -> list[BooleanEntry] | None:
    """Async wrapper for _read_all_sync."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_all_sync)


def _set_boolean_sync(name: str, enabled: bool) -> None:
    if name not in _BOOLEAN_NAMES:
        raise ValueError(f"Unknown SELinux boolean: {name!r}")
    try:
        result = subprocess.run(
            ["setsebool", "-P", name, "on" if enabled else "off"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("setsebool not found — install policycoreutils") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"setsebool -P {name} failed: {result.stderr.strip() or result.stdout.strip()}"
        )


async def set_boolean(name: str, enabled: bool) -> None:
    """Validate and persistently set an SELinux boolean.

    Raises ValueError for unknown names, RuntimeError if setsebool fails.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _set_boolean_sync, name, enabled)

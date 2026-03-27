"""Volume browser helpers."""

import os
from pathlib import PurePosixPath

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...i18n import gettext as _t
from ...models.sanitized import SafeAbsPath, SafeSlug, SafeStr, SafeUUID, resolve_safe_path
from ...services import compartment_manager, host
from ...services.selinux import get_file_context_type
from ...utils import fmt_bytes


async def get_vol(db: AsyncSession, compartment_id: SafeSlug, volume_id: SafeUUID):
    """Look up a single volume by compartment + volume ID, or raise 404."""
    vols = await compartment_manager.list_volumes(db, compartment_id)
    for v in vols:
        if v.id == volume_id:
            return v
    raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Volume not found"))


_EMPTY_MODE = {
    "ur": False,
    "uw": False,
    "ux": False,
    "gr": False,
    "gw": False,
    "gx": False,
    "or": False,
    "ow": False,
    "ox": False,
    "octal": "???",
}


def is_text(path: str, owner: str = "", limit: int = 8192) -> bool:
    """Check whether a file appears to be text (no null bytes in first *limit* bytes)."""
    safe_path = SafeAbsPath.of(path, "is_text_path")
    safe_owner = SafeStr.of(owner, "is_text_owner") if owner else SafeStr.trusted("", "default")
    data = host.read_bytes(safe_path, safe_owner, limit)
    if data is None:
        return False
    return b"\x00" not in data


def mode_bits(full: str, owner: str = "") -> dict:
    """Return rwx bits for owner/group/other as booleans."""
    safe_path = SafeAbsPath.of(full, "mode_bits_path")
    safe_owner = SafeStr.of(owner, "mode_bits_owner") if owner else SafeStr.trusted("", "default")
    st = host.stat_entry(safe_path, safe_owner)
    if st is None:
        return dict(_EMPTY_MODE)
    m = st["mode"]
    return {
        "ur": bool(m & 0o400),
        "uw": bool(m & 0o200),
        "ux": bool(m & 0o100),
        "gr": bool(m & 0o040),
        "gw": bool(m & 0o020),
        "gx": bool(m & 0o010),
        "or": bool(m & 0o004),
        "ow": bool(m & 0o002),
        "ox": bool(m & 0o001),
        "octal": oct(m & 0o777)[2:],
    }


def browse_ctx(compartment_id: SafeSlug, vol, path: SafeAbsPath, target: SafeAbsPath) -> dict:
    """Build template context for the volume browser.

    *target* must be a ``SafeAbsPath`` already validated via
    ``resolve_safe_path`` at the call site — the branded type proves
    containment within the volume root.
    """
    base = os.path.realpath(vol.qm_host_path)
    safe_target = str(target)
    owner = f"qm-{compartment_id}"
    safe_owner = SafeStr.of(owner, "browse_owner")

    # List directory via host helper (handles non-root privilege).
    names = host.listdir(SafeAbsPath.of(safe_target, "browse_target"), safe_owner)

    # Pre-stat all entries so we can sort dirs-first and collect metadata
    # in a single pass without repeated subprocess calls per entry.
    entry_stats: list[tuple[str, str, dict | None]] = []
    for name in names:
        try:
            entry_rel = os.path.relpath(os.path.join(safe_target, name), base)
            full = resolve_safe_path(base, entry_rel)
        except ValueError:
            continue
        safe_full = SafeAbsPath.of(full, "browse_entry")
        st = host.stat_entry(safe_full, safe_owner)
        entry_stats.append((name, full, st))

    # Sort: directories first, then case-insensitive alphabetical.
    entry_stats.sort(key=lambda e: (not (e[2] or {}).get("is_dir", False), e[0].lower()))

    entries = []
    for name, full, st in entry_stats:
        is_dir = (st or {}).get("is_dir", False)
        size = None if is_dir or st is None else st.get("size")
        entries.append(
            {
                "name": name,
                "type": "dir" if is_dir else "file",
                "size_fmt": "" if size is None else fmt_bytes(size),
                "is_text": (not is_dir) and is_text(full, owner),
                "mode": mode_bits(full, owner),
                "selinux_type": get_file_context_type(SafeAbsPath.of(full, "list_files")),
            }
        )
    rel = "/" + os.path.relpath(safe_target, base).replace("\\", "/")
    if rel == "/.":
        rel = "/"
    parent = str(PurePosixPath(rel).parent) if rel != "/" else None
    return {
        "compartment_id": compartment_id,
        "volume": vol,
        "path": rel,
        "parent": parent,
        "entries": entries,
    }

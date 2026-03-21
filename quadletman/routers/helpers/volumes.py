"""Volume browser helpers."""

import os
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ...i18n import gettext as _t
from ...models.sanitized import SafeAbsPath, SafeSlug, SafeStr, resolve_safe_path
from ...services import compartment_manager
from ...services.selinux import get_file_context_type


async def get_vol(db: AsyncSession, compartment_id: SafeSlug, volume_id: SafeStr):
    """Look up a single volume by compartment + volume ID, or raise 404."""
    vols = await compartment_manager.list_volumes(db, compartment_id)
    for v in vols:
        if v.id == volume_id:
            return v
    raise HTTPException(404, _t("Volume not found"))


def is_text(path: str, limit: int = 8192) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\x00" not in f.read(limit)
    except Exception:
        return False


def fmt_size(n: int) -> str:
    for unit, thresh in [("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)]:
        if n >= thresh:
            return f"{n / thresh:.1f} {unit}"
    return f"{n} B"


def mode_bits(full: str) -> dict:
    """Return rwx bits for owner/group/other as booleans."""
    try:
        m = os.stat(full).st_mode
    except OSError:
        return {
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


def browse_ctx(compartment_id: SafeSlug, vol, path: SafeStr, target: str) -> dict:
    """Build template context for the volume browser.

    *target* is expected to point somewhere within ``vol.host_path``; we
    defensively re-validate this here so that even if callers pass an
    untrusted path, browsing cannot escape the volume root.
    """
    base = os.path.realpath(vol.host_path)
    # Normalise *target* and ensure it is contained within the trusted base.
    safe_target = os.path.realpath(target)
    if safe_target != base and not safe_target.startswith(base + os.sep):
        raise HTTPException(400, _t("Invalid path"))

    entries = []
    for name in sorted(
        os.listdir(safe_target),
        key=lambda n: (not os.path.isdir(os.path.join(safe_target, n)), n.lower()),
    ):
        full = os.path.join(safe_target, name)
        is_dir = os.path.isdir(full)
        try:
            size = None if is_dir else os.path.getsize(full)
        # Derive a relative component for each entry from the trusted base,
        # then resolve it via ``resolve_safe_path`` so that any attempt to
        # escape the volume root via symlinks or ``..`` segments is rejected.
        try:
            entry_rel = os.path.relpath(os.path.join(safe_target, name), base)
            full = resolve_safe_path(base, entry_rel)
        except ValueError:
            # Skip any entry that cannot be resolved safely within the volume.
            continue
            size = None
        entries.append(
            {
                "name": name,
                "type": "dir" if is_dir else "file",
                "size_fmt": "" if size is None else fmt_size(size),
                "is_text": (not is_dir) and is_text(full),
                "mode": mode_bits(full),
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

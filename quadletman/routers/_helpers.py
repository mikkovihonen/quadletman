"""Shared helpers used across all domain routers.

Kept in a dedicated module to avoid the circular-import that would arise if
sub-routers imported from ``api.py`` while ``api.py`` imports the sub-routers.
"""

import asyncio
import json
import os
import re

import aiosqlite
from fastapi import Depends, HTTPException, Request

from ..database import get_db
from ..i18n import gettext as _t
from ..models.sanitized import SafeSlug
from ..services import compartment_manager, metrics, user_manager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum size for file uploads (archive restore + single file upload).
_MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MiB

# Environment files are tiny — 64 KiB is generous.
_MAX_ENVFILE_BYTES = 64 * 1024

# Allowed exec_user values for the terminal WebSocket: "root" or a non-negative integer UID.
_EXEC_USER_RE = re.compile(r"^(root|\d+)$")

# ---------------------------------------------------------------------------
# HTMX detection
# ---------------------------------------------------------------------------


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _fmt_bytes(b: int) -> str:
    if b >= 1_073_741_824:
        return f"{b / 1_073_741_824:.1f} GB"
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


async def _get_vol_sizes(compartment_id: SafeSlug, volumes) -> dict[str, str]:
    """Compute formatted sizes for all host-backed volumes concurrently."""
    host_vols = [v for v in volumes if not v.use_quadlet]
    if not host_vols:
        return {}
    loop = asyncio.get_event_loop()
    sizes = await asyncio.gather(
        *[
            loop.run_in_executor(
                None,
                metrics._dir_size,
                os.path.join(metrics._VOLUMES_BASE, compartment_id, v.name),
            )
            for v in host_vols
        ]
    )
    return {v.name: _fmt_bytes(s) for v, s in zip(host_vols, sizes, strict=False)}


# ---------------------------------------------------------------------------
# HTMX response helpers
# ---------------------------------------------------------------------------


async def run_blocking(fn, *args):
    """Run a blocking function in the default thread-pool executor."""
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


def _toast_trigger(message: str, *, error: bool = False) -> dict[str, str]:
    """Return an HX-Trigger header dict for a showToast notification."""
    return {
        "HX-Trigger": json.dumps(
            {"showToast": message, "toastType": "error" if error else "success"}
        )
    }


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


async def _require_compartment(
    compartment_id: SafeSlug,
    db: aiosqlite.Connection = Depends(get_db),
):
    """FastAPI dependency — raises 404 if the compartment does not exist."""
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    return comp


# ---------------------------------------------------------------------------
# Template context helpers
# ---------------------------------------------------------------------------


def _comp_ctx(request: Request, comp) -> dict:
    """Base template context for compartment_detail.html, including service user info."""
    net_drivers, vol_drivers = user_manager.get_compartment_drivers(comp.id)
    vol_mounts: dict[str, list[str]] = {}
    for c in comp.containers:
        for vm in c.volumes:
            vol_mounts.setdefault(vm.volume_id, []).append(c.name)
    return {
        "compartment": comp,
        "service_user_info": user_manager.get_user_info(comp.id),
        "helper_users": user_manager.list_helper_users(comp.id),
        "net_drivers": net_drivers,
        "vol_drivers": vol_drivers,
        "vol_mounts": vol_mounts,
    }


def _status_dot_context(compartment_id: SafeSlug, statuses: list[dict], oob: bool = False) -> dict:
    """Compute template context for the status dot partial."""
    active = [s for s in statuses if s["active_state"] == "active"]
    failed = [s for s in statuses if s["active_state"] == "failed"]
    transitioning = [s for s in statuses if s["active_state"] in ("activating", "deactivating")]
    if not statuses:
        color = "bg-gray-600"
        title = "no units"
    elif failed:
        color = "bg-red-500"
        title = f"{len(failed)} failed"
    elif transitioning:
        color = "bg-yellow-400 animate-pulse"
        title = "transitioning"
    elif len(active) == len(statuses):
        color = "bg-green-500"
        title = "all running"
    elif active:
        color = "bg-yellow-500"
        title = f"{len(active)}/{len(statuses)} running"
    else:
        color = "bg-gray-500"
        title = "stopped"
    return {"compartment_id": compartment_id, "color": color, "title": title, "oob": oob}

"""Host settings, SELinux, registry logins, and events routes."""

import asyncio
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..db.orm import SystemEventRow
from ..models.sanitized import SafeSlug, SafeStr, enforce_model
from ..services import compartment_manager, host_settings, selinux_booleans, user_manager
from ._helpers import _is_htmx

logger = logging.getLogger(__name__)
router = APIRouter()

_AUDIT_LOG_PATH = Path("/var/log/quadletman/host.log")


def _read_audit_lines(limit: int) -> list[str]:
    """Read the last N lines from the host audit log file."""
    if not _AUDIT_LOG_PATH.is_file():
        return []
    with open(_AUDIT_LOG_PATH) as f:
        lines = f.readlines()
    return [line.rstrip() for line in lines[-limit:]]


def _read_journalctl_lines(limit: int) -> list[str]:
    """Read recent journalctl lines for the quadletman unit."""
    cmd = [
        "journalctl",
        "-u",
        "quadletman",
        f"-n{limit}",
        "--no-pager",
        "--output=short-iso",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return [f"[error: {exc}]"]
    if result.returncode != 0:
        return [f"[journalctl exited {result.returncode}: {result.stderr.strip()}]"]
    return [line for line in result.stdout.splitlines() if not line.startswith("-- ")]


@router.get("/api/compartments/{compartment_id}/registry-logins")
async def get_registry_logins(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: str = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404)
    logins = user_manager.list_registry_logins(compartment_id)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/registry_logins.html",
        {"compartment_id": compartment_id, "logins": logins},
    )


@router.post("/api/compartments/{compartment_id}/registry-login")
async def post_registry_login(
    request: Request,
    compartment_id: SafeSlug,
    registry: SafeStr = Form(...),
    username: SafeStr = Form(...),
    password: SafeStr = Form(...),
    db: AsyncSession = Depends(get_db),
    user: str = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404)
    try:
        loop = __import__("asyncio").get_event_loop()
        await loop.run_in_executor(
            None,
            user_manager.registry_login,
            compartment_id,
            registry,
            username,
            password,
        )
    except RuntimeError as exc:
        logins = user_manager.list_registry_logins(compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/registry_logins.html",
            {"compartment_id": compartment_id, "logins": logins, "error": str(exc)},
        )
    logins = user_manager.list_registry_logins(compartment_id)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/registry_logins.html",
        {"compartment_id": compartment_id, "logins": logins},
    )


@router.post("/api/compartments/{compartment_id}/registry-logout")
async def post_registry_logout(
    request: Request,
    compartment_id: SafeSlug,
    registry: SafeStr = Form(...),
    db: AsyncSession = Depends(get_db),
    user: str = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404)
    try:
        loop = __import__("asyncio").get_event_loop()
        await loop.run_in_executor(
            None,
            user_manager.registry_logout,
            compartment_id,
            registry,
        )
    except RuntimeError as exc:
        logins = user_manager.list_registry_logins(compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/registry_logins.html",
            {"compartment_id": compartment_id, "logins": logins, "error": str(exc)},
        )
    logins = user_manager.list_registry_logins(compartment_id)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/registry_logins.html",
        {"compartment_id": compartment_id, "logins": logins},
    )


@router.get("/api/events")
async def list_events(
    request: Request,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user: str = Depends(require_auth),
):
    result = await db.execute(
        select(SystemEventRow.__table__).order_by(SystemEventRow.created_at.desc()).limit(limit)
    )
    rows = result.mappings().all()
    events = [dict(r) for r in rows]
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/events.html",
            {"events": events},
        )
    return events


@router.get("/api/events/systemd")
async def events_systemd(
    request: Request,
    limit: int = 200,
    user: str = Depends(require_auth),
):
    lines = await asyncio.get_event_loop().run_in_executor(None, _read_journalctl_lines, limit)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/events_log.html",
        {
            "lines": lines,
            "empty_msg": "No log entries. — quadletman may not be running as a systemd service, or the unit has no recent activity.",
        },
    )


@router.get("/api/events/audit")
async def events_audit(
    request: Request,
    limit: int = 500,
    user: str = Depends(require_auth),
):
    lines = await asyncio.get_event_loop().run_in_executor(None, _read_audit_lines, limit)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/events_log.html",
        {
            "lines": lines,
            "empty_msg": "No log entries. — /var/log/quadletman/host.log does not exist or is empty. — This file is created when quadletman runs as a systemd service.",
        },
    )


@router.get("/api/host-settings")
async def get_host_settings(user: str = Depends(require_auth)):
    entries = await asyncio.get_event_loop().run_in_executor(None, host_settings.read_all)
    return [
        {
            "key": e.key,
            "value": e.value,
            "category": e.category,
            "description": e.description,
            "value_type": e.value_type,
            "min_val": e.min_val,
            "max_val": e.max_val,
            "value_parts": e.value_parts,
        }
        for e in entries
    ]


@enforce_model
class _HostSettingUpdate(BaseModel):
    key: SafeStr
    value: SafeStr


@router.post("/api/host-settings")
async def set_host_setting(
    body: _HostSettingUpdate,
    user: str = Depends(require_auth),
):
    try:
        await host_settings.apply(body.key, body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"ok": True}


@router.get("/api/host-settings-partial")
async def host_settings_partial(request: Request, user: str = Depends(require_auth)):
    entries = await asyncio.get_event_loop().run_in_executor(None, host_settings.read_all)
    # Group by category preserving order
    categories: dict[str, list] = {}
    for entry in entries:
        categories.setdefault(entry.category, []).append(entry)

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/host_settings.html",
        {"categories": categories},
    )


@router.get("/api/selinux-booleans-partial")
async def selinux_booleans_partial(request: Request, user: str = Depends(require_auth)):
    bool_entries = await selinux_booleans.read_all()
    bool_categories: dict[str, list] = {}
    if bool_entries is not None:
        for b in bool_entries:
            bool_categories.setdefault(b.category, []).append(b)

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/selinux_booleans.html",
        {"selinux_active": bool_entries is not None, "selinux_categories": bool_categories},
    )


@enforce_model
class _BooleanUpdate(BaseModel):
    name: SafeStr
    enabled: bool


@router.post("/api/selinux-booleans")
async def set_selinux_boolean(
    body: _BooleanUpdate,
    user: str = Depends(require_auth),
):
    try:
        await selinux_booleans.set_boolean(body.name, body.enabled)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

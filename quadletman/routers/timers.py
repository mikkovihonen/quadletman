"""Scheduled task (timer) routes."""

import asyncio
import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from ..auth import require_auth
from ..config import TEMPLATES as _TEMPLATES
from ..database import get_db
from ..i18n import gettext as _t
from ..models import TimerCreate
from ..models.sanitized import SafeSlug, SafeStr
from ..services import compartment_manager, systemd_manager
from ._helpers import _is_htmx, _require_compartment, _toast_trigger

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/compartments/{compartment_id}/timers")
async def list_timers(
    request: Request,
    compartment_id: SafeSlug,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    timers = await compartment_manager.list_timers(db, compartment_id)
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/timers.html",
            {"compartment": comp, "timers": timers},
        )
    return [t.model_dump() for t in timers]


@router.post("/api/compartments/{compartment_id}/timers", status_code=status.HTTP_201_CREATED)
async def create_timer(
    request: Request,
    compartment_id: SafeSlug,
    data: TimerCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    if not data.on_calendar and not data.on_boot_sec:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            _t("At least one of on_calendar or on_boot_sec is required"),
        )
    try:
        timer = await compartment_manager.create_timer(db, compartment_id, data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        timers = await compartment_manager.list_timers(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/timers.html",
            {"compartment": comp, "timers": timers},
            headers=_toast_trigger(_t("Timer created")),
        )
    return timer.model_dump()


@router.delete(
    "/api/compartments/{compartment_id}/timers/{timer_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_timer(
    request: Request,
    compartment_id: SafeSlug,
    timer_id: SafeStr,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    await compartment_manager.delete_timer(db, compartment_id, timer_id)
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        timers = await compartment_manager.list_timers(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/timers.html",
            {"compartment": comp, "timers": timers},
            headers=_toast_trigger(_t("Timer deleted")),
        )


@router.get("/api/compartments/{compartment_id}/timers/{timer_id}/status")
async def timer_status(
    compartment_id: SafeSlug,
    timer_id: SafeStr,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
    _: object = Depends(_require_compartment),
) -> JSONResponse:
    """Return last-run / next-run status for a single timer from systemd (Feature 12)."""
    timers = await compartment_manager.list_timers(db, compartment_id)
    timer = next((t for t in timers if t.id == timer_id), None)
    if timer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Timer not found"))
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(
        None, systemd_manager.get_timer_status, compartment_id, SafeStr.of(timer.name, "timer_name")
    )
    return JSONResponse(info)

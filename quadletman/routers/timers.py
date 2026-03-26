"""Scheduled task (timer) routes."""

import asyncio
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..i18n import gettext as _t
from ..models import TimerCreate
from ..models.sanitized import (
    SafeCalendarSpec,
    SafeFormBool,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeTimeDuration,
    SafeUsername,
    SafeUUID,
)
from ..services import compartment_manager, systemd_manager
from ..services.compartment_manager import ServiceCondition
from .helpers import is_htmx, require_auth, require_compartment, toast_trigger

logger = logging.getLogger(__name__)
router = APIRouter()


def _timers_ctx(comp, timers, compartment_id):
    """Build shared template context for the timers partial."""
    auto_update_enabled = systemd_manager.get_auto_update_timer_enabled(compartment_id)
    return {"compartment": comp, "timers": timers, "auto_update_enabled": auto_update_enabled}


@router.get("/api/compartments/{compartment_id}/timers")
async def list_timers(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    timers = await compartment_manager.list_timers(db, compartment_id)
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/timers.html",
            _timers_ctx(comp, timers, compartment_id),
        )
    return [t.model_dump() for t in timers]


@router.post("/api/compartments/{compartment_id}/timers", status_code=status.HTTP_201_CREATED)
async def create_timer(
    request: Request,
    compartment_id: SafeSlug,
    name: SafeResourceName = Form(...),
    container_id: SafeUUID = Form(...),
    on_calendar: SafeCalendarSpec = Form(""),
    on_boot_sec: SafeTimeDuration = Form(""),
    random_delay_sec: SafeTimeDuration = Form(""),
    persistent: SafeFormBool = Form(""),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    data = TimerCreate(
        qm_name=name,
        qm_container_id=container_id,
        on_calendar=on_calendar,
        on_boot_sec=on_boot_sec,
        random_delay_sec=random_delay_sec,
        persistent=persistent == "true",
    )
    if not data.on_calendar and not data.on_boot_sec:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            _t("At least one of on_calendar or on_boot_sec is required"),
        )
    try:
        timer = await compartment_manager.create_timer(db, compartment_id, data)
    except ValueError as exc:
        logger.warning("Timer creation failed: %s", exc)
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, _t("A timer named '%(name)s' already exists") % {"name": name}
        ) from exc
    except Exception as exc:
        if isinstance(exc, ServiceCondition):
            raise
        logger.exception("Failed to create timer")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, _t("Internal server error")
        ) from exc

    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        timers = await compartment_manager.list_timers(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/timers.html",
            _timers_ctx(comp, timers, compartment_id),
            headers=toast_trigger(_t("Timer created")),
        )
    return timer.model_dump()


@router.delete(
    "/api/compartments/{compartment_id}/timers/{timer_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_timer(
    request: Request,
    compartment_id: SafeSlug,
    timer_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    await compartment_manager.delete_timer(db, compartment_id, timer_id)
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        timers = await compartment_manager.list_timers(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/timers.html",
            _timers_ctx(comp, timers, compartment_id),
            headers=toast_trigger(_t("Timer deleted")),
        )


@router.post("/api/compartments/{compartment_id}/auto-update")
async def toggle_auto_update(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    """Toggle the podman-auto-update.timer for a compartment user."""
    loop = asyncio.get_event_loop()
    enabled = await loop.run_in_executor(
        None, systemd_manager.get_auto_update_timer_enabled, compartment_id
    )
    if enabled:
        await loop.run_in_executor(None, systemd_manager.disable_auto_update_timer, compartment_id)
    else:
        await loop.run_in_executor(None, systemd_manager.enable_auto_update_timer, compartment_id)
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        timers = await compartment_manager.list_timers(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/timers.html",
            _timers_ctx(comp, timers, compartment_id),
            headers=toast_trigger(
                _t("Auto-update enabled") if not enabled else _t("Auto-update disabled")
            ),
        )
    return {"enabled": not enabled}


@router.get("/api/compartments/{compartment_id}/timers/{timer_id}/status")
async def timer_status(
    compartment_id: SafeSlug,
    timer_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
) -> JSONResponse:
    """Return last-run / next-run status for a single timer from systemd (Feature 12)."""
    timers = await compartment_manager.list_timers(db, compartment_id)
    timer = next((t for t in timers if t.id == timer_id), None)
    if timer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Timer not found"))
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(
        None,
        systemd_manager.get_timer_status,
        compartment_id,
        SafeStr.of(timer.qm_name, "timer_name"),
    )
    return JSONResponse(info)

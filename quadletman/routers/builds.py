"""Build unit routes — CRUD for .build Quadlet units."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..i18n import gettext as _t
from ..models import BuildUnitCreate
from ..models.sanitized import SafeSlug, SafeUsername, SafeUUID
from ..models.version_span import validate_version_spans
from ..podman_version import get_features
from ..services import compartment_manager
from .helpers import comp_ctx, is_htmx, toast_trigger

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/api/compartments/{compartment_id}/build-units", status_code=201)
async def add_build_unit(
    request: Request,
    compartment_id: SafeSlug,
    data: BuildUnitCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    features = get_features()
    if not features.build_units:
        raise HTTPException(
            status_code=400,
            detail=_t("Requires Podman 5.2+ (detected: %(v)s)") % {"v": features.version_str},
        )
    validate_version_spans(data, features.version, features.version_str)
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    try:
        bu = await compartment_manager.add_build_unit(db, compartment_id, data)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=_t("A build unit named '%(name)s' already exists") % {"name": data.name},
        ) from exc
    except Exception as exc:
        logger.error("Failed to add build unit: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to add build unit")) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Build unit added"),
        )
    return bu.model_dump()


@router.put("/api/compartments/{compartment_id}/build-units/{build_unit_id}")
async def update_build_unit(
    request: Request,
    compartment_id: SafeSlug,
    build_unit_id: SafeUUID,
    data: BuildUnitCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    features = get_features()
    validate_version_spans(data, features.version, features.version_str)
    try:
        bu = await compartment_manager.update_build_unit(db, compartment_id, build_unit_id, data)
    except Exception as exc:
        logger.error("Failed to update build unit: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to update build unit")) from exc
    if bu is None:
        raise HTTPException(status_code=404, detail=_t("Build unit not found"))
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Build unit updated"),
        )
    return bu.model_dump()


@router.delete("/api/compartments/{compartment_id}/build-units/{build_unit_id}", status_code=204)
async def delete_build_unit(
    request: Request,
    compartment_id: SafeSlug,
    build_unit_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    try:
        await compartment_manager.delete_build_unit(db, compartment_id, build_unit_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Build unit removed"),
        )


@router.get("/api/compartments/{compartment_id}/build-units/form")
async def build_unit_create_form(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/build_form.html",
        {"compartment": comp, "build_unit": None},
    )


@router.get("/api/compartments/{compartment_id}/build-units/{build_unit_id}/form")
async def build_unit_edit_form(
    request: Request,
    compartment_id: SafeSlug,
    build_unit_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    build_unit = next((bu for bu in comp.build_units if bu.id == build_unit_id), None)
    if build_unit is None:
        raise HTTPException(status_code=404, detail=_t("Build unit not found"))
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/build_form.html",
        {"compartment": comp, "build_unit": build_unit},
    )

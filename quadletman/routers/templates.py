"""Service template routes."""

import json
import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth import require_auth
from ..config import TEMPLATES as _TEMPLATES
from ..database import get_db
from ..i18n import gettext as _t
from ..models import TemplateCreate, TemplateInstantiate
from ..models.sanitized import SafeStr
from ..services import compartment_manager
from ._helpers import _is_htmx, _toast_trigger

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/templates")
async def list_templates(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    templates = await compartment_manager.list_templates(db)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/templates.html",
            {"templates": templates},
        )
    return [t.model_dump() for t in templates]


@router.post("/api/templates", status_code=status.HTTP_201_CREATED)
async def save_template(
    request: Request,
    data: TemplateCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    try:
        template = await compartment_manager.save_template(db, data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    if _is_htmx(request):
        templates = await compartment_manager.list_templates(db)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/templates.html",
            {"templates": templates},
            headers=_toast_trigger(_t("Template saved")),
        )
    return template.model_dump()


@router.delete("/api/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: SafeStr,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    await compartment_manager.delete_template(db, template_id)


@router.post("/api/compartments/from-template/{template_id}", status_code=status.HTTP_201_CREATED)
async def create_from_template(
    request: Request,
    template_id: SafeStr,
    data: TemplateInstantiate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    # Read template config to detect stripped secrets before creating the compartment
    async with db.execute("SELECT config_json FROM templates WHERE id = ?", (template_id,)) as cur:
        trow = await cur.fetchone()
    if trow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Template not found"))
    config = json.loads(trow["config_json"])
    stripped_count = sum(len(cd.get("secrets", [])) for cd in config.get("containers", []))

    try:
        comp = await compartment_manager.create_compartment_from_template(
            db, template_id, data.compartment_id, data.description
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to instantiate template %s: %s", template_id, exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    msg = _t("Compartment created from template")
    if stripped_count:
        msg += ". " + _t("%(n)d secret reference(s) cleared — re-add secrets manually.") % {
            "n": stripped_count
        }

    if _is_htmx(request):
        services = await compartment_manager.list_compartments(db)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/dashboard.html",
            {"services": services, "user": user},
            headers=_toast_trigger(msg),
        )
    result = comp.model_dump()
    if stripped_count:
        result["warnings"] = [
            _t("%(n)d secret reference(s) cleared — re-add secrets manually.")
            % {"n": stripped_count}
        ]
    return result

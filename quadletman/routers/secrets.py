"""Secrets management routes."""

import asyncio
import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth import require_auth
from ..database import get_db
from ..i18n import gettext as _t
from ..models import SecretCreate
from ..services import compartment_manager, secrets_manager
from ..templates_config import TEMPLATES as _TEMPLATES
from ._helpers import _is_htmx, _require_compartment, _toast_trigger

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/compartments/{compartment_id}/secrets")
async def list_secrets(
    request: Request,
    compartment_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    secrets = await compartment_manager.list_secrets(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/secrets.html",
            {"compartment_id": compartment_id, "secrets": secrets},
        )
    return [s.model_dump() for s in secrets]


@router.post("/api/compartments/{compartment_id}/secrets", status_code=status.HTTP_201_CREATED)
async def add_secret(
    request: Request,
    compartment_id: str,
    data: SecretCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    # Create in podman store first, then register in DB
    # The SecretCreate model only has name; content is passed via /secrets/create instead.
    raise HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Use /api/compartments/{id}/secrets/create instead",
    )


@router.post(
    "/api/compartments/{compartment_id}/secrets/create", status_code=status.HTTP_201_CREATED
)
async def create_secret(
    request: Request,
    compartment_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    """Create a new secret. Body: {name, value}."""
    body = await request.json()
    name = body.get("name", "").strip()
    value = body.get("value", "")
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _t("Secret name is required"))
    if not value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _t("Secret value is required"))

    # Validate name via model
    try:
        data = SecretCreate(name=name)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None, secrets_manager.create_podman_secret, compartment_id, data.name, value
        )
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    secret = await compartment_manager.add_secret(db, compartment_id, data)

    if _is_htmx(request):
        secrets = await compartment_manager.list_secrets(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/secrets.html",
            {"compartment_id": compartment_id, "secrets": secrets},
            headers=_toast_trigger(_t("Secret created")),
        )
    return secret.model_dump()


@router.delete(
    "/api/compartments/{compartment_id}/secrets/{secret_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_secret(
    request: Request,
    compartment_id: str,
    secret_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    await compartment_manager.delete_secret(db, compartment_id, secret_id)
    if _is_htmx(request):
        secrets = await compartment_manager.list_secrets(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/secrets.html",
            {"compartment_id": compartment_id, "secrets": secrets},
            headers=_toast_trigger(_t("Secret deleted")),
        )

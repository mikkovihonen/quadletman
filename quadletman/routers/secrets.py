"""Secrets management routes."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..db.orm import SecretRow
from ..i18n import gettext as _t
from ..models import SecretCreate
from ..models.sanitized import (
    SafeMultilineStr,
    SafeSecretName,
    SafeSlug,
    SafeUsername,
    SafeUUID,
)
from ..services import compartment_manager, secrets_manager
from .helpers import is_htmx, require_compartment, toast_trigger

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/compartments/{compartment_id}/secrets")
async def list_secrets(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    secrets = await compartment_manager.list_secrets(db, compartment_id)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/secrets.html",
            {"compartment_id": compartment_id, "secrets": secrets},
        )
    return [s.model_dump() for s in secrets]


@router.post("/api/compartments/{compartment_id}/secrets", status_code=status.HTTP_201_CREATED)
async def add_secret(
    request: Request,
    compartment_id: SafeSlug,
    data: SecretCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
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
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
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
        logger.warning("Invalid secret name: %s", exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    safe_value = SafeMultilineStr.of(value, "secret_value")
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            secrets_manager.create_podman_secret,
            compartment_id,
            data.name,
            safe_value,
        )
    except RuntimeError as exc:
        logger.exception("Failed to create podman secret")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error") from exc

    try:
        secret = await compartment_manager.add_secret(db, compartment_id, data)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            _t("A secret named '%(name)s' already exists") % {"name": data.name},
        ) from exc

    if is_htmx(request):
        secrets = await compartment_manager.list_secrets(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/secrets.html",
            {"compartment_id": compartment_id, "secrets": secrets},
            headers=toast_trigger(_t("Secret created")),
        )
    return secret.model_dump()


@router.put("/api/compartments/{compartment_id}/secrets/{secret_id}")
async def overwrite_secret(
    request: Request,
    compartment_id: SafeSlug,
    secret_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    """Overwrite an existing secret's value (delete + recreate in podman store)."""
    body = await request.json()
    value = body.get("value", "")
    if not value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _t("Secret value is required"))

    result = await db.execute(
        select(SecretRow.name).where(
            SecretRow.id == secret_id, SecretRow.compartment_id == compartment_id
        ),
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Secret not found"))

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            secrets_manager.overwrite_podman_secret,
            compartment_id,
            SafeSecretName.of(row["name"], "name"),
            SafeMultilineStr.of(value, "secret_value"),
        )
    except RuntimeError as exc:
        logger.exception("Failed to overwrite podman secret")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error") from exc

    if is_htmx(request):
        secrets = await compartment_manager.list_secrets(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/secrets.html",
            {"compartment_id": compartment_id, "secrets": secrets},
            headers=toast_trigger(_t("Secret updated")),
        )
    return {"id": secret_id}


@router.delete(
    "/api/compartments/{compartment_id}/secrets/{secret_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_secret(
    request: Request,
    compartment_id: SafeSlug,
    secret_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    await compartment_manager.delete_secret(db, compartment_id, secret_id)
    if is_htmx(request):
        secrets = await compartment_manager.list_secrets(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/secrets.html",
            {"compartment_id": compartment_id, "secrets": secrets},
            headers=toast_trigger(_t("Secret deleted")),
        )

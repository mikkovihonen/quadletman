"""Generic config file upload/preview/delete routes.

Supports uploading configuration files for any Quadlet path field that is
registered in ``UPLOADABLE_FIELDS``.  Files are stored under
``/home/qm-{id}/conf/{resource_type}/{resource_name}/{field_name}{ext}``.
"""

import logging
import os

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.engine import get_db
from ..i18n import gettext as _t
from ..models.sanitized import (
    SafeAbsPath,
    SafeMultilineStr,
    SafeSlug,
    SafeStr,
    SafeUsername,
    SafeUUID,
    resolve_safe_path,
)
from ..services import host, user_manager
from .helpers import (
    MAX_ENVFILE_BYTES,
    UPLOADABLE_FIELDS,
    lookup_resource,
    require_auth,
    require_compartment,
    run_blocking,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _validate_field(resource_type: str, field_name: str):
    """Return UploadableFieldMeta or raise 400."""
    fields = UPLOADABLE_FIELDS.get(resource_type)
    if not fields:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            _t("Invalid resource type: %(t)s") % {"t": resource_type},
        )
    meta = fields.get(field_name)
    if not meta:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            _t("Field %(f)s does not support file upload for %(t)s")
            % {"f": field_name, "t": resource_type},
        )
    return meta


@router.post(
    "/api/compartments/{compartment_id}/{resource_type}/{resource_id}/configfile/{field_name}"
)
async def upload_config_file(
    compartment_id: SafeSlug,
    resource_type: SafeStr,
    resource_id: SafeUUID,
    field_name: SafeStr,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _user: SafeUsername = Depends(require_auth),
    comp: object = Depends(require_compartment),
) -> JSONResponse:
    meta = _validate_field(resource_type, field_name)
    resource = lookup_resource(comp, resource_type, str(resource_id))
    if resource is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Resource not found"))

    raw = await file.read(MAX_ENVFILE_BYTES + 1)
    if len(raw) > MAX_ENVFILE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            _t("Config file exceeds %(n)s KiB limit") % {"n": MAX_ENVFILE_BYTES // 1024},
        )
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, _t("Config file must be valid UTF-8")
        ) from exc

    if meta.validate:
        try:
            meta.validate(content)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    safe_content = SafeMultilineStr.of(content, "config_content")
    safe_rt = SafeStr.of(resource_type, "resource_type")
    safe_fn = SafeStr.of(field_name, "field_name")
    safe_ext = SafeStr.of(meta.ext, "file_ext") if meta.ext else SafeStr.trusted("", "default")
    dest = await run_blocking(
        user_manager.write_config_file,
        compartment_id,
        safe_rt,
        resource.qm_name,
        safe_fn,
        safe_content,
        safe_ext,
    )
    return JSONResponse({"path": dest})


@router.get("/api/compartments/{compartment_id}/configfile")
async def preview_config_file(
    compartment_id: SafeSlug,
    path: SafeAbsPath = Query(...),
    preview: SafeStr = Query(SafeStr.trusted("raw", "default")),
    db: AsyncSession = Depends(get_db),
    _user: SafeUsername = Depends(require_auth),
) -> JSONResponse:
    """Preview a config file's contents.

    ``preview=keyvalue`` parses KEY=value lines (for .env files).
    ``preview=raw`` returns the raw text content.
    """
    try:
        home = await run_blocking(user_manager.get_home, compartment_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Service user not found")) from exc

    try:
        real_path = resolve_safe_path(home, path, absolute=True)
    except ValueError as exc:
        logger.warning("Config file path validation failed: %s", exc)
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    safe_real = SafeAbsPath.of(real_path, "config_preview_path")
    owner = SafeStr.of(f"qm-{compartment_id}", "owner")
    if not await run_blocking(host.path_exists, safe_real, owner):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("File not found"))

    content = await run_blocking(host.read_text, safe_real, owner)
    if content is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, _t("Could not read file"))

    if str(preview) == "keyvalue":
        lines = []
        for raw_line in content.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key, _, value = stripped.partition("=")
            lines.append({"key": key.strip(), "value": value})
        return JSONResponse({"lines": lines})
    return JSONResponse({"raw": content})


@router.delete(
    "/api/compartments/{compartment_id}/{resource_type}/{resource_id}/configfile/{field_name}"
)
async def delete_config_file(
    compartment_id: SafeSlug,
    resource_type: SafeStr,
    resource_id: SafeUUID,
    field_name: SafeStr,
    db: AsyncSession = Depends(get_db),
    _user: SafeUsername = Depends(require_auth),
    comp: object = Depends(require_compartment),
) -> JSONResponse:
    meta = _validate_field(resource_type, field_name)
    resource = lookup_resource(comp, resource_type, str(resource_id))
    if resource is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Resource not found"))

    home = await run_blocking(user_manager.get_home, compartment_id)
    conf_path = os.path.join(
        home, "conf", str(resource_type), str(resource.qm_name), f"{field_name}{meta.ext}"
    )
    safe_path = SafeAbsPath.of(conf_path, "config_delete_path")

    try:
        await run_blocking(user_manager.delete_config_file, compartment_id, safe_path)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, _t("Path is outside the service user home directory")
        ) from exc
    return JSONResponse({"ok": True})

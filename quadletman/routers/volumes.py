"""Volume management routes."""

import asyncio
import io
import logging
import os
import re
import shutil
import urllib.parse
import zipfile
from contextlib import suppress
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..i18n import gettext as _t
from ..models import VolumeCreate, VolumeUpdate
from ..models.sanitized import (
    SafeAbsPath,
    SafeMultilineStr,
    SafeOctalMode,
    SafeSlug,
    SafeStr,
    SafeUsername,
    SafeUUID,
    log_safe,
    resolve_safe_path,
)
from ..models.version_span import validate_version_spans
from ..security.auth import require_auth
from ..services import compartment_manager, user_manager
from ..services.archive import extract_archive
from ..services.compartment_manager import ServiceCondition
from ..services.selinux import apply_context, relabel
from .helpers import (
    MAX_UPLOAD_BYTES,
    browse_ctx,
    comp_ctx,
    get_vol,
    is_htmx,
    is_text,
    require_compartment,
    toast_trigger,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/compartments/{compartment_id}/volumes/{volume_name}/size")
async def get_volume_size(
    request: Request,
    compartment_id: SafeSlug,
    volume_name: SafeSlug,
    user: SafeUsername = Depends(require_auth),
):
    from ..services import metrics
    from ..utils import dir_size

    loop = asyncio.get_event_loop()
    path = os.path.join(metrics._VOLUMES_BASE, compartment_id, volume_name)
    size = await loop.run_in_executor(None, dir_size, path)
    from .helpers import fmt_bytes

    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/volume_size.html",
            {"size_str": fmt_bytes(size)},
        )
    return {"bytes": size}


@router.post("/api/compartments/{compartment_id}/volumes", status_code=status.HTTP_201_CREATED)
async def add_volume(
    request: Request,
    compartment_id: SafeSlug,
    data: VolumeCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    from ..podman_version import get_features

    features = get_features()
    validate_version_spans(data, features.version, features.version_str)
    try:
        volume = await compartment_manager.add_volume(db, compartment_id, data)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=_t("A volume named '%(name)s' already exists") % {"name": data.qm_name},
        ) from exc
    except Exception as exc:
        if isinstance(exc, ServiceCondition):
            raise
        logger.error("Failed to add volume: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to add volume")) from exc

    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger(_t("Volume created")),
        )
    return volume.model_dump()


@router.patch("/api/compartments/{compartment_id}/volumes/{volume_id}", status_code=200)
async def update_volume(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeUUID,
    data: VolumeUpdate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    try:
        await compartment_manager.update_volume_owner(
            db, compartment_id, volume_id, data.qm_owner_uid
        )
    except Exception as exc:
        if isinstance(exc, ServiceCondition):
            raise
        logger.error("Failed to update volume %s: %s", log_safe(volume_id), exc)
        raise HTTPException(status_code=500, detail=_t("Failed to update volume")) from exc
    comp = await compartment_manager.get_compartment(db, compartment_id)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/compartment_detail.html",
        await comp_ctx(request, comp),
        headers=toast_trigger(_t("Volume updated")),
    )


@router.delete("/api/compartments/{compartment_id}/volumes/{volume_id}", status_code=204)
async def delete_volume(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    try:
        await compartment_manager.delete_volume(db, compartment_id, volume_id)
    except ValueError as exc:
        logger.warning("Volume deletion conflict: %s", exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger(_t("Volume deleted")),
        )


@router.get("/api/compartments/{compartment_id}/volumes/form")
async def volume_create_form(
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
        "partials/volume_form.html",
        await comp_ctx(request, comp),
    )


@router.get("/api/compartments/{compartment_id}/volumes/{volume_id}/browse")
async def volume_browse(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeUUID,
    path: SafeAbsPath = SafeAbsPath.trusted("/", "default"),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    vol = await get_vol(db, compartment_id, volume_id)
    try:
        target = SafeAbsPath.of(resolve_safe_path(vol.qm_host_path, path), "browse_target")
    except ValueError as exc:
        logger.warning("Path validation failed: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not os.path.isdir(target):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Directory not found"))
    ctx = browse_ctx(compartment_id, vol, path, target)
    return _TEMPLATES.TemplateResponse(request, "partials/volume_browser.html", {**ctx})


@router.get("/api/compartments/{compartment_id}/volumes/{volume_id}/file")
async def volume_get_file(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeUUID,
    path: SafeAbsPath,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    vol = await get_vol(db, compartment_id, volume_id)
    try:
        target = resolve_safe_path(vol.qm_host_path, path)
    except ValueError as exc:
        logger.warning("Path validation failed: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # codeql[py/path-injection] target validated by resolve_safe_path() above
    try:
        if not os.path.isfile(target):
            if os.path.exists(target):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, _t("Not a file"))
            raise FileNotFoundError
        if not is_text(target):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, _t("Binary files cannot be edited as text")
            )
        with open(target) as _f:
            content = _f.read()
        is_new = False
    except FileNotFoundError:
        content = ""
        is_new = True
    dir_path = str(PurePosixPath(path).parent)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/volume_file_editor.html",
        {
            "compartment_id": compartment_id,
            "volume": vol,
            "path": path,
            "dir_path": dir_path,
            "content": content,
            "is_new": is_new,
        },
    )


@router.put("/api/compartments/{compartment_id}/volumes/{volume_id}/file")
async def volume_save_file(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeUUID,
    path: SafeAbsPath,
    content: SafeMultilineStr = Form(default=""),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    vol = await get_vol(db, compartment_id, volume_id)
    try:
        target = resolve_safe_path(vol.qm_host_path, path)
    except ValueError as exc:
        logger.warning("Path validation failed: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    os.makedirs(os.path.dirname(target), exist_ok=True)
    # 0o640: group-read needed for container service user
    # codeql[py/overly-permissive-file] intentional — group-read for container service user
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        raise
    safe_target = SafeAbsPath.of(target, "vol_file_target")
    user_manager.chown_to_service_user(compartment_id, safe_target)
    relabel(safe_target)
    dir_path = str(PurePosixPath(path).parent)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/volume_file_editor.html",
        {
            "compartment_id": compartment_id,
            "volume": vol,
            "path": path,
            "dir_path": dir_path,
            "content": content,
            "is_new": False,
        },
        headers=toast_trigger(_t("Saved")),
    )


@router.post("/api/compartments/{compartment_id}/volumes/{volume_id}/upload")
async def volume_upload(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeUUID,
    path: SafeAbsPath = SafeAbsPath.trusted("/", "default"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    vol = await get_vol(db, compartment_id, volume_id)
    try:
        target_dir = SafeAbsPath.of(resolve_safe_path(vol.qm_host_path, path), "upload_target")
    except ValueError as exc:
        logger.warning("Path validation failed: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not os.path.isdir(target_dir):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _t("Target is not a directory"))
    filename = re.sub(r"[^\w.\-]", "_", os.path.basename(file.filename or "upload"))
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _t("Empty filename"))
    dest = os.path.join(target_dir, filename)
    try:
        dest = resolve_safe_path(
            vol.qm_host_path, os.path.relpath(dest, os.path.realpath(vol.qm_host_path))
        )
    except ValueError as exc:
        logger.warning("Path validation failed: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            _t("File exceeds maximum upload size of %(n)s MiB")
            % {"n": MAX_UPLOAD_BYTES // (1024 * 1024)},
        )
    # 0o640: group-read needed for container service user
    # codeql[py/overly-permissive-file] intentional — group-read for container service user
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        raise
    safe_dest = SafeAbsPath.of(dest, "vol_upload_dest")
    user_manager.chown_to_service_user(compartment_id, safe_dest)
    relabel(safe_dest)
    ctx = browse_ctx(compartment_id, vol, path, target_dir)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/volume_browser.html",
        {**ctx},
        headers=toast_trigger(_t("Uploaded %(name)s") % {"name": filename}),
    )


@router.delete("/api/compartments/{compartment_id}/volumes/{volume_id}/file")
async def volume_delete_entry(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeUUID,
    path: SafeAbsPath,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    vol = await get_vol(db, compartment_id, volume_id)
    try:
        target = resolve_safe_path(vol.qm_host_path, path)
    except ValueError as exc:
        logger.warning("Path validation failed: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # codeql[py/path-injection] target validated by resolve_safe_path() above
    try:
        if os.path.isdir(target):
            logger.info(
                "User %s deleted directory %s in volume %s/%s",
                log_safe(user),
                log_safe(path),
                log_safe(compartment_id),
                log_safe(volume_id),
            )
            shutil.rmtree(target)
        else:
            logger.info(
                "User %s deleted file %s in volume %s/%s",
                log_safe(user),
                log_safe(path),
                log_safe(compartment_id),
                log_safe(volume_id),
            )
            os.unlink(target)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Not found")) from exc
    dir_path = SafeAbsPath.of(str(PurePosixPath(path).parent), "dir_path")
    try:
        target_dir = SafeAbsPath.of(resolve_safe_path(vol.qm_host_path, dir_path), "delete_browse")
    except ValueError:
        target_dir = SafeAbsPath.of(os.path.realpath(vol.qm_host_path), "vol_root")
    ctx = browse_ctx(compartment_id, vol, dir_path, target_dir)
    return _TEMPLATES.TemplateResponse(request, "partials/volume_browser.html", {**ctx})


@router.post("/api/compartments/{compartment_id}/volumes/{volume_id}/mkdir")
async def volume_mkdir(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeUUID,
    path: SafeAbsPath = Form(...),
    name: SafeStr = Form(...),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    vol = await get_vol(db, compartment_id, volume_id)
    new_rel = str(PurePosixPath(path) / name)
    try:
        target = resolve_safe_path(vol.qm_host_path, new_rel)
    except ValueError as exc:
        logger.warning("Path validation failed: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    os.makedirs(target, exist_ok=True)
    safe_target = SafeAbsPath.of(target, "mkdir_target")
    user_manager.chown_to_service_user(compartment_id, safe_target)
    relabel(safe_target)
    try:
        parent_target = SafeAbsPath.of(resolve_safe_path(vol.qm_host_path, path), "mkdir_browse")
    except ValueError:
        parent_target = SafeAbsPath.of(os.path.realpath(vol.qm_host_path), "vol_root")
    ctx = browse_ctx(compartment_id, vol, path, parent_target)
    return _TEMPLATES.TemplateResponse(request, "partials/volume_browser.html", {**ctx})


@router.patch("/api/compartments/{compartment_id}/volumes/{volume_id}/chmod")
async def volume_chmod(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeUUID,
    path: SafeAbsPath = Form(...),
    mode: SafeOctalMode = Form(...),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    """Change permissions of a single file or directory."""
    vol = await get_vol(db, compartment_id, volume_id)
    try:
        target = resolve_safe_path(vol.qm_host_path, path)
    except ValueError as exc:
        logger.warning("Path validation failed: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # codeql[py/path-injection] target validated by resolve_safe_path() above
    mode_int = int(mode, 8)
    try:
        os.chmod(target, mode_int)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Path not found")) from exc
    dir_path = SafeAbsPath.of(str(PurePosixPath(path).parent), "dir_path")
    try:
        dir_target = SafeAbsPath.of(resolve_safe_path(vol.qm_host_path, dir_path), "chmod_browse")
    except ValueError:
        dir_target = SafeAbsPath.of(os.path.realpath(vol.qm_host_path), "vol_root")
    ctx = browse_ctx(compartment_id, vol, dir_path, dir_target)
    return _TEMPLATES.TemplateResponse(request, "partials/volume_browser.html", {**ctx})


@router.get("/api/compartments/{compartment_id}/volumes/{volume_id}/archive")
async def volume_archive(
    compartment_id: SafeSlug,
    volume_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    """Download all volume files as a zip archive."""
    vol = await get_vol(db, compartment_id, volume_id)
    base = os.path.realpath(vol.qm_host_path)

    def _build_zip() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # followlinks=False (the default) prevents traversal via symlinks,
            # but os.walk still yields symlinked files in filenames when they
            # exist inside visited directories.  We explicitly skip any entry
            # whose realpath escapes the volume root so that a symlink pointing
            # outside the volume cannot leak host files.
            for dirpath, _dirnames, filenames in os.walk(base, followlinks=False):
                for fname in filenames:
                    abs_path = os.path.join(dirpath, fname)
                    real = os.path.realpath(abs_path)
                    if real != base and not real.startswith(base + os.sep):
                        logger.warning(
                            "Skipping symlink escaping volume root during archive: %s -> %s",
                            abs_path,
                            real,
                        )
                        continue
                    arcname = os.path.relpath(abs_path, base)
                    zf.write(abs_path, arcname)
        return buf.getvalue()

    data = await __import__("asyncio").get_event_loop().run_in_executor(None, _build_zip)
    filename = f"{compartment_id}-{vol.qm_name}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
        },
    )


@router.post("/api/compartments/{compartment_id}/volumes/{volume_id}/restore")
async def volume_restore(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeUUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    """Extract a zip or tar.gz archive into the volume root."""
    vol = await get_vol(db, compartment_id, volume_id)
    base = os.path.realpath(vol.qm_host_path)

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            _t("Archive exceeds maximum upload size of %(n)s MiB")
            % {"n": MAX_UPLOAD_BYTES // (1024 * 1024)},
        )
    fname = (file.filename or "").lower()

    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            extract_archive,
            data,
            SafeAbsPath.of(base, "vol.qm_host_path"),
            SafeStr.of(fname, "file.filename"),
        )
    except ValueError as exc:
        logger.warning(
            "Invalid archive for %s/%s: %s", log_safe(compartment_id), log_safe(volume_id), exc
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        if isinstance(exc, ServiceCondition):
            raise
        logger.warning(
            "Archive extraction failed for %s/%s: %s",
            log_safe(compartment_id),
            log_safe(volume_id),
            exc,
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _t("Failed to extract archive")) from exc

    safe_base = SafeAbsPath.of(base, "archive_base")
    user_manager.chown_to_service_user(compartment_id, safe_base)
    apply_context(safe_base, vol.qm_selinux_context)
    ctx = browse_ctx(compartment_id, vol, SafeAbsPath.trusted("/", "browse_root"), safe_base)
    return _TEMPLATES.TemplateResponse(request, "partials/volume_browser.html", {**ctx})

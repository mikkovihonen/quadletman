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

import aiosqlite
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel

from ..auth import require_auth
from ..config import TEMPLATES as _TEMPLATES
from ..database import get_db
from ..i18n import gettext as _t
from ..models import VolumeCreate
from ..models.sanitized import SafeAbsPath, SafeSlug, SafeStr, enforce_model
from ..services import compartment_manager, user_manager
from ..services.archive import extract_archive
from ..services.selinux import apply_context, get_file_context_type, relabel
from ._helpers import (
    _MAX_UPLOAD_BYTES,
    _comp_ctx,
    _is_htmx,
    _require_compartment,
    _toast_trigger,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_vol_path(host_path: str, rel: str) -> str:
    """Resolve rel relative to host_path, raising ValueError on traversal."""
    base = os.path.realpath(host_path)
    if not rel or rel in ("/", "."):
        return base
    target = os.path.realpath(os.path.join(base, rel.lstrip("/")))
    if target != base and not target.startswith(base + os.sep):
        raise ValueError("Path escapes volume directory")
    return target


def _is_text(path: str, limit: int = 8192) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\x00" not in f.read(limit)
    except Exception:
        return False


def _fmt_size(n: int) -> str:
    for unit, thresh in [("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)]:
        if n >= thresh:
            return f"{n / thresh:.1f} {unit}"
    return f"{n} B"


async def _get_vol(db: aiosqlite.Connection, compartment_id: SafeSlug, volume_id: SafeStr):
    vols = await compartment_manager.list_volumes(db, compartment_id)
    for v in vols:
        if v.id == volume_id:
            return v
    raise HTTPException(404, _t("Volume not found"))


def _mode_bits(full: str) -> dict:
    """Return rwx bits for owner/group/other as booleans."""
    try:
        m = os.stat(full).st_mode
    except OSError:
        return {
            "ur": False,
            "uw": False,
            "ux": False,
            "gr": False,
            "gw": False,
            "gx": False,
            "or": False,
            "ow": False,
            "ox": False,
            "octal": "???",
        }
    return {
        "ur": bool(m & 0o400),
        "uw": bool(m & 0o200),
        "ux": bool(m & 0o100),
        "gr": bool(m & 0o040),
        "gw": bool(m & 0o020),
        "gx": bool(m & 0o010),
        "or": bool(m & 0o004),
        "ow": bool(m & 0o002),
        "ox": bool(m & 0o001),
        "octal": oct(m & 0o777)[2:],
    }


def _browse_ctx(compartment_id: SafeSlug, vol, path: str, target: str) -> dict:
    """Build template context for the volume browser."""
    entries = []
    for name in sorted(
        os.listdir(target), key=lambda n: (not os.path.isdir(os.path.join(target, n)), n.lower())
    ):
        full = os.path.join(target, name)
        is_dir = os.path.isdir(full)
        try:
            size = None if is_dir else os.path.getsize(full)
        except OSError:
            size = None
        entries.append(
            {
                "name": name,
                "type": "dir" if is_dir else "file",
                "size_fmt": "" if size is None else _fmt_size(size),
                "is_text": (not is_dir) and _is_text(full),
                "mode": _mode_bits(full),
                "selinux_type": get_file_context_type(SafeAbsPath.of(full, "list_files")),
            }
        )
    base = os.path.realpath(vol.host_path)
    rel = "/" + os.path.relpath(target, base).replace("\\", "/")
    if rel == "/.":
        rel = "/"
    parent = str(PurePosixPath(rel).parent) if rel != "/" else None
    return {
        "compartment_id": compartment_id,
        "volume": vol,
        "path": rel,
        "parent": parent,
        "entries": entries,
    }


@router.get("/api/compartments/{compartment_id}/volumes/{volume_name}/size")
async def get_volume_size(
    request: Request,
    compartment_id: SafeSlug,
    volume_name: SafeSlug,
    user: str = Depends(require_auth),
):
    from ..services import metrics

    loop = asyncio.get_event_loop()
    path = os.path.join(metrics._VOLUMES_BASE, compartment_id, volume_name)
    size = await loop.run_in_executor(None, metrics._dir_size, path)
    from ._helpers import _fmt_bytes

    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/volume_size.html",
            {"size_str": _fmt_bytes(size)},
        )
    return {"bytes": size}


@router.post("/api/compartments/{compartment_id}/volumes", status_code=status.HTTP_201_CREATED)
async def add_volume(
    request: Request,
    compartment_id: SafeSlug,
    data: VolumeCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    try:
        volume = await compartment_manager.add_volume(db, compartment_id, data)
    except Exception as exc:
        logger.error("Failed to add volume: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to add volume")) from exc

    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            _comp_ctx(request, comp),
            headers=_toast_trigger("Volume created"),
        )
    return volume.model_dump()


@enforce_model
class _VolumeUpdate(BaseModel):
    owner_uid: int = 0


@router.patch("/api/compartments/{compartment_id}/volumes/{volume_id}", status_code=200)
async def update_volume(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeStr,
    data: _VolumeUpdate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    try:
        await compartment_manager.update_volume_owner(db, compartment_id, volume_id, data.owner_uid)
    except Exception as exc:
        logger.error("Failed to update volume %s: %s", volume_id, exc)
        raise HTTPException(status_code=500, detail=_t("Failed to update volume")) from exc
    comp = await compartment_manager.get_compartment(db, compartment_id)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/compartment_detail.html",
        _comp_ctx(request, comp),
        headers=_toast_trigger("Volume updated"),
    )


@router.delete("/api/compartments/{compartment_id}/volumes/{volume_id}", status_code=204)
async def delete_volume(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeStr,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    try:
        await compartment_manager.delete_volume(db, compartment_id, volume_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            _comp_ctx(request, comp),
            headers=_toast_trigger("Volume deleted"),
        )


@router.get("/api/compartments/{compartment_id}/volumes/form")
async def volume_create_form(
    request: Request,
    compartment_id: SafeSlug,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/volume_form.html",
        _comp_ctx(request, comp),
    )


@router.get("/api/compartments/{compartment_id}/volumes/{volume_id}/browse")
async def volume_browse(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeStr,
    path: SafeStr = "/",
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, compartment_id, volume_id)
    try:
        target = _resolve_vol_path(vol.host_path, path)
    except ValueError as exc:
        raise HTTPException(400, _t("Invalid path")) from exc
    if not os.path.isdir(target):
        raise HTTPException(404, _t("Directory not found"))
    ctx = _browse_ctx(compartment_id, vol, path, target)
    return _TEMPLATES.TemplateResponse(request, "partials/volume_browser.html", {**ctx})


@router.get("/api/compartments/{compartment_id}/volumes/{volume_id}/file")
async def volume_get_file(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeStr,
    path: SafeStr,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, compartment_id, volume_id)
    try:
        target = _resolve_vol_path(vol.host_path, path)
    except ValueError as exc:
        raise HTTPException(400, _t("Invalid path")) from exc
    is_new = not os.path.exists(target)
    if not is_new and not os.path.isfile(target):
        raise HTTPException(400, _t("Not a file"))
    if not is_new and not _is_text(target):
        raise HTTPException(400, _t("Binary files cannot be edited as text"))
    if is_new:
        content = ""
    else:
        with open(target) as _f:
            content = _f.read()
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
    volume_id: SafeStr,
    path: SafeStr,
    content: str = Form(default=""),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, compartment_id, volume_id)
    try:
        target = _resolve_vol_path(vol.host_path, path)
    except ValueError as exc:
        raise HTTPException(400, _t("Invalid path")) from exc
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        raise
    user_manager.chown_to_service_user(compartment_id, SafeAbsPath.of(target, "vol_file_target"))
    relabel(target)
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
        headers=_toast_trigger("Saved"),
    )


@router.post("/api/compartments/{compartment_id}/volumes/{volume_id}/upload")
async def volume_upload(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeStr,
    path: SafeStr = "/",
    file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, compartment_id, volume_id)
    try:
        target_dir = _resolve_vol_path(vol.host_path, path)
    except ValueError as exc:
        raise HTTPException(400, _t("Invalid path")) from exc
    if not os.path.isdir(target_dir):
        raise HTTPException(400, _t("Target is not a directory"))
    filename = re.sub(r"[^\w.\-]", "_", os.path.basename(file.filename or "upload"))
    if not filename:
        raise HTTPException(400, _t("Empty filename"))
    dest = os.path.join(target_dir, filename)
    try:
        _resolve_vol_path(vol.host_path, os.path.relpath(dest, os.path.realpath(vol.host_path)))
    except ValueError as exc:
        raise HTTPException(400, _t("Invalid filename")) from exc
    raw = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            _t("File exceeds maximum upload size of %(n)s MiB")
            % {"n": _MAX_UPLOAD_BYTES // (1024 * 1024)},
        )
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        raise
    user_manager.chown_to_service_user(compartment_id, SafeAbsPath.of(dest, "vol_upload_dest"))
    relabel(dest)
    ctx = _browse_ctx(compartment_id, vol, path, target_dir)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/volume_browser.html",
        {**ctx},
        headers=_toast_trigger(f"Uploaded {filename}"),
    )


@router.delete("/api/compartments/{compartment_id}/volumes/{volume_id}/file")
async def volume_delete_entry(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeStr,
    path: SafeStr,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, compartment_id, volume_id)
    try:
        target = _resolve_vol_path(vol.host_path, path)
    except ValueError as exc:
        raise HTTPException(400, _t("Invalid path")) from exc
    if not os.path.exists(target):
        raise HTTPException(404, _t("Not found"))
    if os.path.isdir(target):
        logger.info(
            "User %s deleted directory %s in volume %s/%s", user, path, compartment_id, volume_id
        )
        shutil.rmtree(target)
    else:
        logger.info(
            "User %s deleted file %s in volume %s/%s", user, path, compartment_id, volume_id
        )
        os.unlink(target)
    dir_path = str(PurePosixPath(path).parent)
    try:
        target_dir = _resolve_vol_path(vol.host_path, dir_path)
    except ValueError:
        target_dir = os.path.realpath(vol.host_path)
    ctx = _browse_ctx(compartment_id, vol, dir_path, target_dir)
    return _TEMPLATES.TemplateResponse(request, "partials/volume_browser.html", {**ctx})


@router.post("/api/compartments/{compartment_id}/volumes/{volume_id}/mkdir")
async def volume_mkdir(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeStr,
    path: SafeStr = Form(...),
    name: SafeStr = Form(...),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, compartment_id, volume_id)
    new_rel = str(PurePosixPath(path) / name)
    try:
        target = _resolve_vol_path(vol.host_path, new_rel)
    except ValueError as exc:
        raise HTTPException(400, _t("Invalid path")) from exc
    os.makedirs(target, exist_ok=True)
    user_manager.chown_to_service_user(compartment_id, SafeAbsPath.of(target, "mkdir_target"))
    relabel(target)
    try:
        parent_target = _resolve_vol_path(vol.host_path, path)
    except ValueError:
        parent_target = os.path.realpath(vol.host_path)
    ctx = _browse_ctx(compartment_id, vol, path, parent_target)
    return _TEMPLATES.TemplateResponse(request, "partials/volume_browser.html", {**ctx})


@router.patch("/api/compartments/{compartment_id}/volumes/{volume_id}/chmod")
async def volume_chmod(
    request: Request,
    compartment_id: SafeSlug,
    volume_id: SafeStr,
    path: SafeStr = Form(...),
    mode: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    """Change permissions of a single file or directory."""
    vol = await _get_vol(db, compartment_id, volume_id)
    try:
        target = _resolve_vol_path(vol.host_path, path)
    except ValueError as exc:
        raise HTTPException(400, _t("Invalid path")) from exc
    if not os.path.exists(target):
        raise HTTPException(404, _t("Path not found"))
    try:
        mode_int = int(mode, 8)
        if not (0 <= mode_int <= 0o777):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(400, _t("Invalid mode — expected octal string like 644")) from exc
    os.chmod(target, mode_int)
    dir_path = str(PurePosixPath(path).parent)
    try:
        dir_target = _resolve_vol_path(vol.host_path, dir_path)
    except ValueError:
        dir_target = os.path.realpath(vol.host_path)
    ctx = _browse_ctx(compartment_id, vol, dir_path, dir_target)
    return _TEMPLATES.TemplateResponse(request, "partials/volume_browser.html", {**ctx})


@router.get("/api/compartments/{compartment_id}/volumes/{volume_id}/archive")
async def volume_archive(
    compartment_id: SafeSlug,
    volume_id: SafeStr,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    """Download all volume files as a zip archive."""
    vol = await _get_vol(db, compartment_id, volume_id)
    base = os.path.realpath(vol.host_path)

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
    filename = f"{compartment_id}-{vol.name}.zip"
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
    volume_id: SafeStr,
    file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    """Extract a zip or tar.gz archive into the volume root."""
    vol = await _get_vol(db, compartment_id, volume_id)
    base = os.path.realpath(vol.host_path)

    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            _t("Archive exceeds maximum upload size of %(n)s MiB")
            % {"n": _MAX_UPLOAD_BYTES // (1024 * 1024)},
        )
    fname = (file.filename or "").lower()

    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            extract_archive,
            data,
            SafeAbsPath.of(base, "vol.host_path"),
            SafeStr.of(fname, "file.filename"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.warning("Archive extraction failed for %s/%s: %s", compartment_id, volume_id, exc)
        raise HTTPException(400, _t("Failed to extract archive")) from exc

    user_manager.chown_to_service_user(compartment_id, SafeAbsPath.of(base, "archive_base"))
    apply_context(base, vol.selinux_context)
    ctx = _browse_ctx(compartment_id, vol, "/", base)
    return _TEMPLATES.TemplateResponse(request, "partials/volume_browser.html", {**ctx})

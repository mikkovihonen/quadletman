"""Container, pod, and image-unit routes."""

import asyncio
import logging
import os
from contextlib import suppress

import aiosqlite
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse

from ..auth import require_auth
from ..database import get_db
from ..i18n import gettext as _t
from ..models import ContainerCreate, ImageUnitCreate, PodCreate
from ..podman_version import get_features
from ..services import compartment_manager, systemd_manager, user_manager
from ..templates_config import TEMPLATES as _TEMPLATES
from ._helpers import (
    _MAX_ENVFILE_BYTES,
    _comp_ctx,
    _is_htmx,
    _require_compartment,
    _toast_trigger,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/compartments/{compartment_id}/containers", status_code=status.HTTP_201_CREATED)
async def add_container(
    request: Request,
    compartment_id: str,
    data: ContainerCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    try:
        container = await compartment_manager.add_container(db, compartment_id, data)
    except Exception as exc:
        logger.error("Failed to add container: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            _comp_ctx(request, comp),
            headers=_toast_trigger("Container added"),
        )
    return container.model_dump()


@router.put("/api/compartments/{compartment_id}/containers/{container_id}")
async def update_container(
    request: Request,
    compartment_id: str,
    container_id: str,
    data: ContainerCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    try:
        container = await compartment_manager.update_container(
            db, compartment_id, container_id, data
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if container is None:
        raise HTTPException(status_code=404, detail=_t("Container not found"))

    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            _comp_ctx(request, comp),
            headers=_toast_trigger("Container updated"),
        )
    return container.model_dump()


@router.delete("/api/compartments/{compartment_id}/containers/{container_id}", status_code=204)
async def delete_container(
    request: Request,
    compartment_id: str,
    container_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    await compartment_manager.delete_container(db, compartment_id, container_id)
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            _comp_ctx(request, comp),
            headers=_toast_trigger("Container removed"),
        )


@router.post("/api/compartments/{compartment_id}/containers/{container_id}/envfile")
async def upload_container_envfile(
    compartment_id: str,
    container_id: str,
    file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    _user: str = Depends(require_auth),
) -> JSONResponse:
    comp = await compartment_manager.get_compartment(db, compartment_id)
    container = next((c for c in comp.containers if c.id == container_id), None)
    if container is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Container not found"))

    raw = await file.read(_MAX_ENVFILE_BYTES + 1)
    if len(raw) > _MAX_ENVFILE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            _t("Env file exceeds %(n)s KiB limit") % {"n": _MAX_ENVFILE_BYTES // 1024},
        )
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, _t("Env file must be valid UTF-8")
        ) from exc

    loop = asyncio.get_event_loop()
    home = await loop.run_in_executor(None, user_manager.get_home, compartment_id)
    env_dir = os.path.join(home, "env")
    await loop.run_in_executor(None, lambda: os.makedirs(env_dir, mode=0o755, exist_ok=True))

    dest = os.path.join(env_dir, f"{container.name}.env")

    def _write() -> None:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(content)
        except Exception:
            with suppress(OSError):
                os.close(fd)
            raise

    await loop.run_in_executor(None, _write)
    await loop.run_in_executor(None, user_manager.chown_to_service_user, compartment_id, dest)
    return JSONResponse({"path": dest})


@router.get("/api/compartments/{compartment_id}/envfile")
async def preview_service_envfile(
    compartment_id: str,
    path: str = Query(...),
    db: aiosqlite.Connection = Depends(get_db),
    _user: str = Depends(require_auth),
) -> JSONResponse:
    loop = asyncio.get_event_loop()
    try:
        home = await loop.run_in_executor(None, user_manager.get_home, compartment_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Service user not found")) from exc

    real_home = os.path.realpath(home)
    real_path = os.path.realpath(path)
    if real_path != real_home and not real_path.startswith(real_home + os.sep):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, _t("Path is outside the service user home directory")
        )
    if not os.path.isfile(real_path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("File not found"))

    def _read() -> str:
        with open(real_path) as fh:
            return fh.read(_MAX_ENVFILE_BYTES)

    try:
        content = await loop.run_in_executor(None, _read)
    except OSError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, _t("Could not read file")
        ) from exc

    lines = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        lines.append({"key": key.strip(), "value": value})

    return JSONResponse({"lines": lines})


@router.delete("/api/compartments/{compartment_id}/containers/{container_id}/envfile")
async def delete_container_envfile(
    compartment_id: str,
    container_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    _user: str = Depends(require_auth),
) -> JSONResponse:
    comp = await compartment_manager.get_compartment(db, compartment_id)
    container = next((c for c in comp.containers if c.id == container_id), None)
    if container is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Container not found"))

    loop = asyncio.get_event_loop()
    try:
        home = await loop.run_in_executor(None, user_manager.get_home, compartment_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Service user not found")) from exc

    env_path = os.path.join(home, "env", f"{container.name}.env")
    real_home = os.path.realpath(home)
    real_path = os.path.realpath(env_path)
    if real_path != real_home and not real_path.startswith(real_home + os.sep):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, _t("Path is outside the service user home directory")
        )

    def _delete() -> None:
        with suppress(FileNotFoundError):
            os.unlink(real_path)

    await loop.run_in_executor(None, _delete)
    return JSONResponse({"ok": True})


@router.post("/api/compartments/{compartment_id}/pods", status_code=201)
async def add_pod(
    request: Request,
    compartment_id: str,
    data: PodCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    features = get_features()
    if not features.quadlet:
        raise HTTPException(
            status_code=400,
            detail=_t("Requires Podman 4.4+ (detected: %(v)s)") % {"v": features.version_str},
        )
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    try:
        pod = await compartment_manager.add_pod(db, compartment_id, data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            _comp_ctx(request, comp),
            headers=_toast_trigger("Pod added"),
        )
    return pod.model_dump()


@router.delete("/api/compartments/{compartment_id}/pods/{pod_id}", status_code=204)
async def delete_pod(
    request: Request,
    compartment_id: str,
    pod_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    try:
        await compartment_manager.delete_pod(db, compartment_id, pod_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            _comp_ctx(request, comp),
            headers=_toast_trigger("Pod removed"),
        )


@router.post("/api/compartments/{compartment_id}/image-units", status_code=201)
async def add_image_unit(
    request: Request,
    compartment_id: str,
    data: ImageUnitCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    features = get_features()
    if not features.quadlet:
        raise HTTPException(
            status_code=400,
            detail=_t("Requires Podman 4.4+ (detected: %(v)s)") % {"v": features.version_str},
        )
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    try:
        iu = await compartment_manager.add_image_unit(db, compartment_id, data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            _comp_ctx(request, comp),
            headers=_toast_trigger("Image unit added"),
        )
    return iu.model_dump()


@router.delete("/api/compartments/{compartment_id}/image-units/{image_unit_id}", status_code=204)
async def delete_image_unit(
    request: Request,
    compartment_id: str,
    image_unit_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    try:
        await compartment_manager.delete_image_unit(db, compartment_id, image_unit_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            _comp_ctx(request, comp),
            headers=_toast_trigger("Image unit removed"),
        )


@router.get("/api/compartments/{compartment_id}/containers/form")
async def container_create_form(
    request: Request,
    compartment_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    loop = asyncio.get_event_loop()
    local_images, log_drivers = await asyncio.gather(
        loop.run_in_executor(None, systemd_manager.list_images, compartment_id),
        loop.run_in_executor(None, user_manager.get_compartment_log_drivers, compartment_id),
    )
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/container_form.html",
        {
            "compartment": comp,
            "container": None,
            "volume_mounts": [],
            "bind_mounts": [],
            "env_pairs": [],
            "ports": [],
            "uid_map": [],
            "gid_map": [],
            "other_containers": [c.name for c in comp.containers],
            "local_images": local_images,
            "log_drivers": log_drivers,
        },
    )


@router.get("/api/compartments/{compartment_id}/containers/{container_id}/form")
async def container_edit_form(
    request: Request,
    compartment_id: str,
    container_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    container = await compartment_manager.get_container(db, container_id)
    if comp is None or container is None:
        raise HTTPException(status_code=404)
    loop = asyncio.get_event_loop()
    local_images, log_drivers = await asyncio.gather(
        loop.run_in_executor(None, systemd_manager.list_images, compartment_id),
        loop.run_in_executor(None, user_manager.get_compartment_log_drivers, compartment_id),
    )
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/container_form.html",
        {
            "compartment": comp,
            "container": container,
            "volume_mounts": [vm.model_dump() for vm in container.volumes],
            "bind_mounts": [bm.model_dump() for bm in container.bind_mounts],
            "env_pairs": list(container.environment.items()),
            "ports": container.ports,
            "uid_map": container.uid_map,
            "gid_map": container.gid_map,
            "other_containers": [c.name for c in comp.containers if c.id != container_id],
            "local_images": local_images,
            "log_drivers": log_drivers,
        },
    )

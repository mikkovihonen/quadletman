"""Container, pod, and image-unit routes."""

import asyncio
import logging
import os
from contextlib import suppress

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..i18n import gettext as _t
from ..models import ContainerCreate, ImageUnitCreate, PodCreate
from ..models.sanitized import (
    SafeAbsPath,
    SafeSlug,
    SafeStr,
    SafeUsername,
    SafeUUID,
    log_safe,
    resolve_safe_path,
)
from ..models.version_span import validate_version_spans
from ..podman_version import get_features
from ..services import compartment_manager, systemd_manager, user_manager
from ..services.archive import extract_archive
from .helpers import (
    MAX_ENVFILE_BYTES,
    MAX_UPLOAD_BYTES,
    comp_ctx,
    is_htmx,
    require_compartment,
    toast_trigger,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/compartments/{compartment_id}/containers", status_code=status.HTTP_201_CREATED)
async def add_container(
    request: Request,
    compartment_id: SafeSlug,
    data: ContainerCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    features = get_features()
    validate_version_spans(data, features.version, features.version_str)
    try:
        container = await compartment_manager.add_container(db, compartment_id, data)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=_t("A container named '%(name)s' already exists") % {"name": data.name},
        ) from exc
    except Exception as exc:
        logger.error("Failed to add container: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to add container")) from exc

    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Container added"),
        )
    return container.model_dump()


@router.put("/api/compartments/{compartment_id}/containers/{container_id}")
async def update_container(
    request: Request,
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    data: ContainerCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    features = get_features()
    validate_version_spans(data, features.version, features.version_str)
    try:
        container = await compartment_manager.update_container(
            db, compartment_id, container_id, data
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=_t("A container named '%(name)s' already exists") % {"name": data.name},
        ) from exc
    except Exception as exc:
        logger.error("Failed to update container %s: %s", log_safe(container_id), exc)
        raise HTTPException(status_code=500, detail=_t("Failed to update container")) from exc
    if container is None:
        raise HTTPException(status_code=404, detail=_t("Container not found"))

    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Container updated"),
        )
    return container.model_dump()


@router.delete("/api/compartments/{compartment_id}/containers/{container_id}", status_code=204)
async def delete_container(
    request: Request,
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    await compartment_manager.delete_container(db, compartment_id, container_id)
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Container removed"),
        )


@router.post("/api/compartments/{compartment_id}/containers/{container_id}/envfile")
async def upload_container_envfile(
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _user: SafeUsername = Depends(require_auth),
) -> JSONResponse:
    comp = await compartment_manager.get_compartment(db, compartment_id)
    container = next((c for c in comp.containers if c.id == container_id), None)
    if container is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Container not found"))

    raw = await file.read(MAX_ENVFILE_BYTES + 1)
    if len(raw) > MAX_ENVFILE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            _t("Env file exceeds %(n)s KiB limit") % {"n": MAX_ENVFILE_BYTES // 1024},
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
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(content)
        except Exception:
            with suppress(OSError):
                os.close(fd)
            raise

    await loop.run_in_executor(None, _write)
    await loop.run_in_executor(
        None,
        user_manager.chown_to_service_user,
        compartment_id,
        SafeAbsPath.of(dest, "envfile_dest"),
    )
    return JSONResponse({"path": dest})


@router.get("/api/compartments/{compartment_id}/envfile")
async def preview_service_envfile(
    compartment_id: SafeSlug,
    path: SafeAbsPath = Query(...),
    db: AsyncSession = Depends(get_db),
    _user: SafeUsername = Depends(require_auth),
) -> JSONResponse:
    loop = asyncio.get_event_loop()
    try:
        home = await loop.run_in_executor(None, user_manager.get_home, compartment_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("Service user not found")) from exc

    try:
        real_path = resolve_safe_path(home, path, absolute=True)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, _t("Path is outside the service user home directory")
        ) from exc
    if not os.path.isfile(real_path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _t("File not found"))

    def _read() -> str:
        with open(real_path) as fh:
            return fh.read(MAX_ENVFILE_BYTES)

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
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    _user: SafeUsername = Depends(require_auth),
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
    compartment_id: SafeSlug,
    data: PodCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    features = get_features()
    if not features.pod_units:
        raise HTTPException(
            status_code=400,
            detail=_t("Requires Podman 5.0+ (detected: %(v)s)") % {"v": features.version_str},
        )
    validate_version_spans(data, features.version, features.version_str)
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    try:
        pod = await compartment_manager.add_pod(db, compartment_id, data)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=_t("A pod named '%(name)s' already exists") % {"name": data.name},
        ) from exc
    except Exception as exc:
        logger.error("Failed to add pod: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to add pod")) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Pod added"),
        )
    return pod.model_dump()


@router.delete("/api/compartments/{compartment_id}/pods/{pod_id}", status_code=204)
async def delete_pod(
    request: Request,
    compartment_id: SafeSlug,
    pod_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    try:
        await compartment_manager.delete_pod(db, compartment_id, pod_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Pod removed"),
        )


@router.post("/api/compartments/{compartment_id}/image-units", status_code=201)
async def add_image_unit(
    request: Request,
    compartment_id: SafeSlug,
    data: ImageUnitCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    features = get_features()
    if not features.image_units:
        raise HTTPException(
            status_code=400,
            detail=_t("Requires Podman 4.8+ (detected: %(v)s)") % {"v": features.version_str},
        )
    validate_version_spans(data, features.version, features.version_str)
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    try:
        iu = await compartment_manager.add_image_unit(db, compartment_id, data)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=_t("An image unit named '%(name)s' already exists") % {"name": data.name},
        ) from exc
    except Exception as exc:
        logger.error("Failed to add image unit: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to add image unit")) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Image unit added"),
        )
    return iu.model_dump()


@router.delete("/api/compartments/{compartment_id}/image-units/{image_unit_id}", status_code=204)
async def delete_image_unit(
    request: Request,
    compartment_id: SafeSlug,
    image_unit_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    try:
        await compartment_manager.delete_image_unit(db, compartment_id, image_unit_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Image unit removed"),
        )


@router.get("/api/compartments/{compartment_id}/containers/form")
async def container_create_form(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    loop = asyncio.get_event_loop()
    local_images, log_drivers = await asyncio.gather(
        loop.run_in_executor(None, systemd_manager.list_images, compartment_id),
        loop.run_in_executor(None, user_manager.get_compartment_log_drivers, compartment_id),
    )
    compartment_secrets = await compartment_manager.list_secrets(db, compartment_id)
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
            "compartment_secrets": compartment_secrets,
        },
    )


@router.get("/api/compartments/{compartment_id}/containers/{container_id}/form")
async def container_edit_form(
    request: Request,
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
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
            "compartment_secrets": await compartment_manager.list_secrets(db, compartment_id),
        },
    )


@router.get("/api/compartments/{compartment_id}/containers/{container_id}/inspect")
async def inspect_container(
    request: Request,
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    """Return podman inspect output for a container."""
    comp = await compartment_manager.get_compartment(db, compartment_id)
    container = await compartment_manager.get_container(db, container_id)
    if comp is None or container is None:
        raise HTTPException(status_code=404, detail=_t("Container not found"))

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(
        None,
        systemd_manager.inspect_container,
        compartment_id,
        SafeStr.of(container.name, "container_name"),
    )

    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/inspect_modal.html",
            {"compartment": comp, "container": container, "inspect": data},
        )
    return data


@router.post("/api/compartments/{compartment_id}/containers/{container_id}/build-context")
async def upload_build_context(
    request: Request,
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    """Upload a tar/zip archive as the build context for a Containerfile container."""
    comp = await compartment_manager.get_compartment(db, compartment_id)
    container = await compartment_manager.get_container(db, container_id)
    if comp is None or container is None:
        raise HTTPException(status_code=404, detail=_t("Container not found"))
    if not container.build_context and not container.containerfile_content:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            _t("Container is not configured as a build container"),
        )

    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            _t("Upload exceeds %(n)s MiB limit") % {"n": MAX_UPLOAD_BYTES // (1024 * 1024)},
        )

    fname = (file.filename or "").lower()

    loop = asyncio.get_event_loop()
    home = await loop.run_in_executor(None, user_manager.get_home, compartment_id)
    build_dir = os.path.join(home, ".config", "containers", "systemd", f"build-{container.name}")

    def _do_extract() -> str:
        os.makedirs(build_dir, mode=0o750, exist_ok=True)
        extract_archive(
            raw, SafeAbsPath.of(build_dir, "build_dir"), SafeStr.of(fname, "file.filename")
        )
        return build_dir

    try:
        ctx_path = await loop.run_in_executor(None, _do_extract)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    await loop.run_in_executor(
        None,
        user_manager.chown_to_service_user,
        compartment_id,
        SafeAbsPath.of(build_dir, "build_dir"),
    )

    # Update build_context in DB using a copy of the container's current settings
    from ..models import ContainerCreate as _CC

    updated_data = _CC(
        **{
            **{f: getattr(container, f) for f in _CC.model_fields},
            "build_context": ctx_path,
        }
    )
    await compartment_manager.update_container(db, compartment_id, container_id, updated_data)

    if is_htmx(request):
        updated_comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, updated_comp),
            headers=toast_trigger(_t("Build context uploaded")),
        )
    return {"build_context": ctx_path}


# ---------------------------------------------------------------------------
# Image management (Feature 8 + 14)
# ---------------------------------------------------------------------------


@router.get("/api/compartments/{compartment_id}/images")
async def list_compartment_images(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
) -> JSONResponse:
    """Return detailed image list for a compartment's Podman store."""
    loop = asyncio.get_event_loop()
    images = await loop.run_in_executor(None, systemd_manager.list_images_detail, compartment_id)
    return JSONResponse(images)


@router.post("/api/compartments/{compartment_id}/images/prune", status_code=200)
async def prune_compartment_images(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
) -> JSONResponse:
    """Remove dangling (unused) images from the compartment's Podman store."""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, systemd_manager.prune_images, compartment_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/api/compartments/{compartment_id}/images/pull")
async def pull_compartment_image(
    compartment_id: SafeSlug,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
) -> JSONResponse:
    """Pull (or re-pull) a specific image for the compartment user (Feature 14)."""
    from ..models import _no_control_chars
    from ..models.sanitized import IMAGE_RE as _IMAGE_RE

    image = (body.get("image") or "").strip()
    if not image:
        raise HTTPException(status_code=400, detail=_t("image is required"))
    try:
        _no_control_chars(image, "image")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not _IMAGE_RE.match(image) or len(image) > 255:
        raise HTTPException(status_code=400, detail=_t("Invalid image reference"))

    loop = asyncio.get_event_loop()
    try:
        output = await loop.run_in_executor(
            None,
            systemd_manager.pull_image,
            compartment_id,
            SafeStr.of(image, "image"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "output": output})

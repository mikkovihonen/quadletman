"""Container, pod, and image-unit routes."""

import asyncio
import logging
import os
from contextlib import suppress

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..i18n import gettext as _t
from ..models import ArtifactCreate, ContainerCreate, ImageCreate, PodCreate
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
from ..security.auth import require_auth
from ..services import compartment_manager, systemd_manager, user_manager
from .helpers import (
    MAX_ENVFILE_BYTES,
    choices_for_template,
    comp_ctx,
    is_htmx,
    require_compartment,
    toast_trigger,
)
from .helpers.common import get_field_choices

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
            detail=_t("A container named '%(name)s' already exists") % {"name": data.qm_name},
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
            detail=_t("A container named '%(name)s' already exists") % {"name": data.qm_name},
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


@router.post("/api/compartments/{compartment_id}/containers/{container_id}/start")
async def start_container(
    request: Request,
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    try:
        await compartment_manager.start_container(db, compartment_id, container_id)
        error = None
    except ValueError as exc:
        logger.warning("Container not found for start: %s: %s", log_safe(container_id), exc)
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to start container %s: %s", log_safe(container_id), exc)
        error = "Operation failed — check server logs"
    statuses = await compartment_manager.get_status(db, compartment_id)
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        toast = error or _t("Container started")
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            {**await comp_ctx(request, comp), "statuses": statuses},
            headers=toast_trigger(toast, error=bool(error)),
        )
    return {"statuses": statuses, "error": error}


@router.post("/api/compartments/{compartment_id}/containers/{container_id}/stop")
async def stop_container(
    request: Request,
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    try:
        await compartment_manager.stop_container(db, compartment_id, container_id)
        error = None
    except ValueError as exc:
        logger.warning("Container not found for stop: %s: %s", log_safe(container_id), exc)
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to stop container %s: %s", log_safe(container_id), exc)
        error = "Operation failed — check server logs"
    statuses = await compartment_manager.get_status(db, compartment_id)
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        toast = error or _t("Container stopped")
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            {**await comp_ctx(request, comp), "statuses": statuses},
            headers=toast_trigger(toast, error=bool(error)),
        )
    return {"statuses": statuses, "error": error}


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

    dest = os.path.join(env_dir, f"{container.qm_name}.env")

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
        logger.warning("Envfile path validation failed: %s", exc)
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
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

    env_path = os.path.join(home, "env", f"{container.qm_name}.env")
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
            detail=_t("A pod named '%(name)s' already exists") % {"name": data.qm_name},
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
        logger.warning("Pod deletion conflict: %s", exc)
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
    data: ImageCreate,
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
        iu = await compartment_manager.add_image(db, compartment_id, data)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=_t("An image named '%(name)s' already exists") % {"name": data.qm_name},
        ) from exc
    except Exception as exc:
        logger.error("Failed to add image: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to add image")) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Image added"),
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
        await compartment_manager.delete_image(db, compartment_id, image_unit_id)
    except ValueError as exc:
        logger.warning("Image deletion conflict: %s", exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Image unit removed"),
        )


@router.get("/api/compartments/{compartment_id}/image-units/form")
async def image_unit_create_form(
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
        "partials/image_unit_form.html",
        {"compartment": comp, "iu": None},
    )


@router.get("/api/compartments/{compartment_id}/image-units/{image_unit_id}/form")
async def image_unit_edit_form(
    request: Request,
    compartment_id: SafeSlug,
    image_unit_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    iu = next((u for u in comp.images if u.id == str(image_unit_id)), None)
    if iu is None:
        raise HTTPException(status_code=404, detail=_t("Image not found"))
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/image_unit_form.html",
        {"compartment": comp, "iu": iu},
    )


@router.put("/api/compartments/{compartment_id}/image-units/{image_unit_id}")
async def update_image_unit(
    request: Request,
    compartment_id: SafeSlug,
    image_unit_id: SafeUUID,
    data: ImageCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    features = get_features()
    validate_version_spans(data, features.version, features.version_str)
    try:
        iu = await compartment_manager.update_image(db, compartment_id, image_unit_id, data)
    except ValueError as exc:
        logger.warning("Image not found for update: %s", exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to update image: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to update image")) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Image updated"),
        )
    return iu.model_dump()


@router.get("/api/compartments/{compartment_id}/pods/form")
async def pod_create_form(
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
        "partials/pod_form.html",
        {"compartment": comp, "pod": None},
    )


@router.get("/api/compartments/{compartment_id}/pods/{pod_id}/form")
async def pod_edit_form(
    request: Request,
    compartment_id: SafeSlug,
    pod_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    pod = next((p for p in comp.pods if p.id == str(pod_id)), None)
    if pod is None:
        raise HTTPException(status_code=404, detail=_t("Pod not found"))
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/pod_form.html",
        {"compartment": comp, "pod": pod},
    )


@router.put("/api/compartments/{compartment_id}/pods/{pod_id}")
async def update_pod(
    request: Request,
    compartment_id: SafeSlug,
    pod_id: SafeUUID,
    data: PodCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    features = get_features()
    validate_version_spans(data, features.version, features.version_str)
    try:
        pod = await compartment_manager.update_pod(db, compartment_id, pod_id, data)
    except ValueError as exc:
        logger.warning("Pod not found for update: %s", exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to update pod: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to update pod")) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Pod updated"),
        )
    return pod.model_dump()


@router.post("/api/compartments/{compartment_id}/artifacts", status_code=201)
async def add_artifact(
    request: Request,
    compartment_id: SafeSlug,
    data: ArtifactCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    features = get_features()
    if not features.artifact_units:
        raise HTTPException(
            status_code=400,
            detail=_t("Requires Podman 5.7+ (detected: %(v)s)") % {"v": features.version_str},
        )
    validate_version_spans(data, features.version, features.version_str)
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    try:
        artifact = await compartment_manager.add_artifact(db, compartment_id, data)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=_t("An artifact named '%(name)s' already exists") % {"name": data.qm_name},
        ) from exc
    except Exception as exc:
        logger.error("Failed to add artifact: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to add artifact")) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Artifact added"),
        )
    return artifact.model_dump()


@router.delete("/api/compartments/{compartment_id}/artifacts/{artifact_id}", status_code=204)
async def delete_artifact(
    request: Request,
    compartment_id: SafeSlug,
    artifact_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    try:
        await compartment_manager.delete_artifact(db, compartment_id, artifact_id)
    except ValueError as exc:
        logger.warning("Artifact deletion conflict: %s", exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Artifact removed"),
        )


@router.get("/api/compartments/{compartment_id}/artifacts/form")
async def artifact_create_form(
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
        "partials/artifact_form.html",
        {"compartment": comp, "artifact": None},
    )


@router.get("/api/compartments/{compartment_id}/artifacts/{artifact_id}/form")
async def artifact_edit_form(
    request: Request,
    compartment_id: SafeSlug,
    artifact_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    artifact = next((a for a in comp.artifacts if a.id == str(artifact_id)), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail=_t("Artifact not found"))
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/artifact_form.html",
        {"compartment": comp, "artifact": artifact},
    )


@router.put("/api/compartments/{compartment_id}/artifacts/{artifact_id}")
async def update_artifact(
    request: Request,
    compartment_id: SafeSlug,
    artifact_id: SafeUUID,
    data: ArtifactCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    features = get_features()
    validate_version_spans(data, features.version, features.version_str)
    try:
        artifact = await compartment_manager.update_artifact(db, compartment_id, artifact_id, data)
    except ValueError as exc:
        logger.warning("Artifact not found for update: %s", exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to update artifact: %s", exc)
        raise HTTPException(status_code=500, detail=_t("Failed to update artifact")) from exc
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger("Artifact updated"),
        )
    return artifact.model_dump()


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
    _fc = get_field_choices(ContainerCreate)
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
            "other_containers": [c.qm_name for c in comp.containers],
            "local_images": local_images,
            "log_drivers": log_drivers,
            "log_driver_choices": choices_for_template(
                _fc["log_driver"],
                dynamic_items=log_drivers,
            ),
            "pod_choices": choices_for_template(
                _fc["pod"],
                dynamic_items=[p.qm_name for p in comp.pods],
            ),
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
    _fc = get_field_choices(ContainerCreate)
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
            "other_containers": [c.qm_name for c in comp.containers if c.id != container_id],
            "local_images": local_images,
            "log_drivers": log_drivers,
            "log_driver_choices": choices_for_template(
                _fc["log_driver"],
                current_value=container.log_driver,
                dynamic_items=log_drivers,
            ),
            "pod_choices": choices_for_template(
                _fc["pod"],
                current_value=container.pod,
                dynamic_items=[p.qm_name for p in comp.pods],
            ),
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
        SafeStr.of(container.qm_name, "container_name"),
    )

    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/inspect_modal.html",
            {"compartment": comp, "container": container, "inspect": data},
        )
    return data


@router.get("/api/compartments/{compartment_id}/containers/{container_id}/tcp")
async def container_tcp(
    request: Request,
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    """Return raw /proc/net/tcp content for a container's network namespace."""
    comp = await compartment_manager.get_compartment(db, compartment_id)
    container = await compartment_manager.get_container(db, container_id)
    if comp is None or container is None:
        raise HTTPException(status_code=404, detail=_t("Container not found"))

    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        None,
        systemd_manager.read_container_tcp,
        compartment_id,
        SafeStr.of(container.qm_name, "container_name"),
    )

    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/tcp_panel.html",
            {"raw_tcp": raw, "container": container},
        )
    return {"raw": raw}


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
        logger.exception("Failed to prune images")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
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
        logger.warning("Invalid image reference input: %s", exc)
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
        logger.exception("Failed to pull image")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    return JSONResponse({"ok": True, "output": output})

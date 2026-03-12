"""REST API + HTMX-aware routes for quadletman."""

import asyncio
import io
import logging
import os
import shutil
import tarfile
import urllib.parse
import zipfile
from pathlib import Path, PurePosixPath

import aiosqlite
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from ..auth import require_auth
from ..database import get_db
from ..models import (
    ContainerCreate,
    ServiceCreate,
    ServiceUpdate,
    VolumeCreate,
)
from ..podman_version import get_features
from ..services import metrics, service_manager, systemd_manager, user_manager
from ..services.selinux import apply_context, get_file_context_type, is_selinux_active, relabel

logger = logging.getLogger(__name__)
router = APIRouter()

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_TEMPLATES.env.globals["podman"] = get_features()
_TEMPLATES.env.globals["selinux_active"] = is_selinux_active()
_TEMPLATES.env.filters["urlencode"] = urllib.parse.quote


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _svc_ctx(request: Request, svc) -> dict:
    """Base template context for service_detail.html, including service user info."""
    return {
        "request": request,
        "service": svc,
        "service_user_info": user_manager.get_user_info(svc.id),
        "helper_users": user_manager.list_helper_users(svc.id),
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@router.get("/api/logout")
async def logout():
    """Force the browser to clear its Basic Auth credentials by returning 401."""
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="quadletman"'},
    )


# ---------------------------------------------------------------------------
# Dashboard / Metrics
# ---------------------------------------------------------------------------


@router.get("/api/dashboard")
async def get_dashboard(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    services = await service_manager.list_services(db)
    return _TEMPLATES.TemplateResponse(
        "partials/dashboard.html",
        {"request": request, "services": services, "user": user},
    )


@router.get("/api/metrics")
async def get_metrics(
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    services = await service_manager.list_services(db)
    loop = asyncio.get_event_loop()
    results = []
    for svc in services:
        info = user_manager.get_user_info(svc.id)
        uid = info.get("uid") if info else None
        if uid is not None:
            m = await loop.run_in_executor(None, metrics.get_metrics, svc.id, uid)
            m["display_name"] = svc.display_name
            results.append(m)
    return results


@router.get("/api/metrics/disk")
async def get_metrics_disk(
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    services = await service_manager.list_services(db)
    loop = asyncio.get_event_loop()
    results = []
    for svc in services:
        d = await loop.run_in_executor(None, metrics.get_disk_breakdown, svc.id)
        total = (sum(x["bytes"] for x in d["images"]) +
                 sum(x["bytes"] for x in d["overlays"]) +
                 d["volumes_total"] +
                 d["config_bytes"])
        results.append({"service_id": svc.id, "disk_bytes": total})
    return results


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@router.get("/api/services")
async def list_services(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    services = await service_manager.list_services(db)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            "partials/service_list.html",
            {"request": request, "services": services},
        )
    return [s.model_dump() for s in services]


@router.post("/api/services", status_code=status.HTTP_201_CREATED)
async def create_service(
    request: Request,
    data: ServiceCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    existing = await service_manager.get_service(db, data.id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Service '{data.id}' already exists")
    try:
        svc = await service_manager.create_service(db, data)
    except Exception as exc:
        logger.error("Failed to create service %s: %s", data.id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if _is_htmx(request):
        services = await service_manager.list_services(db)
        return _TEMPLATES.TemplateResponse(
            "partials/service_list.html",
            {"request": request, "services": services},
            headers={"HX-Trigger": '{"showToast": "Service created successfully"}'},
        )
    return svc.model_dump()


@router.post("/api/services/import", status_code=status.HTTP_201_CREATED)
async def import_service_bundle(
    service_id: str = Form(...),
    display_name: str = Form(""),
    description: str = Form(""),
    file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    """Import a .quadlets bundle file as a new service."""
    from ..services.bundle_parser import parse_quadlets_bundle

    try:
        raw = await file.read()
        content = raw.decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not read file: {exc}") from exc

    parse_result = parse_quadlets_bundle(content)
    if not parse_result.containers:
        raise HTTPException(
            status_code=422,
            detail="No [Container] sections found in bundle",
        )

    existing = await service_manager.get_service(db, service_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Service '{service_id}' already exists")

    try:
        await service_manager.create_service(
            db,
            ServiceCreate(
                id=service_id,
                display_name=display_name or service_id,
                description=description,
            ),
        )
    except Exception as exc:
        logger.error("import: failed to create service %s: %s", service_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to create service: {exc}") from exc

    import_errors: list[dict] = []
    for pc in parse_result.containers:
        try:
            await service_manager.add_container(
                db,
                service_id,
                ContainerCreate(
                    name=pc.name,
                    image=pc.image,
                    environment=pc.environment,
                    ports=pc.ports,
                    labels=pc.labels,
                    network=pc.network,
                    restart_policy=pc.restart_policy,
                    exec_start_pre=pc.exec_start_pre,
                    memory_limit=pc.memory_limit,
                    cpu_quota=pc.cpu_quota,
                    depends_on=pc.depends_on,
                    apparmor_profile=pc.apparmor_profile,
                    volumes=[],
                ),
            )
        except Exception as exc:
            logger.error("import: failed to add container %s: %s", pc.name, exc)
            import_errors.append({"container": pc.name, "error": str(exc)})

    result = (await service_manager.get_service(db, service_id)).model_dump()
    result["import_warnings"] = parse_result.warnings
    result["import_errors"] = import_errors
    return JSONResponse(status_code=201, content=result)


@router.get("/api/services/{service_id}")
async def get_service(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    if _is_htmx(request):
        statuses = await service_manager.get_status(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            {**_svc_ctx(request, svc), "statuses": statuses},
        )
    return svc.model_dump()


@router.put("/api/services/{service_id}")
async def update_service(
    request: Request,
    service_id: str,
    data: ServiceUpdate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    svc = await service_manager.update_service(db, service_id, data.display_name, data.description)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Service updated"}'},
        )
    return svc.model_dump()


@router.delete("/api/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    try:
        await service_manager.delete_service(db, service_id)
    except Exception as exc:
        logger.error("Failed to delete service %s: %s", service_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if _is_htmx(request):
        services = await service_manager.list_services(db)
        return _TEMPLATES.TemplateResponse(
            "partials/service_list.html",
            {"request": request, "services": services},
            headers={"HX-Trigger": '{"showToast": "Service deleted", "clearDetail": true}'},
        )


# ---------------------------------------------------------------------------
# Bundle export
# ---------------------------------------------------------------------------


@router.get("/api/services/{service_id}/export")
async def export_service(
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    """Download the service's quadlet units as a .quadlets bundle file."""
    bundle = await service_manager.export_service_bundle(db, service_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Service not found")
    return Response(
        content=bundle,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{service_id}.quadlets"'},
    )


# ---------------------------------------------------------------------------
# Service actions
# ---------------------------------------------------------------------------


@router.post("/api/services/{service_id}/start")
async def start_service(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    import json as _json

    errors = await service_manager.start_service(db, service_id)
    statuses = await service_manager.get_status(db, service_id)
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        toast = f"{len(errors)} unit(s) failed to start" if errors else "Service started"
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            {**_svc_ctx(request, svc), "statuses": statuses, "errors": errors},
            headers={"HX-Trigger": _json.dumps({"showToast": toast, "toastType": "error" if errors else "success"})},
        )
    return {"statuses": statuses, "errors": errors}


@router.post("/api/services/{service_id}/stop")
async def stop_service(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    import json as _json

    errors = await service_manager.stop_service(db, service_id)
    statuses = await service_manager.get_status(db, service_id)
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        toast = f"{len(errors)} unit(s) failed to stop" if errors else "Service stopped"
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            {**_svc_ctx(request, svc), "statuses": statuses, "errors": errors},
            headers={"HX-Trigger": _json.dumps({"showToast": toast, "toastType": "error" if errors else "success"})},
        )
    return {"statuses": statuses, "errors": errors}


@router.post("/api/services/{service_id}/restart")
async def restart_service(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    import json as _json

    errors = await service_manager.restart_service(db, service_id)
    statuses = await service_manager.get_status(db, service_id)
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        toast = f"{len(errors)} unit(s) failed to restart" if errors else "Service restarted"
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            {**_svc_ctx(request, svc), "statuses": statuses, "errors": errors},
            headers={"HX-Trigger": _json.dumps({"showToast": toast, "toastType": "error" if errors else "success"})},
        )
    return {"statuses": statuses, "errors": errors}


@router.post("/api/services/{service_id}/enable")
async def enable_service(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    await service_manager.enable_service(db, service_id)
    statuses = await service_manager.get_status(db, service_id)
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            {**_svc_ctx(request, svc), "statuses": statuses},
            headers={"HX-Trigger": '{"showToast": "Autostart enabled"}'},
        )
    return {"ok": True}


@router.post("/api/services/{service_id}/disable")
async def disable_service(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    await service_manager.disable_service(db, service_id)
    statuses = await service_manager.get_status(db, service_id)
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            {**_svc_ctx(request, svc), "statuses": statuses},
            headers={"HX-Trigger": '{"showToast": "Autostart disabled"}'},
        )
    return {"ok": True}


@router.get("/api/services/{service_id}/sync")
async def get_sync_status(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    issues = await service_manager.check_sync(db, service_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            "partials/sync_status.html",
            {"request": request, "service_id": service_id, "issues": issues},
        )
    return {"in_sync": not issues, "issues": issues}


@router.post("/api/services/{service_id}/sync")
async def resync_service(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    import json as _json

    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    try:
        await service_manager.resync_service(db, service_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    issues = await service_manager.check_sync(db, service_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            "partials/sync_status.html",
            {"request": request, "service_id": service_id, "issues": issues},
            headers={"HX-Trigger": _json.dumps({"showToast": "Unit files re-synced"})},
        )
    return {"in_sync": not issues, "issues": issues}


@router.get("/api/services/{service_id}/quadlets")
async def get_service_quadlets(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    files = await service_manager.get_quadlet_files(db, service_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            "partials/quadlets_viewer.html",
            {"request": request, "service_id": service_id, "files": files},
        )
    return {"files": files}


@router.get("/api/services/{service_id}/status")
async def get_service_status(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    statuses = await service_manager.get_status(db, service_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            "partials/status_badges.html",
            {"request": request, "service_id": service_id, "statuses": statuses},
        )
    return {"statuses": statuses}


@router.get("/api/services/{service_id}/metrics")
async def get_service_metrics(
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    info = user_manager.get_user_info(service_id)
    uid = info.get("uid") if info else None
    if uid is None:
        return {"service_id": service_id, "cpu_percent": 0, "mem_bytes": 0, "proc_count": 0, "disk_bytes": 0}
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, metrics.get_metrics, service_id, uid)


@router.get("/api/services/{service_id}/processes")
async def get_service_processes(
    service_id: str,
    user: str = Depends(require_auth),
):
    info = user_manager.get_user_info(service_id)
    uid = info.get("uid") if info else None
    if uid is None:
        return []
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, metrics.get_processes, uid)


@router.get("/api/services/{service_id}/disk-usage")
async def get_service_disk_usage(
    request: Request,
    service_id: str,
    user: str = Depends(require_auth),
):
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, metrics.get_disk_breakdown, service_id)
    return data


@router.get("/api/services/{service_id}/volumes/{volume_name}/size")
async def get_volume_size(
    request: Request,
    service_id: str,
    volume_name: str,
    user: str = Depends(require_auth),
):
    import os
    from ..services.metrics import _dir_size, _VOLUMES_BASE
    from fastapi.responses import HTMLResponse
    loop = asyncio.get_event_loop()
    path = os.path.join(_VOLUMES_BASE, service_id, volume_name)
    size = await loop.run_in_executor(None, _dir_size, path)
    if _is_htmx(request):
        b = size
        if b >= 1_073_741_824:
            txt = f"{b/1_073_741_824:.1f} GB"
        elif b >= 1_048_576:
            txt = f"{b/1_048_576:.1f} MB"
        elif b >= 1024:
            txt = f"{b/1024:.1f} KB"
        else:
            txt = f"{b} B"
        return HTMLResponse(f'<span class="font-mono">{txt}</span>')
    return {"bytes": size}


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------


@router.post("/api/services/{service_id}/containers", status_code=status.HTTP_201_CREATED)
async def add_container(
    request: Request,
    service_id: str,
    data: ContainerCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    try:
        container = await service_manager.add_container(db, service_id, data)
    except Exception as exc:
        logger.error("Failed to add container: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Container added"}'},
        )
    return container.model_dump()


@router.put("/api/services/{service_id}/containers/{container_id}")
async def update_container(
    request: Request,
    service_id: str,
    container_id: str,
    data: ContainerCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    try:
        container = await service_manager.update_container(db, service_id, container_id, data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if container is None:
        raise HTTPException(status_code=404, detail="Container not found")

    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Container updated"}'},
        )
    return container.model_dump()


@router.delete("/api/services/{service_id}/containers/{container_id}", status_code=204)
async def delete_container(
    request: Request,
    service_id: str,
    container_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    await service_manager.delete_container(db, service_id, container_id)
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Container removed"}'},
        )


# ---------------------------------------------------------------------------
# Volumes
# ---------------------------------------------------------------------------


@router.post("/api/services/{service_id}/volumes", status_code=status.HTTP_201_CREATED)
async def add_volume(
    request: Request,
    service_id: str,
    data: VolumeCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    try:
        volume = await service_manager.add_volume(db, service_id, data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Volume created"}'},
        )
    return volume.model_dump()


@router.patch("/api/services/{service_id}/volumes/{volume_id}", status_code=200)
async def update_volume(
    request: Request,
    service_id: str,
    volume_id: str,
    owner_uid: int = Form(0),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    try:
        await service_manager.update_volume_owner(db, service_id, volume_id, owner_uid)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    svc = await service_manager.get_service(db, service_id)
    return _TEMPLATES.TemplateResponse(
        "partials/service_detail.html",
        _svc_ctx(request, svc),
        headers={"HX-Trigger": '{"showToast": "Volume updated"}'},
    )


@router.delete("/api/services/{service_id}/volumes/{volume_id}", status_code=204)
async def delete_volume(
    request: Request,
    service_id: str,
    volume_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    try:
        await service_manager.delete_volume(db, service_id, volume_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Volume deleted"}'},
        )


# ---------------------------------------------------------------------------
# Volume file manager
# ---------------------------------------------------------------------------


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


async def _get_vol(db: aiosqlite.Connection, service_id: str, volume_id: str):
    vols = await service_manager.list_volumes(db, service_id)
    for v in vols:
        if v.id == volume_id:
            return v
    raise HTTPException(404, "Volume not found")



def _mode_bits(full: str) -> dict:
    """Return rwx bits for owner/group/other as booleans."""
    try:
        m = os.stat(full).st_mode
    except OSError:
        return {"ur": False, "uw": False, "ux": False,
                "gr": False, "gw": False, "gx": False,
                "or": False, "ow": False, "ox": False, "octal": "???"}
    return {
        "ur": bool(m & 0o400), "uw": bool(m & 0o200), "ux": bool(m & 0o100),
        "gr": bool(m & 0o040), "gw": bool(m & 0o020), "gx": bool(m & 0o010),
        "or": bool(m & 0o004), "ow": bool(m & 0o002), "ox": bool(m & 0o001),
        "octal": oct(m & 0o777)[2:],
    }


def _browse_ctx(service_id: str, vol, path: str, target: str) -> dict:
    """Build template context for the volume browser."""
    entries = []
    for name in sorted(os.listdir(target), key=lambda n: (not os.path.isdir(os.path.join(target, n)), n.lower())):
        full = os.path.join(target, name)
        is_dir = os.path.isdir(full)
        try:
            size = None if is_dir else os.path.getsize(full)
        except OSError:
            size = None
        entries.append({
            "name": name,
            "type": "dir" if is_dir else "file",
            "size_fmt": "" if size is None else _fmt_size(size),
            "is_text": (not is_dir) and _is_text(full),
            "mode": _mode_bits(full),
            "selinux_type": get_file_context_type(full),
        })
    base = os.path.realpath(vol.host_path)
    rel = "/" + os.path.relpath(target, base).replace("\\", "/")
    if rel == "/.":
        rel = "/"
    parent = str(PurePosixPath(rel).parent) if rel != "/" else None
    return {
        "service_id": service_id,
        "volume": vol,
        "path": rel,
        "parent": parent,
        "entries": entries,
    }


@router.get("/api/services/{service_id}/volumes/{volume_id}/browse")
async def volume_browse(
    request: Request,
    service_id: str,
    volume_id: str,
    path: str = "/",
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, service_id, volume_id)
    try:
        target = _resolve_vol_path(vol.host_path, path)
    except ValueError:
        raise HTTPException(400, "Invalid path")
    if not os.path.isdir(target):
        raise HTTPException(404, "Directory not found")
    ctx = _browse_ctx(service_id, vol, path, target)
    return _TEMPLATES.TemplateResponse("partials/volume_browser.html", {"request": request, **ctx})


@router.get("/api/services/{service_id}/volumes/{volume_id}/file")
async def volume_get_file(
    request: Request,
    service_id: str,
    volume_id: str,
    path: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, service_id, volume_id)
    try:
        target = _resolve_vol_path(vol.host_path, path)
    except ValueError:
        raise HTTPException(400, "Invalid path")
    is_new = not os.path.exists(target)
    if not is_new and not os.path.isfile(target):
        raise HTTPException(400, "Not a file")
    if not is_new and not _is_text(target):
        raise HTTPException(400, "Binary files cannot be edited as text")
    content = "" if is_new else open(target).read()
    dir_path = str(PurePosixPath(path).parent)
    return _TEMPLATES.TemplateResponse("partials/volume_file_editor.html", {
        "request": request,
        "service_id": service_id,
        "volume": vol,
        "path": path,
        "dir_path": dir_path,
        "content": content,
        "is_new": is_new,
    })


@router.put("/api/services/{service_id}/volumes/{volume_id}/file")
async def volume_save_file(
    request: Request,
    service_id: str,
    volume_id: str,
    path: str,
    content: str = Form(default=""),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, service_id, volume_id)
    try:
        target = _resolve_vol_path(vol.host_path, path)
    except ValueError:
        raise HTTPException(400, "Invalid path")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as f:
        f.write(content)
    user_manager.chown_to_service_user(service_id, target)
    relabel(target)
    dir_path = str(PurePosixPath(path).parent)
    return _TEMPLATES.TemplateResponse("partials/volume_file_editor.html", {
        "request": request,
        "service_id": service_id,
        "volume": vol,
        "path": path,
        "dir_path": dir_path,
        "content": content,
        "is_new": False,
    }, headers={"HX-Trigger": '{"showToast": "Saved"}'})


@router.post("/api/services/{service_id}/volumes/{volume_id}/upload")
async def volume_upload(
    request: Request,
    service_id: str,
    volume_id: str,
    path: str = "/",
    file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, service_id, volume_id)
    try:
        target_dir = _resolve_vol_path(vol.host_path, path)
    except ValueError:
        raise HTTPException(400, "Invalid path")
    if not os.path.isdir(target_dir):
        raise HTTPException(400, "Target is not a directory")
    filename = os.path.basename(file.filename or "upload")
    if not filename:
        raise HTTPException(400, "Empty filename")
    dest = os.path.join(target_dir, filename)
    try:
        _resolve_vol_path(vol.host_path, os.path.relpath(dest, os.path.realpath(vol.host_path)))
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    with open(dest, "wb") as f:
        f.write(await file.read())
    user_manager.chown_to_service_user(service_id, dest)
    relabel(dest)
    ctx = _browse_ctx(service_id, vol, path, target_dir)
    return _TEMPLATES.TemplateResponse("partials/volume_browser.html", {
        "request": request, **ctx,
    }, headers={"HX-Trigger": f'{{"showToast": "Uploaded {filename}"}}'})


@router.delete("/api/services/{service_id}/volumes/{volume_id}/file")
async def volume_delete_entry(
    request: Request,
    service_id: str,
    volume_id: str,
    path: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, service_id, volume_id)
    try:
        target = _resolve_vol_path(vol.host_path, path)
    except ValueError:
        raise HTTPException(400, "Invalid path")
    if not os.path.exists(target):
        raise HTTPException(404, "Not found")
    if os.path.isdir(target):
        shutil.rmtree(target)
    else:
        os.unlink(target)
    dir_path = str(PurePosixPath(path).parent)
    try:
        target_dir = _resolve_vol_path(vol.host_path, dir_path)
    except ValueError:
        target_dir = os.path.realpath(vol.host_path)
    ctx = _browse_ctx(service_id, vol, dir_path, target_dir)
    return _TEMPLATES.TemplateResponse("partials/volume_browser.html", {"request": request, **ctx})


@router.post("/api/services/{service_id}/volumes/{volume_id}/mkdir")
async def volume_mkdir(
    request: Request,
    service_id: str,
    volume_id: str,
    path: str = Form(...),
    name: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    vol = await _get_vol(db, service_id, volume_id)
    new_rel = str(PurePosixPath(path) / name)
    try:
        target = _resolve_vol_path(vol.host_path, new_rel)
    except ValueError:
        raise HTTPException(400, "Invalid path")
    os.makedirs(target, exist_ok=True)
    user_manager.chown_to_service_user(service_id, target)
    relabel(target)
    try:
        parent_target = _resolve_vol_path(vol.host_path, path)
    except ValueError:
        parent_target = os.path.realpath(vol.host_path)
    ctx = _browse_ctx(service_id, vol, path, parent_target)
    return _TEMPLATES.TemplateResponse("partials/volume_browser.html", {"request": request, **ctx})


@router.patch("/api/services/{service_id}/volumes/{volume_id}/chmod")
async def volume_chmod(
    request: Request,
    service_id: str,
    volume_id: str,
    path: str = Form(...),
    mode: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    """Change permissions of a single file or directory."""
    vol = await _get_vol(db, service_id, volume_id)
    try:
        target = _resolve_vol_path(vol.host_path, path)
    except ValueError:
        raise HTTPException(400, "Invalid path")
    if not os.path.exists(target):
        raise HTTPException(404, "Path not found")
    try:
        mode_int = int(mode, 8)
        if not (0 <= mode_int <= 0o777):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Invalid mode — expected octal string like 644")
    os.chmod(target, mode_int)
    dir_path = str(PurePosixPath(path).parent)
    try:
        dir_target = _resolve_vol_path(vol.host_path, dir_path)
    except ValueError:
        dir_target = os.path.realpath(vol.host_path)
    ctx = _browse_ctx(service_id, vol, dir_path, dir_target)
    return _TEMPLATES.TemplateResponse("partials/volume_browser.html", {"request": request, **ctx})


@router.get("/api/services/{service_id}/volumes/{volume_id}/archive")
async def volume_archive(
    service_id: str,
    volume_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    """Download all volume files as a zip archive."""
    vol = await _get_vol(db, service_id, volume_id)
    base = os.path.realpath(vol.host_path)

    def _build_zip() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for dirpath, _dirnames, filenames in os.walk(base):
                for fname in filenames:
                    abs_path = os.path.join(dirpath, fname)
                    arcname = os.path.relpath(abs_path, base)
                    zf.write(abs_path, arcname)
        return buf.getvalue()

    data = await __import__("asyncio").get_event_loop().run_in_executor(None, _build_zip)
    filename = f"{service_id}-{vol.name}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/services/{service_id}/volumes/{volume_id}/restore")
async def volume_restore(
    request: Request,
    service_id: str,
    volume_id: str,
    file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    """Extract a zip or tar.gz archive into the volume root."""
    vol = await _get_vol(db, service_id, volume_id)
    base = os.path.realpath(vol.host_path)

    data = await file.read()
    fname = (file.filename or "").lower()

    def _extract_zip(raw: bytes, dest: str):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for member in zf.infolist():
                # Zip-slip prevention
                member_path = os.path.realpath(os.path.join(dest, member.filename))
                if not member_path.startswith(dest + os.sep) and member_path != dest:
                    raise ValueError(f"Unsafe path in archive: {member.filename}")
                zf.extract(member, dest)

    def _extract_tar(raw: bytes, dest: str):
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            for member in tf.getmembers():
                member_path = os.path.realpath(os.path.join(dest, member.name))
                if not member_path.startswith(dest + os.sep) and member_path != dest:
                    raise ValueError(f"Unsafe path in archive: {member.name}")
            tf.extractall(dest)

    def _extract():
        # Detect format by magic bytes then fall back to filename
        if data[:2] == b"PK":
            _extract_zip(data, base)
        elif data[:2] in (b"\x1f\x8b", b"BZ") or fname.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
            _extract_tar(data, base)
        elif fname.endswith(".zip"):
            _extract_zip(data, base)
        else:
            raise ValueError("Unsupported archive format. Upload a .zip or .tar.gz file.")

    try:
        await __import__("asyncio").get_event_loop().run_in_executor(None, _extract)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"Failed to extract archive: {exc}")

    user_manager.chown_to_service_user(service_id, base)
    apply_context(base, vol.selinux_context)
    ctx = _browse_ctx(service_id, vol, "/", base)
    return _TEMPLATES.TemplateResponse("partials/volume_browser.html", {"request": request, **ctx})


# ---------------------------------------------------------------------------
# Logs (SSE)
# ---------------------------------------------------------------------------


@router.get("/api/services/{service_id}/containers/{container_name}/logs")
async def stream_logs(
    service_id: str,
    container_name: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    unit = f"{container_name}.service"

    async def event_stream():
        async for line in systemd_manager.stream_journal(service_id, unit):
            yield f"data: {line}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Form partials (loaded into modals)
# ---------------------------------------------------------------------------


@router.get("/api/services/{service_id}/containers/form")
async def container_create_form(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):

    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    return _TEMPLATES.TemplateResponse(
        "partials/container_form.html",
        {
            "request": request,
            "service": svc,
            "container": None,
            "volume_mounts": [],
            "bind_mounts": [],
            "env_pairs": [],
            "ports": [],
            "uid_map": [],
            "gid_map": [],
            "other_containers": [c.name for c in svc.containers],
        },
    )


@router.get("/api/services/{service_id}/containers/{container_id}/form")
async def container_edit_form(
    request: Request,
    service_id: str,
    container_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    svc = await service_manager.get_service(db, service_id)
    container = await service_manager.get_container(db, container_id)
    if svc is None or container is None:
        raise HTTPException(status_code=404)
    return _TEMPLATES.TemplateResponse(
        "partials/container_form.html",
        {
            "request": request,
            "service": svc,
            "container": container,
            "volume_mounts": [vm.model_dump() for vm in container.volumes],
            "bind_mounts": [bm.model_dump() for bm in container.bind_mounts],
            "env_pairs": list(container.environment.items()),
            "ports": container.ports,
            "uid_map": container.uid_map,
            "gid_map": container.gid_map,
            "other_containers": [c.name for c in svc.containers if c.id != container_id],
        },
    )


@router.get("/api/services/{service_id}/volumes/form")
async def volume_create_form(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404)
    return _TEMPLATES.TemplateResponse(
        "partials/volume_form.html",
        _svc_ctx(request, svc),
    )


# ---------------------------------------------------------------------------
# Registry login
# ---------------------------------------------------------------------------


@router.get("/api/services/{service_id}/registry-logins")
async def get_registry_logins(
    request: Request,
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404)
    logins = user_manager.list_registry_logins(service_id)
    return _TEMPLATES.TemplateResponse(
        "partials/registry_logins.html",
        {"request": request, "service_id": service_id, "logins": logins},
    )


@router.post("/api/services/{service_id}/registry-login")
async def post_registry_login(
    request: Request,
    service_id: str,
    registry: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404)
    try:
        loop = __import__("asyncio").get_event_loop()
        await loop.run_in_executor(
            None, user_manager.registry_login, service_id, registry, username, password
        )
    except RuntimeError as exc:
        logins = user_manager.list_registry_logins(service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/registry_logins.html",
            {"request": request, "service_id": service_id, "logins": logins, "error": str(exc)},
        )
    logins = user_manager.list_registry_logins(service_id)
    return _TEMPLATES.TemplateResponse(
        "partials/registry_logins.html",
        {"request": request, "service_id": service_id, "logins": logins},
    )


@router.post("/api/services/{service_id}/registry-logout")
async def post_registry_logout(
    request: Request,
    service_id: str,
    registry: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404)
    try:
        loop = __import__("asyncio").get_event_loop()
        await loop.run_in_executor(
            None, user_manager.registry_logout, service_id, registry
        )
    except RuntimeError as exc:
        logins = user_manager.list_registry_logins(service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/registry_logins.html",
            {"request": request, "service_id": service_id, "logins": logins, "error": str(exc)},
        )
    logins = user_manager.list_registry_logins(service_id)
    return _TEMPLATES.TemplateResponse(
        "partials/registry_logins.html",
        {"request": request, "service_id": service_id, "logins": logins},
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@router.get("/api/events")
async def list_events(
    request: Request,
    limit: int = 50,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    async with db.execute(
        "SELECT * FROM system_events ORDER BY created_at DESC LIMIT ?", (limit,)
    ) as cur:
        rows = await cur.fetchall()
    events = [dict(r) for r in rows]
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            "partials/events.html",
            {"request": request, "events": events},
        )
    return events

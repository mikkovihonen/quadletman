"""REST API + HTMX-aware routes for quadletman."""

import asyncio
import fcntl
import io
import json
import logging
import os
import pty
import re
import shutil
import struct
import subprocess
import tarfile
import termios
import urllib.parse
import zipfile
from contextlib import suppress
from pathlib import Path, PurePosixPath

import aiosqlite
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    status,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from ..auth import require_auth
from ..database import get_db
from ..models import (
    ContainerCreate,
    ImageUnitCreate,
    PodCreate,
    ServiceCreate,
    ServiceNetworkUpdate,
    ServiceUpdate,
    VolumeCreate,
)
from ..podman_version import get_features
from ..services import metrics, service_manager, systemd_manager, user_manager
from ..services.selinux import apply_context, get_file_context_type, is_selinux_active, relabel
from ..session import get_session

logger = logging.getLogger(__name__)
router = APIRouter()

# Maximum size for file uploads (archive restore + single file upload).
_MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MiB

# Environment files are tiny — 64 KiB is generous.
_MAX_ENVFILE_BYTES = 64 * 1024

# Allowed exec_user values for the terminal WebSocket: "root" or a non-negative integer UID.
_EXEC_USER_RE = re.compile(r"^(root|\d+)$")

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


@router.post("/api/logout")
async def logout(qm_session: str = Cookie(default=None)):
    """Invalidate the server-side session and clear the session cookie."""
    if qm_session:
        from ..session import delete_session

        delete_session(qm_session)
    resp = Response(status_code=204)
    resp.delete_cookie("qm_session")
    resp.delete_cookie("qm_csrf")
    return resp


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
        total = (
            sum(x["bytes"] for x in d["images"])
            + sum(x["bytes"] for x in d["overlays"])
            + d["volumes_total"]
            + d["config_bytes"]
        )
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

    features = get_features()
    if not features.bundle:
        raise HTTPException(
            status_code=400,
            detail=f"Bundle import requires Podman 5.8+ (detected: {features.version_str})",
        )

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

    # Import pods first (containers may reference them)
    for pp in parse_result.pods:
        try:
            await service_manager.add_pod(
                db,
                service_id,
                PodCreate(name=pp.name, network=pp.network, publish_ports=pp.publish_ports),
            )
        except Exception as exc:
            logger.error("import: failed to add pod %s: %s", pp.name, exc)
            import_errors.append({"pod": pp.name, "error": str(exc)})

    # Import image units
    for pi in parse_result.image_units:
        try:
            await service_manager.add_image_unit(
                db,
                service_id,
                ImageUnitCreate(
                    name=pi.name,
                    image=pi.image,
                    pull_policy=pi.pull_policy,
                    auth_file=pi.auth_file,
                ),
            )
        except Exception as exc:
            logger.error("import: failed to add image unit %s: %s", pi.name, exc)
            import_errors.append({"image_unit": pi.name, "error": str(exc)})

    # Import quadlet-managed volume units (host-directory volumes must be added via UI)
    for pv in parse_result.volume_units:
        try:
            await service_manager.add_volume(
                db,
                service_id,
                VolumeCreate(
                    name=pv.name,
                    use_quadlet=True,
                    vol_driver=pv.vol_driver,
                    vol_device=pv.vol_device,
                    vol_options=pv.vol_options,
                    vol_copy=pv.vol_copy,
                ),
            )
        except Exception as exc:
            logger.error("import: failed to add volume unit %s: %s", pv.name, exc)
            import_errors.append({"volume": pv.name, "error": str(exc)})

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
                    exec_start_post=pc.exec_start_post,
                    exec_stop=pc.exec_stop,
                    memory_limit=pc.memory_limit,
                    cpu_quota=pc.cpu_quota,
                    depends_on=pc.depends_on,
                    apparmor_profile=pc.apparmor_profile,
                    pod_name=pc.pod_name,
                    log_driver=pc.log_driver,
                    working_dir=pc.working_dir,
                    hostname=pc.hostname,
                    no_new_privileges=pc.no_new_privileges,
                    read_only=pc.read_only,
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


@router.put("/api/services/{service_id}/network")
async def update_service_network(
    request: Request,
    service_id: str,
    net_driver: str = Form(""),
    net_subnet: str = Form(""),
    net_gateway: str = Form(""),
    net_ipv6: str = Form(""),
    net_internal: str = Form(""),
    net_dns_enabled: str = Form(""),
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    data = ServiceNetworkUpdate(
        net_driver=net_driver,
        net_subnet=net_subnet,
        net_gateway=net_gateway,
        net_ipv6=net_ipv6 == "true",
        net_internal=net_internal == "true",
        net_dns_enabled=net_dns_enabled == "true",
    )
    svc = await service_manager.update_service_network(db, service_id, data)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Network config updated"}'},
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
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(service_id)}.quadlets"
        },
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
            headers={
                "HX-Trigger": _json.dumps(
                    {"showToast": toast, "toastType": "error" if errors else "success"}
                )
            },
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
            headers={
                "HX-Trigger": _json.dumps(
                    {"showToast": toast, "toastType": "error" if errors else "success"}
                )
            },
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
            headers={
                "HX-Trigger": _json.dumps(
                    {"showToast": toast, "toastType": "error" if errors else "success"}
                )
            },
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


@router.get("/api/services/{service_id}/status-dot")
async def get_service_status_dot(
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    """Return a tiny colored status dot for the sidebar service list."""
    statuses = await service_manager.get_status(db, service_id)
    active = [s for s in statuses if s["active_state"] == "active"]
    failed = [s for s in statuses if s["active_state"] == "failed"]
    transitioning = [s for s in statuses if s["active_state"] in ("activating", "deactivating")]
    if not statuses:
        color = "bg-gray-600"
        title = "no units"
    elif failed:
        color = "bg-red-500"
        title = f"{len(failed)} failed"
    elif transitioning:
        color = "bg-yellow-400 animate-pulse"
        title = "transitioning"
    elif len(active) == len(statuses):
        color = "bg-green-500"
        title = "all running"
    elif active:
        color = "bg-yellow-500"
        title = f"{len(active)}/{len(statuses)} running"
    else:
        color = "bg-gray-500"
        title = "stopped"
    return Response(
        content=(
            f'<span id="svc-dot-{service_id}" '
            f'hx-get="/api/services/{service_id}/status-dot" '
            f'hx-trigger="every 10s" hx-swap="outerHTML" '
            f'class="w-2 h-2 rounded-full {color} inline-block shrink-0" '
            f'title="{title}"></span>'
        ),
        media_type="text/html",
    )


@router.get("/api/services/{service_id}/metrics")
async def get_service_metrics(
    service_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    info = user_manager.get_user_info(service_id)
    uid = info.get("uid") if info else None
    if uid is None:
        return {
            "service_id": service_id,
            "cpu_percent": 0,
            "mem_bytes": 0,
            "proc_count": 0,
            "disk_bytes": 0,
        }
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

    from fastapi.responses import HTMLResponse

    from ..services.metrics import _VOLUMES_BASE, _dir_size

    loop = asyncio.get_event_loop()
    path = os.path.join(_VOLUMES_BASE, service_id, volume_name)
    size = await loop.run_in_executor(None, _dir_size, path)
    if _is_htmx(request):
        b = size
        if b >= 1_073_741_824:
            txt = f"{b / 1_073_741_824:.1f} GB"
        elif b >= 1_048_576:
            txt = f"{b / 1_048_576:.1f} MB"
        elif b >= 1024:
            txt = f"{b / 1024:.1f} KB"
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
# Container env file upload / preview
# ---------------------------------------------------------------------------


@router.post("/api/services/{service_id}/containers/{container_id}/envfile")
async def upload_container_envfile(
    service_id: str,
    container_id: str,
    file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    _user: str = Depends(require_auth),
) -> JSONResponse:
    svc = await service_manager.get_service(db, service_id)
    container = next((c for c in svc.containers if c.id == container_id), None)
    if container is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Container not found")

    raw = await file.read(_MAX_ENVFILE_BYTES + 1)
    if len(raw) > _MAX_ENVFILE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Env file exceeds {_MAX_ENVFILE_BYTES // 1024} KiB limit",
        )
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Env file must be valid UTF-8") from exc

    loop = asyncio.get_event_loop()
    home = await loop.run_in_executor(None, user_manager.get_home, service_id)
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
    await loop.run_in_executor(None, user_manager.chown_to_service_user, service_id, dest)
    return JSONResponse({"path": dest})


@router.get("/api/services/{service_id}/envfile")
async def preview_service_envfile(
    service_id: str,
    path: str = Query(...),
    db: aiosqlite.Connection = Depends(get_db),
    _user: str = Depends(require_auth),
) -> JSONResponse:
    loop = asyncio.get_event_loop()
    try:
        home = await loop.run_in_executor(None, user_manager.get_home, service_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service user not found") from exc

    real_home = os.path.realpath(home)
    real_path = os.path.realpath(path)
    if real_path != real_home and not real_path.startswith(real_home + os.sep):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Path is outside the service user home directory"
        )
    if not os.path.isfile(real_path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    def _read() -> str:
        with open(real_path) as fh:
            return fh.read(_MAX_ENVFILE_BYTES)

    try:
        content = await loop.run_in_executor(None, _read)
    except OSError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not read file") from exc

    lines = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        lines.append({"key": key.strip(), "value": value})

    return JSONResponse({"lines": lines})


@router.delete("/api/services/{service_id}/containers/{container_id}/envfile")
async def delete_container_envfile(
    service_id: str,
    container_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    _user: str = Depends(require_auth),
) -> JSONResponse:
    svc = await service_manager.get_service(db, service_id)
    container = next((c for c in svc.containers if c.id == container_id), None)
    if container is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Container not found")

    loop = asyncio.get_event_loop()
    try:
        home = await loop.run_in_executor(None, user_manager.get_home, service_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service user not found") from exc

    env_path = os.path.join(home, "env", f"{container.name}.env")
    real_home = os.path.realpath(home)
    real_path = os.path.realpath(env_path)
    if real_path != real_home and not real_path.startswith(real_home + os.sep):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Path is outside the service user home directory"
        )

    def _delete() -> None:
        with suppress(FileNotFoundError):
            os.unlink(real_path)

    await loop.run_in_executor(None, _delete)
    return JSONResponse({"ok": True})


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
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Volume deleted"}'},
        )


# ---------------------------------------------------------------------------
# Pod routes (P2)
# ---------------------------------------------------------------------------


@router.post("/api/services/{service_id}/pods", status_code=201)
async def add_pod(
    request: Request,
    service_id: str,
    data: PodCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    features = get_features()
    if not features.quadlet:
        raise HTTPException(
            status_code=400,
            detail=f"Requires Podman 4.4+ (detected: {features.version_str})",
        )
    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    try:
        pod = await service_manager.add_pod(db, service_id, data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Pod added"}'},
        )
    return pod.model_dump()


@router.delete("/api/services/{service_id}/pods/{pod_id}", status_code=204)
async def delete_pod(
    request: Request,
    service_id: str,
    pod_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    try:
        await service_manager.delete_pod(db, service_id, pod_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Pod removed"}'},
        )


# ---------------------------------------------------------------------------
# Image unit routes (P2)
# ---------------------------------------------------------------------------


@router.post("/api/services/{service_id}/image-units", status_code=201)
async def add_image_unit(
    request: Request,
    service_id: str,
    data: ImageUnitCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    features = get_features()
    if not features.quadlet:
        raise HTTPException(
            status_code=400,
            detail=f"Requires Podman 4.4+ (detected: {features.version_str})",
        )
    svc = await service_manager.get_service(db, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    try:
        iu = await service_manager.add_image_unit(db, service_id, data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Image unit added"}'},
        )
    return iu.model_dump()


@router.delete("/api/services/{service_id}/image-units/{image_unit_id}", status_code=204)
async def delete_image_unit(
    request: Request,
    service_id: str,
    image_unit_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: str = Depends(require_auth),
):
    await service_manager.delete_image_unit(db, service_id, image_unit_id)
    if _is_htmx(request):
        svc = await service_manager.get_service(db, service_id)
        return _TEMPLATES.TemplateResponse(
            "partials/service_detail.html",
            _svc_ctx(request, svc),
            headers={"HX-Trigger": '{"showToast": "Image unit removed"}'},
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


def _browse_ctx(service_id: str, vol, path: str, target: str) -> dict:
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
                "selinux_type": get_file_context_type(full),
            }
        )
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
    except ValueError as exc:
        raise HTTPException(400, "Invalid path") from exc
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
    except ValueError as exc:
        raise HTTPException(400, "Invalid path") from exc
    is_new = not os.path.exists(target)
    if not is_new and not os.path.isfile(target):
        raise HTTPException(400, "Not a file")
    if not is_new and not _is_text(target):
        raise HTTPException(400, "Binary files cannot be edited as text")
    if is_new:
        content = ""
    else:
        with open(target) as _f:
            content = _f.read()
    dir_path = str(PurePosixPath(path).parent)
    return _TEMPLATES.TemplateResponse(
        "partials/volume_file_editor.html",
        {
            "request": request,
            "service_id": service_id,
            "volume": vol,
            "path": path,
            "dir_path": dir_path,
            "content": content,
            "is_new": is_new,
        },
    )


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
    except ValueError as exc:
        raise HTTPException(400, "Invalid path") from exc
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        raise
    user_manager.chown_to_service_user(service_id, target)
    relabel(target)
    dir_path = str(PurePosixPath(path).parent)
    return _TEMPLATES.TemplateResponse(
        "partials/volume_file_editor.html",
        {
            "request": request,
            "service_id": service_id,
            "volume": vol,
            "path": path,
            "dir_path": dir_path,
            "content": content,
            "is_new": False,
        },
        headers={"HX-Trigger": '{"showToast": "Saved"}'},
    )


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
    except ValueError as exc:
        raise HTTPException(400, "Invalid path") from exc
    if not os.path.isdir(target_dir):
        raise HTTPException(400, "Target is not a directory")
    filename = re.sub(r"[^\w.\-]", "_", os.path.basename(file.filename or "upload"))
    if not filename:
        raise HTTPException(400, "Empty filename")
    dest = os.path.join(target_dir, filename)
    try:
        _resolve_vol_path(vol.host_path, os.path.relpath(dest, os.path.realpath(vol.host_path)))
    except ValueError as exc:
        raise HTTPException(400, "Invalid filename") from exc
    raw = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds maximum upload size of {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB",
        )
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        raise
    user_manager.chown_to_service_user(service_id, dest)
    relabel(dest)
    ctx = _browse_ctx(service_id, vol, path, target_dir)
    return _TEMPLATES.TemplateResponse(
        "partials/volume_browser.html",
        {
            "request": request,
            **ctx,
        },
        headers={"HX-Trigger": f'{{"showToast": "Uploaded {filename}"}}'},
    )


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
    except ValueError as exc:
        raise HTTPException(400, "Invalid path") from exc
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
    except ValueError as exc:
        raise HTTPException(400, "Invalid path") from exc
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
    except ValueError as exc:
        raise HTTPException(400, "Invalid path") from exc
    if not os.path.exists(target):
        raise HTTPException(404, "Path not found")
    try:
        mode_int = int(mode, 8)
        if not (0 <= mode_int <= 0o777):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(400, "Invalid mode — expected octal string like 644") from exc
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
    filename = f"{service_id}-{vol.name}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
        },
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

    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Archive exceeds maximum upload size of {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB",
        )
    fname = (file.filename or "").lower()

    def _extract_zip(raw: bytes, dest: str):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for member in zf.infolist():
                # Zip-slip prevention: check BEFORE extracting this member.
                # Use realpath so that any symlinks already written by prior
                # members are followed — catching symlink-based traversal.
                member_path = os.path.realpath(os.path.join(dest, member.filename))
                if not member_path.startswith(dest + os.sep) and member_path != dest:
                    raise ValueError(f"Unsafe path in archive: {member.filename}")
                zf.extract(member, dest)
                # Re-validate after extraction in case the member itself was a
                # symlink that now resolves outside dest.
                member_path = os.path.realpath(os.path.join(dest, member.filename))
                if not member_path.startswith(dest + os.sep) and member_path != dest:
                    os.unlink(os.path.join(dest, member.filename))
                    raise ValueError(f"Unsafe symlink in archive: {member.filename}")

    def _extract_tar(raw: bytes, dest: str):
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            # Python 3.12+ provides a safe extraction filter that blocks
            # absolute paths, symlink traversal, and dangerous member types.
            if hasattr(tarfile, "data_filter"):
                tf.extractall(dest, filter="data")
            else:
                # Fallback: extract member-by-member, re-checking realpath
                # AFTER each member so symlinks created by prior members are
                # caught before subsequent members follow them.
                for member in tf.getmembers():
                    member_path = os.path.realpath(os.path.join(dest, member.name))
                    if not member_path.startswith(dest + os.sep) and member_path != dest:
                        raise ValueError(f"Unsafe path in archive: {member.name}")
                    tf.extract(member, dest)
                    # Re-check after write — catches symlinks that now point outside.
                    member_path = os.path.realpath(os.path.join(dest, member.name))
                    if not member_path.startswith(dest + os.sep) and member_path != dest:
                        extracted = os.path.join(dest, member.name)
                        if os.path.lexists(extracted):
                            os.unlink(extracted)
                        raise ValueError(f"Unsafe symlink in archive: {member.name}")

    def _extract():
        # Detect format by magic bytes then fall back to filename
        if data[:2] == b"PK":
            _extract_zip(data, base)
        elif data[:2] in (b"\x1f\x8b", b"BZ") or fname.endswith(
            (".tar.gz", ".tgz", ".tar.bz2", ".tar")
        ):
            _extract_tar(data, base)
        elif fname.endswith(".zip"):
            _extract_zip(data, base)
        else:
            raise ValueError("Unsupported archive format. Upload a .zip or .tar.gz file.")

    try:
        await __import__("asyncio").get_event_loop().run_in_executor(None, _extract)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"Failed to extract archive: {exc}") from exc

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
# Terminal (WebSocket / PTY)
# ---------------------------------------------------------------------------


@router.websocket("/api/services/{service_id}/containers/{container_name}/terminal")
async def container_terminal(
    websocket: WebSocket,
    service_id: str,
    container_name: str,
    exec_user: str | None = Query(default=None),
):
    """WebSocket endpoint that bridges an xterm.js client to podman exec inside a container.

    Authentication is validated manually from the qm_session cookie because FastAPI's
    Depends() injection is not available for WebSocket routes the same way as HTTP.

    CSRF: the double-submit cookie used for HTTP cannot be applied here (the browser
    WebSocket API does not allow sending custom headers). Instead we validate the Origin
    header, which the browser always sets on WebSocket upgrades and JavaScript cannot
    spoof. Connections whose origin does not match the server host are rejected.
    """
    # Origin check — CSRF defence for WebSocket
    origin = websocket.headers.get("origin", "")
    host = websocket.headers.get("host", "")
    # Strip the scheme from origin for comparison (ws/wss vs http/https share the same host)
    origin_host = origin.split("://", 1)[-1] if "://" in origin else origin
    if not origin_host or origin_host != host:
        await websocket.close(code=4403)
        return

    qm_session = websocket.cookies.get("qm_session")
    if not qm_session or not get_session(qm_session):
        await websocket.close(code=4401)
        return

    if exec_user is not None and not _EXEC_USER_RE.match(exec_user):
        await websocket.close(code=4400)
        return

    await websocket.accept()
    loop = asyncio.get_event_loop()

    # Quadlet sets ContainerName={service_id}-{container_name} in the unit file
    podman_container_name = f"{service_id}-{container_name}"
    cmd = systemd_manager.exec_pty_cmd(service_id, podman_container_name, exec_user)
    master_fd: int | None = None
    proc: subprocess.Popen | None = None

    try:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True, cwd="/"
        )
        os.close(slave_fd)
    except OSError as exc:
        err_msg = f"\r\n\x1b[31m[exec failed: {exc}]\x1b[0m\r\n"
        with suppress(Exception):
            await websocket.send_bytes(err_msg.encode())
        await websocket.close(code=1011)
        if master_fd is not None:
            with suppress(OSError):
                os.close(master_fd)
        return

    async def _read_loop() -> None:
        try:
            while True:
                data = await loop.run_in_executor(None, os.read, master_fd, 4096)
                if not data:
                    break
                await websocket.send_bytes(data)
        except OSError:
            pass

    async def _write_loop() -> None:
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    await loop.run_in_executor(None, os.write, master_fd, msg["bytes"])
                elif msg.get("text"):
                    with suppress(json.JSONDecodeError, KeyError, ValueError, TypeError):
                        payload = json.loads(msg["text"])
                        if payload.get("type") == "resize":
                            cols = int(payload["cols"])
                            rows = int(payload["rows"])
                            winsize = struct.pack("HHHH", rows, cols, 0, 0)
                            await loop.run_in_executor(
                                None, fcntl.ioctl, master_fd, termios.TIOCSWINSZ, winsize
                            )
        except Exception:
            pass

    read_task = asyncio.create_task(_read_loop())
    write_task = asyncio.create_task(_write_loop())
    _, pending = await asyncio.wait([read_task, write_task], return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    with suppress(asyncio.CancelledError):
        await asyncio.gather(*pending)

    with suppress(OSError):
        os.close(master_fd)
    with suppress(Exception):
        proc.kill()
        proc.wait()


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
        await loop.run_in_executor(None, user_manager.registry_logout, service_id, registry)
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

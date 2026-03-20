"""Compartment-level routes."""

import asyncio
import csv
import io
import logging
import urllib.parse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..db.orm import ContainerRestartStatsRow, MetricsHistoryRow
from ..i18n import gettext as _t
from ..models import (
    CompartmentCreate,
    CompartmentNetworkUpdate,
    CompartmentUpdate,
    ContainerCreate,
    ImageUnitCreate,
    NotificationHookCreate,
    PodCreate,
    VolumeCreate,
)
from ..models.sanitized import SafeIpAddress, SafeSlug, SafeStr, SafeUnitName, log_safe
from ..podman_version import get_features
from ..services import compartment_manager, metrics, user_manager
from ._helpers import (
    _comp_ctx,
    _is_htmx,
    _require_compartment,
    _status_dot_context,
    _toast_trigger,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/compartments")
async def list_compartments(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    services = await compartment_manager.list_compartments(db)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_list.html",
            {"compartments": services},
        )
    return [s.model_dump() for s in services]


@router.post("/api/compartments", status_code=status.HTTP_201_CREATED)
async def create_compartment(
    request: Request,
    data: CompartmentCreate,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    existing = await compartment_manager.get_compartment(db, data.id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=_t("Compartment '%(id)s' already exists") % {"id": data.id},
        )
    try:
        comp = await compartment_manager.create_compartment(db, data)
    except Exception as exc:
        logger.error("Failed to create service %s: %s", log_safe(data.id), log_safe(exc))
        raise HTTPException(status_code=500, detail=_t("Failed to create compartment")) from exc

    if _is_htmx(request):
        services = await compartment_manager.list_compartments(db)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_list.html",
            {"compartments": services},
            headers=_toast_trigger("Compartment created successfully"),
        )
    return comp.model_dump()


@router.post("/api/compartments/import", status_code=status.HTTP_201_CREATED)
async def import_compartment_bundle(
    compartment_id: SafeSlug = Form(...),
    description: SafeStr = Form(""),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    """Import a .quadlets bundle file as a new service."""
    from ..services.bundle_parser import parse_quadlets_bundle

    features = get_features()
    if not features.bundle:
        raise HTTPException(
            status_code=400,
            detail=_t("Bundle import requires Podman 5.8+ (detected: %(v)s)")
            % {"v": features.version_str},
        )

    try:
        raw = await file.read()
        content = raw.decode("utf-8")
    except Exception as exc:
        logger.warning("Bundle import: could not read uploaded file: %s", exc)
        raise HTTPException(status_code=422, detail=_t("Could not read uploaded file")) from exc

    from ..models.sanitized import SafeMultilineStr

    parse_result = parse_quadlets_bundle(SafeMultilineStr.of(content, "import_compartment_bundle"))
    if not parse_result.containers:
        raise HTTPException(
            status_code=422,
            detail=_t("No [Container] sections found in bundle"),
        )

    existing = await compartment_manager.get_compartment(db, compartment_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=_t("Compartment '%(id)s' already exists") % {"id": compartment_id},
        )

    try:
        await compartment_manager.create_compartment(
            db,
            CompartmentCreate(
                id=compartment_id,
                description=description,
            ),
        )
    except Exception as exc:
        logger.error("import: failed to create service %s: %s", log_safe(compartment_id), exc)
        raise HTTPException(status_code=500, detail=_t("Failed to create service")) from exc

    import_errors: list[dict] = []

    # Import pods first (containers may reference them)
    for pp in parse_result.pods:
        try:
            await compartment_manager.add_pod(
                db,
                compartment_id,
                PodCreate(name=pp.name, network=pp.network, publish_ports=pp.publish_ports),
            )
        except Exception as exc:
            logger.error("import: failed to add pod %s: %s", pp.name, exc)
            import_errors.append({"pod": pp.name, "error": str(exc)})

    # Import image units
    for pi in parse_result.image_units:
        try:
            await compartment_manager.add_image_unit(
                db,
                compartment_id,
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
            await compartment_manager.add_volume(
                db,
                compartment_id,
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
            await compartment_manager.add_container(
                db,
                compartment_id,
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

    result = (await compartment_manager.get_compartment(db, compartment_id)).model_dump()
    result["import_warnings"] = parse_result.warnings
    result["import_errors"] = import_errors
    return JSONResponse(status_code=201, content=result)


@router.get("/api/compartments/{compartment_id}")
async def get_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    if _is_htmx(request):
        statuses = await compartment_manager.get_status(db, compartment_id, comp.containers)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            {**await _comp_ctx(request, comp), "statuses": statuses},
        )
    return comp.model_dump()


@router.put("/api/compartments/{compartment_id}")
async def update_compartment(
    request: Request,
    compartment_id: SafeSlug,
    data: CompartmentUpdate,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    comp = await compartment_manager.update_compartment(db, compartment_id, data.description)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await _comp_ctx(request, comp),
            headers=_toast_trigger("Compartment updated"),
        )
    return comp.model_dump()


@router.put("/api/compartments/{compartment_id}/network")
async def update_compartment_network(
    request: Request,
    compartment_id: SafeSlug,
    net_driver: SafeStr = Form(""),
    net_subnet: SafeStr = Form(""),
    net_gateway: SafeStr = Form(""),
    net_ipv6: SafeStr = Form(""),
    net_internal: SafeStr = Form(""),
    net_dns_enabled: SafeStr = Form(""),
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    data = CompartmentNetworkUpdate(
        net_driver=net_driver,
        net_subnet=net_subnet,
        net_gateway=net_gateway,
        net_ipv6=net_ipv6 == "true",
        net_internal=net_internal == "true",
        net_dns_enabled=net_dns_enabled == "true",
    )
    comp = await compartment_manager.update_compartment_network(db, compartment_id, data)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await _comp_ctx(request, comp),
            headers=_toast_trigger("Network config updated"),
        )
    return comp.model_dump()


@router.delete("/api/compartments/{compartment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    try:
        await compartment_manager.delete_compartment(db, compartment_id)
    except Exception as exc:
        logger.error("Failed to delete service %s: %s", log_safe(compartment_id), exc)
        raise HTTPException(status_code=500, detail=_t("Failed to delete compartment")) from exc

    if _is_htmx(request):
        services = await compartment_manager.list_compartments(db)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_list.html",
            {"compartments": services},
            headers={"HX-Trigger": '{"showToast": "Compartment deleted", "clearDetail": true}'},
        )


@router.get("/api/compartments/{compartment_id}/export")
async def export_compartment(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    """Download the service's quadlet units as a .quadlets bundle file."""
    bundle = await compartment_manager.export_compartment_bundle(db, compartment_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    return Response(
        content=bundle,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(compartment_id)}.quadlets"
        },
    )


@router.post("/api/compartments/{compartment_id}/start")
async def start_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    errors = await compartment_manager.start_compartment(db, compartment_id)
    statuses = await compartment_manager.get_status(db, compartment_id)
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        toast = f"{len(errors)} unit(s) failed to start" if errors else "Compartment started"
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            {**await _comp_ctx(request, comp), "statuses": statuses, "errors": errors},
            headers=_toast_trigger(toast, error=bool(errors)),
        )
    return {"statuses": statuses, "errors": errors}


@router.post("/api/compartments/{compartment_id}/stop")
async def stop_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    errors = await compartment_manager.stop_compartment(db, compartment_id)
    statuses = await compartment_manager.get_status(db, compartment_id)
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        toast = f"{len(errors)} unit(s) failed to stop" if errors else "Compartment stopped"
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            {**await _comp_ctx(request, comp), "statuses": statuses, "errors": errors},
            headers=_toast_trigger(toast, error=bool(errors)),
        )
    return {"statuses": statuses, "errors": errors}


@router.post("/api/compartments/{compartment_id}/restart")
async def restart_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    errors = await compartment_manager.restart_compartment(db, compartment_id)
    statuses = await compartment_manager.get_status(db, compartment_id)
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        toast = f"{len(errors)} unit(s) failed to restart" if errors else "Compartment restarted"
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            {**await _comp_ctx(request, comp), "statuses": statuses, "errors": errors},
            headers=_toast_trigger(toast, error=bool(errors)),
        )
    return {"statuses": statuses, "errors": errors}


@router.post("/api/compartments/{compartment_id}/enable")
async def enable_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    await compartment_manager.enable_compartment(db, compartment_id)
    statuses = await compartment_manager.get_status(db, compartment_id)
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            {**await _comp_ctx(request, comp), "statuses": statuses},
            headers=_toast_trigger("Autostart enabled"),
        )
    return {"ok": True}


@router.post("/api/compartments/{compartment_id}/disable")
async def disable_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    await compartment_manager.disable_compartment(db, compartment_id)
    statuses = await compartment_manager.get_status(db, compartment_id)
    if _is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            {**await _comp_ctx(request, comp), "statuses": statuses},
            headers=_toast_trigger("Autostart disabled"),
        )
    return {"ok": True}


@router.get("/api/compartments/{compartment_id}/sync")
async def get_sync_status(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    issues = await compartment_manager.check_sync(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/sync_status.html",
            {"compartment_id": compartment_id, "issues": issues},
        )
    return {"in_sync": not issues, "issues": issues}


@router.post("/api/compartments/{compartment_id}/sync")
async def resync_compartment_route(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    try:
        await compartment_manager.resync_compartment(db, compartment_id)
    except Exception as exc:
        logger.error("Resync failed for %s: %s", log_safe(compartment_id), exc)
        raise HTTPException(status_code=500, detail=_t("Resync failed")) from exc
    issues = await compartment_manager.check_sync(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/sync_status.html",
            {"compartment_id": compartment_id, "issues": issues},
            headers=_toast_trigger("Unit files re-synced"),
        )
    return {"in_sync": not issues, "issues": issues}


@router.get("/api/compartments/{compartment_id}/quadlets")
async def get_compartment_quadlets(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    files = await compartment_manager.get_quadlet_files(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/quadlets_viewer.html",
            {"compartment_id": compartment_id, "files": files},
        )
    return {"files": files}


@router.get("/api/compartments/{compartment_id}/status")
async def get_compartment_status(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    statuses = await compartment_manager.get_status(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/status_badges.html",
            {"compartment_id": compartment_id, "statuses": statuses},
        )
    return {"statuses": statuses}


@router.get("/api/compartments/{compartment_id}/containers/{container_name}/status-detail")
async def get_container_status_detail(
    request: Request,
    compartment_id: SafeSlug,
    container_name: SafeUnitName,
    user: SafeStr = Depends(require_auth),
):
    from ..services import systemd_manager

    loop = asyncio.get_event_loop()
    statuses = await loop.run_in_executor(
        None, systemd_manager.get_service_status, compartment_id, [container_name]
    )
    status_item = statuses[0] if statuses else {}
    return _TEMPLATES.TemplateResponse(
        request, "partials/status_modal_body.html", {"status": status_item}
    )


@router.get("/api/compartments/{compartment_id}/status-dot")
async def get_compartment_status_dot(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    """Return a tiny colored status dot for the sidebar service list."""
    statuses = await compartment_manager.get_status(db, compartment_id)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/status_dot.html",
        _status_dot_context(compartment_id, statuses),
    )


@router.get("/api/status-dots")
async def get_all_status_dots(
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    """Return OOB status dots for all compartments in a single request."""
    compartments = await compartment_manager.list_compartments(db)
    # list_compartments already populated containers; pass them in to skip re-query
    all_statuses = await asyncio.gather(
        *[compartment_manager.get_status(db, comp.id, comp.containers) for comp in compartments]
    )
    tmpl = _TEMPLATES.env.get_template("partials/status_dot.html")
    parts = [
        tmpl.render(
            _status_dot_context(SafeSlug.of(comp.id, "_all_status_dots"), statuses, oob=True)
        )
        for comp, statuses in zip(compartments, all_statuses, strict=False)
    ]
    return Response("\n".join(parts), media_type="text/html")


@router.get("/api/compartments/{compartment_id}/metrics")
async def get_compartment_metrics(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    info = user_manager.get_user_info(compartment_id)
    uid = info.get("uid") if info else None
    if uid is None:
        return {
            "compartment_id": compartment_id,
            "cpu_percent": 0,
            "mem_bytes": 0,
            "proc_count": 0,
            "disk_bytes": 0,
        }
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, metrics.get_metrics, compartment_id, uid)


@router.get("/api/compartments/{compartment_id}/processes")
async def get_service_processes(
    request: Request,
    compartment_id: SafeSlug,
    user: SafeStr = Depends(require_auth),
):
    info = user_manager.get_user_info(compartment_id)
    uid = info.get("uid") if info else None
    if uid is None:
        procs = []
    else:
        loop = asyncio.get_event_loop()
        procs = await loop.run_in_executor(None, metrics.get_processes, uid)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request, "partials/proc_modal_body.html", {"procs": procs}
        )
    return procs


@router.get("/api/compartments/{compartment_id}/disk-usage")
async def get_service_disk_usage(
    request: Request,
    compartment_id: SafeSlug,
    user: SafeStr = Depends(require_auth),
):
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, metrics.get_disk_breakdown, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(request, "partials/disk_modal_body.html", {"disk": data})
    return data


@router.get("/api/metrics")
async def get_metrics(
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    services = await compartment_manager.list_compartments(db)
    loop = asyncio.get_event_loop()
    results = []
    for comp in services:
        info = user_manager.get_user_info(comp.id)
        uid = info.get("uid") if info else None
        if uid is not None:
            m = await loop.run_in_executor(None, metrics.get_metrics, comp.id, uid)
            m["compartment_id"] = comp.id
            results.append(m)
    return results


@router.get("/api/metrics/disk")
async def get_metrics_disk(
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    services = await compartment_manager.list_compartments(db)
    loop = asyncio.get_event_loop()
    results = []
    for comp in services:
        d = await loop.run_in_executor(None, metrics.get_disk_breakdown, comp.id)
        total = (
            sum(x["bytes"] for x in d["images"])
            + sum(x["bytes"] for x in d["overlays"])
            + d["volumes_total"]
            + d["config_bytes"]
        )
        results.append({"compartment_id": comp.id, "disk_bytes": total})
    return results


# ---------------------------------------------------------------------------
# Notification hooks
# ---------------------------------------------------------------------------


async def _notification_hooks_ctx(db: AsyncSession, compartment_id: SafeSlug) -> dict:
    """Build the template context for the notification hooks partial."""
    hooks = await compartment_manager.list_notification_hooks(db, compartment_id)
    comp = await compartment_manager.get_compartment(db, compartment_id)
    container_names = [c.name for c in (comp.containers if comp else [])]
    return {"compartment_id": compartment_id, "hooks": hooks, "container_names": container_names}


@router.get("/api/compartments/{compartment_id}/notification-hooks")
async def list_notification_hooks(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    ctx = await _notification_hooks_ctx(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(request, "partials/notification_hooks.html", ctx)
    return [h.model_dump() for h in ctx["hooks"]]


@router.post(
    "/api/compartments/{compartment_id}/notification-hooks",
    status_code=status.HTTP_201_CREATED,
)
async def add_notification_hook(
    request: Request,
    compartment_id: SafeSlug,
    event_type: SafeStr = Form("on_failure"),
    container_name: SafeStr = Form(""),
    webhook_url: SafeStr = Form(...),
    webhook_secret: SafeStr = Form(""),
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    # on_unexpected_process applies to the whole compartment, not a single container
    if event_type == "on_unexpected_process":
        container_name = ""
    try:
        data = NotificationHookCreate(
            event_type=event_type,
            container_name=container_name,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
        )
        hook = await compartment_manager.add_notification_hook(db, compartment_id, data)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    if _is_htmx(request):
        ctx = await _notification_hooks_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/notification_hooks.html",
            ctx,
            headers=_toast_trigger(_t("Notification hook added")),
        )
    return hook.model_dump()


@router.delete(
    "/api/compartments/{compartment_id}/notification-hooks/{hook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_notification_hook(
    request: Request,
    compartment_id: SafeSlug,
    hook_id: SafeStr,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    await compartment_manager.delete_notification_hook(db, compartment_id, hook_id)
    if _is_htmx(request):
        ctx = await _notification_hooks_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/notification_hooks.html",
            ctx,
            headers=_toast_trigger(_t("Notification hook deleted")),
        )


# ---------------------------------------------------------------------------
# Process monitor
# ---------------------------------------------------------------------------


async def _process_monitor_ctx(db: AsyncSession, compartment_id: SafeSlug) -> dict:
    processes = await compartment_manager.list_processes(db, compartment_id)
    compartment = await compartment_manager.get_compartment(db, compartment_id)
    return {
        "compartment_id": compartment_id,
        "processes": processes,
        "process_monitor_enabled": compartment.process_monitor_enabled,
    }


@router.get("/api/compartments/{compartment_id}/process-monitor")
async def get_process_monitor(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    ctx = await _process_monitor_ctx(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(request, "partials/process_monitor.html", ctx)
    return [p.model_dump() for p in ctx["processes"]]


@router.post(
    "/api/compartments/{compartment_id}/processes/{process_id}/known",
    status_code=status.HTTP_200_OK,
)
async def mark_process_known(
    request: Request,
    compartment_id: SafeSlug,
    process_id: SafeStr,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    await compartment_manager.set_process_known(db, compartment_id, process_id, known=True)
    if _is_htmx(request):
        ctx = await _process_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/process_monitor.html",
            ctx,
            headers=_toast_trigger(_t("Process marked as known")),
        )


@router.post(
    "/api/compartments/{compartment_id}/processes/{process_id}/unknown",
    status_code=status.HTTP_200_OK,
)
async def mark_process_unknown(
    request: Request,
    compartment_id: SafeSlug,
    process_id: SafeStr,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    await compartment_manager.set_process_known(db, compartment_id, process_id, known=False)
    if _is_htmx(request):
        ctx = await _process_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/process_monitor.html",
            ctx,
            headers=_toast_trigger(_t("Process marked as unknown")),
        )


@router.delete(
    "/api/compartments/{compartment_id}/processes/{process_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_process(
    request: Request,
    compartment_id: SafeSlug,
    process_id: SafeStr,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    await compartment_manager.delete_process(db, compartment_id, process_id)
    if _is_htmx(request):
        ctx = await _process_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/process_monitor.html",
            ctx,
            headers=_toast_trigger(_t("Process record removed")),
        )


@router.post(
    "/api/compartments/{compartment_id}/process-monitor/enabled",
    status_code=status.HTTP_200_OK,
)
async def set_process_monitor_enabled(
    request: Request,
    compartment_id: SafeSlug,
    enabled: bool = Form(...),
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    await compartment_manager.set_process_monitor_enabled(db, compartment_id, enabled)
    ctx = await _process_monitor_ctx(db, compartment_id)
    msg = _t("Process monitor enabled") if enabled else _t("Process monitor disabled")
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/process_monitor.html",
            ctx,
            headers=_toast_trigger(msg),
        )
    return {"process_monitor_enabled": enabled}


# ---------------------------------------------------------------------------
# Connection monitor
# ---------------------------------------------------------------------------


async def _connection_monitor_ctx(db: AsyncSession, compartment_id: SafeSlug) -> dict:
    compartment = await compartment_manager.get_compartment(db, compartment_id)
    connections = await compartment_manager.list_connections(db, compartment_id)
    rules = await compartment_manager.list_whitelist_rules(db, compartment_id)
    containers = await compartment_manager.list_containers(db, compartment_id)
    return {
        "compartment_id": compartment_id,
        "connections": connections,
        "rules": rules,
        "containers": containers,
        "connection_monitor_enabled": compartment.connection_monitor_enabled,
        "connection_history_retention_days": compartment.connection_history_retention_days,
    }


@router.get("/api/compartments/{compartment_id}/connection-monitor")
async def get_connection_monitor(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    _: object = Depends(_require_compartment),
):
    ctx = await _connection_monitor_ctx(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(request, "partials/connection_monitor.html", ctx)
    return [c.model_dump() for c in ctx["connections"]]


@router.post(
    "/api/compartments/{compartment_id}/connection-monitor/enabled",
    status_code=status.HTTP_200_OK,
)
async def set_connection_monitor_enabled(
    request: Request,
    compartment_id: SafeSlug,
    enabled: bool = Form(...),
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    await compartment_manager.set_connection_monitor_enabled(db, compartment_id, enabled)
    ctx = await _connection_monitor_ctx(db, compartment_id)
    msg = _t("Connection monitor enabled") if enabled else _t("Connection monitor disabled")
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=_toast_trigger(msg),
        )
    return {"connection_monitor_enabled": enabled}


@router.post(
    "/api/compartments/{compartment_id}/connection-monitor/retention",
    status_code=status.HTTP_200_OK,
)
async def set_connection_history_retention(
    request: Request,
    compartment_id: SafeSlug,
    days: SafeStr = Form(...),
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    retention: int | None
    try:
        retention = int(days) if days.strip() else None
        if retention is not None and retention < 1:
            raise ValueError("must be at least 1")
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid retention value"
        ) from exc
    await compartment_manager.set_connection_history_retention(db, compartment_id, retention)
    ctx = await _connection_monitor_ctx(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=_toast_trigger(_t("History retention updated")),
        )
    return {"connection_history_retention_days": retention}


# Whitelist rules


@router.post(
    "/api/compartments/{compartment_id}/connection-whitelist",
    status_code=status.HTTP_200_OK,
)
async def add_whitelist_rule(
    request: Request,
    compartment_id: SafeSlug,
    description: SafeStr = Form(""),
    container_name: SafeStr = Form(""),
    proto: SafeStr = Form(""),
    dst_ip: SafeStr = Form(""),
    dst_port: SafeStr = Form(""),
    direction: SafeStr = Form(""),
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    port: int | None
    try:
        port = int(dst_port) if dst_port.strip() else None
        if port is not None and not (1 <= port <= 65535):
            raise ValueError("port out of range")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid port number") from exc
    direction_val = direction if direction in ("outbound", "inbound") else None
    ip: SafeIpAddress | None = None
    if dst_ip:
        try:
            ip = SafeIpAddress.of(dst_ip, "dst_ip")
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid IP address"
            ) from exc
    await compartment_manager.add_whitelist_rule(
        db,
        compartment_id,
        description,
        container_name or None,
        proto or None,
        ip,
        port,
        direction_val,
    )
    ctx = await _connection_monitor_ctx(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=_toast_trigger(_t("Whitelist rule added")),
        )
    return ctx["rules"][-1].model_dump() if ctx["rules"] else {}


@router.delete(
    "/api/compartments/{compartment_id}/connection-whitelist/{rule_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_whitelist_rule(
    request: Request,
    compartment_id: SafeSlug,
    rule_id: SafeStr,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    await compartment_manager.delete_whitelist_rule(db, compartment_id, rule_id)
    ctx = await _connection_monitor_ctx(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=_toast_trigger(_t("Whitelist rule removed")),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Connection history


@router.get("/api/compartments/{compartment_id}/connections.csv")
async def download_connections_csv(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    connections = await compartment_manager.list_connections(db, compartment_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "container_name",
            "proto",
            "dst_ip",
            "dst_port",
            "whitelisted",
            "times_seen",
            "first_seen_at",
            "last_seen_at",
        ]
    )
    for c in connections:
        writer.writerow(
            [
                c.container_name,
                c.proto,
                c.dst_ip,
                c.dst_port,
                "yes" if c.whitelisted else "no",
                c.times_seen,
                c.first_seen_at,
                c.last_seen_at,
            ]
        )
    filename = f"connections-{compartment_id}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete(
    "/api/compartments/{compartment_id}/connections",
    status_code=status.HTTP_200_OK,
)
async def clear_connections_history(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
    _: object = Depends(_require_compartment),
):
    await compartment_manager.clear_connections_history(db, compartment_id)
    ctx = await _connection_monitor_ctx(db, compartment_id)
    if _is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=_toast_trigger(_t("Connection history cleared")),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/api/compartments/{compartment_id}/connections/{connection_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_connection(
    request: Request,
    compartment_id: SafeSlug,
    connection_id: SafeStr,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
):
    await compartment_manager.delete_connection(db, compartment_id, connection_id)
    if _is_htmx(request):
        ctx = await _connection_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=_toast_trigger(_t("Connection record removed")),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Metrics history (Feature 9)
# ---------------------------------------------------------------------------


@router.get("/api/compartments/{compartment_id}/metrics-history")
async def get_metrics_history(
    compartment_id: SafeSlug,
    limit: int = 288,  # default: ~24 h at 5-min intervals
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
    _: object = Depends(_require_compartment),
) -> JSONResponse:
    """Return the last *limit* metrics snapshots for a compartment."""
    result = await db.execute(
        select(
            MetricsHistoryRow.recorded_at,
            MetricsHistoryRow.cpu_percent,
            MetricsHistoryRow.memory_bytes,
            MetricsHistoryRow.disk_bytes,
        )
        .where(MetricsHistoryRow.compartment_id == compartment_id)
        .order_by(MetricsHistoryRow.recorded_at.desc())
        .limit(min(limit, 2000))
    )
    rows = result.mappings().all()
    return JSONResponse(
        [
            {
                "recorded_at": row["recorded_at"],
                "cpu_percent": row["cpu_percent"],
                "memory_bytes": row["memory_bytes"],
                "disk_bytes": row["disk_bytes"],
            }
            for row in reversed(rows)  # return chronological order
        ]
    )


# ---------------------------------------------------------------------------
# Restart / failure analytics (Feature 10)
# ---------------------------------------------------------------------------


@router.get("/api/compartments/{compartment_id}/restart-stats")
async def get_restart_stats(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeStr = Depends(require_auth),
    _: object = Depends(_require_compartment),
) -> JSONResponse:
    """Return restart and failure counts for all containers in a compartment."""
    result = await db.execute(
        select(
            ContainerRestartStatsRow.container_name,
            ContainerRestartStatsRow.restart_count,
            ContainerRestartStatsRow.last_failure_at,
            ContainerRestartStatsRow.last_restart_at,
        )
        .where(ContainerRestartStatsRow.compartment_id == compartment_id)
        .order_by(ContainerRestartStatsRow.restart_count.desc())
    )
    rows = result.mappings().all()
    return JSONResponse(
        [
            {
                "container_name": row["container_name"],
                "restart_count": row["restart_count"],
                "last_failure_at": row["last_failure_at"],
                "last_restart_at": row["last_restart_at"],
            }
            for row in rows
        ]
    )

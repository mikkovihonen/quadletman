"""Compartment-level routes."""

import asyncio
import contextlib
import csv
import io
import json
import logging
import urllib.parse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import TEMPLATES as _TEMPLATES
from ..config.settings import settings
from ..db.engine import get_db
from ..db.orm import ContainerRestartStatsRow, MetricsHistoryRow
from ..i18n import gettext as _t
from ..models import (
    CompartmentCreate,
    CompartmentUpdate,
    ContainerCreate,
    ImageCreate,
    NotificationHookCreate,
    PodCreate,
    VolumeCreate,
)
from ..models.api.poll import (
    CompartmentPollResponse,
    ContainerStatus,
    DashboardPollResponse,
    DiskBreakdown,
    DiskTotal,
    MetricsSnapshot,
    PendingOp,
    StatusDot,
)
from ..models.sanitized import (
    SafeFormBool,
    SafeIpAddress,
    SafeMultilineStr,
    SafePortStr,
    SafeRegex,
    SafeSlug,
    SafeStr,
    SafeUnitName,
    SafeUsername,
    SafeUUID,
    SafeWebhookUrl,
    log_safe,
)
from ..podman import get_features
from ..services import compartment_manager, metrics, systemd_manager, user_manager
from ..services.bundle_parser import parse_quadlets_bundle
from ..services.compartment_manager import ServiceCondition
from .helpers import (
    MAX_UPLOAD_BYTES,
    comp_ctx,
    connection_monitor_ctx,
    is_htmx,
    notification_hooks_ctx,
    process_monitor_ctx,
    require_auth,
    require_compartment,
    run_blocking,
    status_dot_context,
    toast_trigger,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/compartments")
async def list_compartments(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    services = await compartment_manager.list_compartments(db)
    if is_htmx(request):
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
    user: SafeUsername = Depends(require_auth),
):
    existing = await compartment_manager.get_compartment(db, data.id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=_t("Compartment '%(id)s' already exists") % {"id": data.id},
        )
    try:
        comp = await compartment_manager.create_compartment(db, data)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=_t("Compartment '%(id)s' already exists") % {"id": data.id},
        ) from exc
    except Exception as exc:
        if isinstance(exc, ServiceCondition):
            raise
        logger.error("Failed to create service %s: %s", log_safe(data.id), log_safe(exc))
        raise HTTPException(status_code=500, detail=_t("Failed to create compartment")) from exc

    if is_htmx(request):
        services = await compartment_manager.list_compartments(db)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_list.html",
            {"compartments": services},
            headers=toast_trigger(_t("Compartment created successfully")),
        )
    return comp.model_dump()


@router.post("/api/compartments/import", status_code=status.HTTP_201_CREATED)
async def import_compartment_bundle(
    compartment_id: SafeSlug = Form(...),
    description: SafeStr = Form(""),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    """Import a .quadlets bundle file as a new service."""
    features = get_features()
    if not features.bundle:
        raise HTTPException(
            status_code=400,
            detail=_t("Bundle import requires Podman 5.8+ (detected: %(v)s)")
            % {"v": features.version_str},
        )

    try:
        raw = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=_t("Bundle file exceeds size limit"))
        content = raw.decode("utf-8")
    except HTTPException:
        raise
    except Exception as exc:
        if isinstance(exc, ServiceCondition):
            raise
        logger.warning("Bundle import: could not read uploaded file: %s", exc)
        raise HTTPException(status_code=422, detail=_t("Could not read uploaded file")) from exc

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
        if isinstance(exc, ServiceCondition):
            raise
        logger.error("import: failed to create service %s: %s", log_safe(compartment_id), exc)
        raise HTTPException(status_code=500, detail=_t("Failed to create service")) from exc

    import_errors: list[dict] = []

    # Import pods first (containers may reference them)
    for pp in parse_result.pods:
        try:
            await compartment_manager.add_pod(
                db,
                compartment_id,
                PodCreate(qm_name=pp.qm_name, network=pp.network, publish_ports=pp.publish_ports),
            )
        except Exception as exc:
            if isinstance(exc, ServiceCondition):
                raise
            logger.error("import: failed to add pod %s: %s", log_safe(pp.qm_name), exc)
            import_errors.append({"pod": pp.qm_name, "error": "Failed to import pod"})

    # Import images
    for pi in parse_result.image_units:
        try:
            await compartment_manager.add_image(
                db,
                compartment_id,
                ImageCreate(
                    qm_name=pi.qm_name,
                    image=pi.image,
                    auth_file=pi.auth_file,
                ),
            )
        except Exception as exc:
            if isinstance(exc, ServiceCondition):
                raise
            logger.error("import: failed to add image %s: %s", log_safe(pi.qm_name), exc)
            import_errors.append({"image": pi.qm_name, "error": "Failed to import image"})

    # Import quadlet-managed volume units (host-directory volumes must be added via UI)
    for pv in parse_result.volume_units:
        try:
            await compartment_manager.add_volume(
                db,
                compartment_id,
                VolumeCreate(
                    qm_name=pv.qm_name,
                    qm_use_quadlet=True,
                    driver=pv.driver,
                    device=pv.device,
                    options=pv.options,
                    copy=pv.copy,
                ),
            )
        except Exception as exc:
            if isinstance(exc, ServiceCondition):
                raise
            logger.error("import: failed to add volume unit %s: %s", log_safe(pv.qm_name), exc)
            import_errors.append({"volume": pv.qm_name, "error": "Failed to import volume"})

    for pc in parse_result.containers:
        try:
            await compartment_manager.add_container(
                db,
                compartment_id,
                ContainerCreate(
                    qm_name=pc.qm_name,
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
                    pod=pc.pod,
                    log_driver=pc.log_driver,
                    working_dir=pc.working_dir,
                    hostname=pc.hostname,
                    no_new_privileges=pc.no_new_privileges,
                    read_only=pc.read_only,
                    volumes=[],
                ),
            )
        except Exception as exc:
            if isinstance(exc, ServiceCondition):
                raise
            logger.error("import: failed to add container %s: %s", log_safe(pc.qm_name), exc)
            import_errors.append({"container": pc.qm_name, "error": "Failed to import container"})

    result = (await compartment_manager.get_compartment(db, compartment_id)).model_dump()
    result["import_warnings"] = parse_result.warnings
    result["import_errors"] = import_errors
    return JSONResponse(status_code=201, content=result)


@router.get("/api/compartments/{compartment_id}")
async def get_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    if is_htmx(request):
        statuses = await compartment_manager.get_status(db, compartment_id, comp.containers)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            {**await comp_ctx(request, comp), "statuses": statuses},
        )
    return comp.model_dump()


@router.put("/api/compartments/{compartment_id}")
async def update_compartment(
    request: Request,
    compartment_id: SafeSlug,
    data: CompartmentUpdate,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.update_compartment(db, compartment_id, data.description)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            await comp_ctx(request, comp),
            headers=toast_trigger(_t("Compartment updated")),
        )
    return comp.model_dump()


@router.delete("/api/compartments/{compartment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    try:
        await compartment_manager.delete_compartment(db, compartment_id)
    except Exception as exc:
        if isinstance(exc, ServiceCondition):
            raise
        logger.error("Failed to delete service %s: %s", log_safe(compartment_id), exc)
        raise HTTPException(status_code=500, detail=_t("Failed to delete compartment")) from exc

    if is_htmx(request):
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
    user: SafeUsername = Depends(require_auth),
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


@router.post("/api/compartments/{compartment_id}/start", status_code=202)
async def start_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    session_id = request.cookies.get("qm_session", "")
    op_id = await compartment_manager.enqueue_operation(
        db,
        compartment_id,
        SafeStr.of("start", "op_type"),
        SafeStr.of(str(user), "user"),
        SafeStr.of(session_id, "session_id"),
    )
    if is_htmx(request):
        return Response(status_code=202, headers=toast_trigger(_t("Starting compartment...")))
    return {"operation_id": str(op_id)}


@router.post("/api/compartments/{compartment_id}/stop", status_code=202)
async def stop_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    session_id = request.cookies.get("qm_session", "")
    op_id = await compartment_manager.enqueue_operation(
        db,
        compartment_id,
        SafeStr.of("stop", "op_type"),
        SafeStr.of(str(user), "user"),
        SafeStr.of(session_id, "session_id"),
    )
    if is_htmx(request):
        return Response(status_code=202, headers=toast_trigger(_t("Stopping compartment...")))
    return {"operation_id": str(op_id)}


@router.post("/api/compartments/{compartment_id}/restart", status_code=202)
async def restart_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    session_id = request.cookies.get("qm_session", "")
    op_id = await compartment_manager.enqueue_operation(
        db,
        compartment_id,
        SafeStr.of("restart", "op_type"),
        SafeStr.of(str(user), "user"),
        SafeStr.of(session_id, "session_id"),
    )
    if is_htmx(request):
        return Response(status_code=202, headers=toast_trigger(_t("Restarting compartment...")))
    return {"operation_id": str(op_id)}


@router.post("/api/compartments/{compartment_id}/enable")
async def enable_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    await compartment_manager.enable_compartment(db, compartment_id)
    statuses = await compartment_manager.get_status(db, compartment_id)
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            {**await comp_ctx(request, comp), "statuses": statuses},
            headers=toast_trigger(_t("Autostart enabled")),
        )
    return {"ok": True}


@router.post("/api/compartments/{compartment_id}/disable")
async def disable_compartment(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    await compartment_manager.disable_compartment(db, compartment_id)
    statuses = await compartment_manager.get_status(db, compartment_id)
    if is_htmx(request):
        comp = await compartment_manager.get_compartment(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/compartment_detail.html",
            {**await comp_ctx(request, comp), "statuses": statuses},
            headers=toast_trigger(_t("Autostart disabled")),
        )
    return {"ok": True}


@router.get("/api/compartments/{compartment_id}/sync")
async def get_sync_status(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    issues = await compartment_manager.check_sync(db, compartment_id)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/sync_status.html",
            {"compartment_id": compartment_id, "issues": issues},
        )
    return {"in_sync": not issues, "issues": issues}


@router.post("/api/compartments/{compartment_id}/sync", status_code=202)
async def resync_compartment_route(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    session_id = request.cookies.get("qm_session", "")
    await compartment_manager.enqueue_operation(
        db,
        compartment_id,
        SafeStr.of("resync", "op_type"),
        SafeStr.of(str(user), "user"),
        SafeStr.of(session_id, "session_id"),
    )
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/sync_status.html",
            {"compartment_id": compartment_id, "issues": []},
            headers=toast_trigger(_t("Re-syncing unit files...")),
            status_code=202,
        )
    return {"status": "queued"}


@router.get("/api/operations/{operation_id}")
async def get_operation(
    operation_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    op = await compartment_manager.get_operation(db, operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail=_t("Operation not found"))
    return op.model_dump()


@router.get("/api/compartments/{compartment_id}/quadlets")
async def get_compartment_quadlets(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    files = await compartment_manager.get_quadlet_files(db, compartment_id)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/quadlets_viewer.html",
            {"compartment_id": compartment_id, "files": files},
        )
    return {"files": files}


@router.get("/api/compartments/{compartment_id}/containers/{container_name}/status-detail")
async def get_container_status_detail(
    request: Request,
    compartment_id: SafeSlug,
    container_name: SafeUnitName,
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    statuses = await run_blocking(
        systemd_manager.get_service_status, compartment_id, [container_name]
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
    user: SafeUsername = Depends(require_auth),
):
    """Return a tiny colored status dot for the sidebar service list."""
    statuses = await compartment_manager.get_status(db, compartment_id)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/status_dot.html",
        status_dot_context(compartment_id, statuses),
    )


@router.get("/api/compartments/{compartment_id}/processes")
async def get_service_processes(
    request: Request,
    compartment_id: SafeSlug,
    user: SafeUsername = Depends(require_auth),
):
    info = user_manager.get_user_info(compartment_id)
    uid = info.get("uid") if info else None
    if uid is None:
        procs = []
    else:
        procs = await run_blocking(metrics.get_processes, uid)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request, "partials/proc_modal_body.html", {"procs": procs}
        )
    return procs


@router.get("/api/compartments/{compartment_id}/disk-usage")
async def get_service_disk_usage(
    request: Request,
    compartment_id: SafeSlug,
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    data = await run_blocking(metrics.get_disk_breakdown, compartment_id)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(request, "partials/disk_modal_body.html", {"disk": data})
    return data


# ---------------------------------------------------------------------------
# Consolidated polling
# ---------------------------------------------------------------------------


def _pending_op_from(op) -> PendingOp:
    """Build a PendingOp from an Operation, extracting container_name from payload."""
    cname = None
    if op.op_type in ("start_container", "stop_container"):
        with contextlib.suppress(Exception):
            p = json.loads(str(op.payload))
            cname = p.get("container_name")
    return PendingOp(op_type=op.op_type, status=op.status, container_name=cname)


@router.get("/api/dashboard/poll")
async def dashboard_poll(
    include_disk: bool = False,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
) -> DashboardPollResponse:
    """Single endpoint for all dashboard recurring data: metrics + status dots + disk."""
    compartments = await compartment_manager.list_compartments(db)

    # Gather metrics and statuses concurrently for all compartments
    _zero = {"cpu_percent": 0, "mem_bytes": 0, "proc_count": 0, "disk_bytes": 0}
    uid_map: dict[str, int | None] = {}
    metrics_coros = []
    status_coros = []
    for comp in compartments:
        info = user_manager.get_user_info(comp.id)
        uid = info.get("uid") if info else None
        uid_map[comp.id] = uid
        if uid is not None:
            metrics_coros.append(run_blocking(metrics.get_metrics, comp.id, uid))
        else:
            metrics_coros.append(asyncio.sleep(0, result=dict(_zero)))
        status_coros.append(compartment_manager.get_status(db, comp.id, comp.containers))

    all_metrics, all_statuses = await asyncio.gather(
        asyncio.gather(*metrics_coros, return_exceptions=True),
        asyncio.gather(*status_coros, return_exceptions=True),
    )

    metrics_list = []
    dots_list = []
    for comp, m, statuses in zip(compartments, all_metrics, all_statuses, strict=True):
        if isinstance(m, BaseException):
            m = dict(_zero)
        m.setdefault("compartment_id", comp.id)
        metrics_list.append(
            MetricsSnapshot(
                compartment_id=SafeSlug.of(comp.id, "poll"),
                cpu_percent=m["cpu_percent"],
                mem_bytes=m["mem_bytes"],
                proc_count=m["proc_count"],
                disk_bytes=m["disk_bytes"],
            )
        )
        if isinstance(statuses, BaseException):
            statuses = []
        ctx = status_dot_context(SafeSlug.of(comp.id, "poll"), statuses)
        dots_list.append(
            StatusDot(
                compartment_id=SafeSlug.of(comp.id, "poll"),
                color=SafeStr.of(ctx["color"], "dot_color"),
                title=SafeStr.of(ctx["title"], "dot_title"),
            )
        )

    disk = None
    if include_disk:
        disk_results = await asyncio.gather(
            *[run_blocking(metrics.get_disk_breakdown, comp.id) for comp in compartments],
            return_exceptions=True,
        )
        disk = []
        for comp, d in zip(compartments, disk_results, strict=True):
            if isinstance(d, BaseException):
                disk.append(DiskTotal(compartment_id=SafeSlug.of(comp.id, "poll"), disk_bytes=0))
            else:
                total = (
                    sum(x["bytes"] for x in d["images"])
                    + sum(x["bytes"] for x in d["overlays"])
                    + d["volumes_total"]
                    + d["config_bytes"]
                )
                disk.append(
                    DiskTotal(compartment_id=SafeSlug.of(comp.id, "poll"), disk_bytes=total)
                )

    # Gather pending operations for all compartments
    all_pending = {}
    for comp in compartments:
        ops = await compartment_manager.get_pending_operations(db, SafeSlug.of(comp.id, "poll"))
        if ops:
            all_pending[comp.id] = [_pending_op_from(o) for o in ops]

    return DashboardPollResponse(
        poll_interval=settings.ui_poll_interval,
        disk_poll_interval=settings.ui_disk_poll_interval,
        metrics=metrics_list,
        status_dots=dots_list,
        disk=disk,
        pending_ops=all_pending or None,
    )


@router.get("/api/compartments/{compartment_id}/poll")
async def compartment_poll(
    compartment_id: SafeSlug,
    include_disk: bool = False,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
) -> CompartmentPollResponse:
    """Single endpoint for all compartment detail recurring data: metrics + statuses + disk."""
    info = user_manager.get_user_info(compartment_id)
    uid = info.get("uid") if info else None

    if uid is not None:
        m_result, statuses = await asyncio.gather(
            run_blocking(metrics.get_metrics, compartment_id, uid),
            compartment_manager.get_status(db, compartment_id),
        )
    else:
        m_result = {"cpu_percent": 0, "mem_bytes": 0, "proc_count": 0, "disk_bytes": 0}
        statuses = await compartment_manager.get_status(db, compartment_id)

    status_list = [
        ContainerStatus(
            container=SafeStr.of(s.get("container", ""), "container"),
            active_state=SafeStr.of(s.get("active_state", "unknown"), "active_state"),
            sub_state=SafeStr.of(s.get("sub_state", ""), "sub_state"),
            load_state=SafeStr.of(s.get("load_state", ""), "load_state"),
            unit_file_state=SafeStr.of(s.get("unit_file_state", ""), "unit_file_state"),
        )
        for s in statuses
    ]

    disk = None
    if include_disk:
        try:
            d = await run_blocking(metrics.get_disk_breakdown, compartment_id)
            disk = DiskBreakdown(
                images=d["images"],
                overlays=d["overlays"],
                volumes=d["volumes"],
                volumes_total=d["volumes_total"],
                config_bytes=d["config_bytes"],
            )
        except Exception:
            disk = DiskBreakdown(
                images=[], overlays=[], volumes=[], volumes_total=0, config_bytes=0
            )

    dot_ctx = status_dot_context(compartment_id, statuses)
    dot = StatusDot(
        compartment_id=compartment_id,
        color=SafeStr.of(dot_ctx["color"], "dot_color"),
        title=SafeStr.of(dot_ctx["title"], "dot_title"),
    )

    ops = await compartment_manager.get_pending_operations(db, compartment_id)
    pending = [_pending_op_from(o) for o in ops] or None

    return CompartmentPollResponse(
        poll_interval=settings.ui_poll_interval,
        disk_poll_interval=settings.ui_disk_poll_interval,
        cpu_percent=m_result["cpu_percent"],
        mem_bytes=m_result["mem_bytes"],
        proc_count=m_result["proc_count"],
        disk_bytes=m_result["disk_bytes"],
        statuses=status_list,
        status_dot=dot,
        disk=disk,
        pending_ops=pending,
    )


# ---------------------------------------------------------------------------
# Notification hooks
# ---------------------------------------------------------------------------


@router.get("/api/compartments/{compartment_id}/notification-hooks")
async def list_notification_hooks(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    ctx = await notification_hooks_ctx(db, compartment_id)
    if is_htmx(request):
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
    webhook_url: SafeWebhookUrl = Form(...),
    webhook_secret: SafeStr = Form(""),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    # on_unexpected_process applies to the whole compartment, not a single container
    if event_type == "on_unexpected_process":
        container_name = ""
    try:
        data = NotificationHookCreate(
            event_type=event_type,
            qm_container_name=container_name,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
        )
        hook = await compartment_manager.add_notification_hook(db, compartment_id, data)
    except Exception as exc:
        if isinstance(exc, ServiceCondition):
            raise
        logger.exception("Failed to add notification hook")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, _t("Internal server error")
        ) from exc

    if is_htmx(request):
        ctx = await notification_hooks_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/notification_hooks.html",
            ctx,
            headers=toast_trigger(_t("Notification hook added")),
        )
    return hook.model_dump()


@router.delete(
    "/api/compartments/{compartment_id}/notification-hooks/{hook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_notification_hook(
    request: Request,
    compartment_id: SafeSlug,
    hook_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    await compartment_manager.delete_notification_hook(db, compartment_id, hook_id)
    if is_htmx(request):
        ctx = await notification_hooks_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/notification_hooks.html",
            ctx,
            headers=toast_trigger(_t("Notification hook deleted")),
        )


# ---------------------------------------------------------------------------
# Process monitor
# ---------------------------------------------------------------------------


@router.get("/api/compartments/{compartment_id}/process-monitor")
async def get_process_monitor(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    ctx = await process_monitor_ctx(db, compartment_id)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(request, "partials/process_monitor.html", ctx)
    return [p.model_dump() for p in ctx["processes"]]


@router.post(
    "/api/compartments/{compartment_id}/processes/{process_id}/known",
    status_code=status.HTTP_200_OK,
)
async def mark_process_known(
    request: Request,
    compartment_id: SafeSlug,
    process_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    try:
        await compartment_manager.set_process_known(db, compartment_id, process_id, known=True)
    except ValueError as exc:
        logger.warning("Process mark-known conflict: %s", exc)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if is_htmx(request):
        ctx = await process_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/process_monitor.html",
            ctx,
            headers=toast_trigger(_t("Pattern created")),
        )


@router.post(
    "/api/compartments/{compartment_id}/processes/{process_id}/unknown",
    status_code=status.HTTP_200_OK,
)
async def mark_process_unknown(
    request: Request,
    compartment_id: SafeSlug,
    process_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    await compartment_manager.set_process_known(db, compartment_id, process_id, known=False)
    if is_htmx(request):
        ctx = await process_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/process_monitor.html",
            ctx,
            headers=toast_trigger(_t("Process marked as unknown")),
        )


@router.delete(
    "/api/compartments/{compartment_id}/processes/{process_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_process(
    request: Request,
    compartment_id: SafeSlug,
    process_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    await compartment_manager.delete_process(db, compartment_id, process_id)
    if is_htmx(request):
        ctx = await process_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/process_monitor.html",
            ctx,
            headers=toast_trigger(_t("Process record removed")),
        )


@router.post(
    "/api/compartments/{compartment_id}/process-monitor/enabled",
    status_code=status.HTTP_200_OK,
)
async def set_process_monitor_enabled(
    request: Request,
    compartment_id: SafeSlug,
    enabled: SafeFormBool = Form(...),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    flag = enabled == "true"
    await compartment_manager.set_process_monitor_enabled(db, compartment_id, flag)
    ctx = await process_monitor_ctx(db, compartment_id)
    msg = _t("Process monitor enabled") if flag else _t("Process monitor disabled")
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/process_monitor.html",
            ctx,
            headers=toast_trigger(msg),
        )
    return {"process_monitor_enabled": flag}


# ---------------------------------------------------------------------------
# Process patterns
# ---------------------------------------------------------------------------


@router.get("/api/compartments/{compartment_id}/process-patterns")
async def list_process_patterns(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    patterns = await compartment_manager.list_process_patterns(db, compartment_id)
    if is_htmx(request):
        ctx = await process_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(request, "partials/process_monitor.html", ctx)
    return [p.model_dump() for p in patterns]


@router.post(
    "/api/compartments/{compartment_id}/process-patterns",
    status_code=status.HTTP_201_CREATED,
)
async def create_process_pattern(
    request: Request,
    compartment_id: SafeSlug,
    process_name: SafeStr = Form(...),
    cmdline_pattern: SafeRegex = Form(...),
    segments_json: SafeStr = Form("[]"),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    try:
        await compartment_manager.create_process_pattern(
            db, compartment_id, process_name, cmdline_pattern, segments_json
        )
    except ValueError as exc:
        logger.warning("Process pattern creation conflict: %s", exc)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if is_htmx(request):
        ctx = await process_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/process_monitor.html",
            ctx,
            headers=toast_trigger(_t("Pattern created")),
        )


@router.post(
    "/api/compartments/{compartment_id}/process-patterns/{pattern_id}",
    status_code=status.HTTP_200_OK,
)
async def update_process_pattern(
    request: Request,
    compartment_id: SafeSlug,
    pattern_id: SafeUUID,
    cmdline_pattern: SafeRegex = Form(...),
    segments_json: SafeStr = Form("[]"),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    try:
        await compartment_manager.update_process_pattern(
            db, compartment_id, pattern_id, cmdline_pattern, segments_json
        )
    except ValueError as exc:
        logger.warning("Process pattern update conflict: %s", exc)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if is_htmx(request):
        ctx = await process_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/process_monitor.html",
            ctx,
            headers=toast_trigger(_t("Pattern updated")),
        )


@router.delete(
    "/api/compartments/{compartment_id}/process-patterns/{pattern_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_process_pattern(
    request: Request,
    compartment_id: SafeSlug,
    pattern_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    await compartment_manager.delete_process_pattern(db, compartment_id, pattern_id)
    if is_htmx(request):
        ctx = await process_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/process_monitor.html",
            ctx,
            headers=toast_trigger(_t("Pattern deleted")),
        )


@router.get("/api/compartments/{compartment_id}/process-patterns/{pattern_id}/matches")
async def get_pattern_matches(
    request: Request,
    compartment_id: SafeSlug,
    pattern_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    matches = await compartment_manager.get_pattern_matches(db, compartment_id, pattern_id)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/pattern_matches.html",
            {"matches": matches, "compartment_id": compartment_id, "pattern_id": pattern_id},
        )
    return [m.model_dump() for m in matches]


# ---------------------------------------------------------------------------
# Connection monitor
# ---------------------------------------------------------------------------


@router.get("/api/compartments/{compartment_id}/connection-monitor")
async def get_connection_monitor(
    request: Request,
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_compartment),
):
    ctx = await connection_monitor_ctx(db, compartment_id)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(request, "partials/connection_monitor.html", ctx)
    return [c.model_dump() for c in ctx["connections"]]


@router.post(
    "/api/compartments/{compartment_id}/connection-monitor/enabled",
    status_code=status.HTTP_200_OK,
)
async def set_connection_monitor_enabled(
    request: Request,
    compartment_id: SafeSlug,
    enabled: SafeFormBool = Form(...),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    flag = enabled == "true"
    await compartment_manager.set_connection_monitor_enabled(db, compartment_id, flag)
    ctx = await connection_monitor_ctx(db, compartment_id)
    msg = _t("Connection monitor enabled") if flag else _t("Connection monitor disabled")
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=toast_trigger(msg),
        )
    return {"connection_monitor_enabled": flag}


@router.post(
    "/api/compartments/{compartment_id}/connection-monitor/retention",
    status_code=status.HTTP_200_OK,
)
async def set_connection_history_retention(
    request: Request,
    compartment_id: SafeSlug,
    days: SafeStr = Form(...),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    retention: int | None
    try:
        retention = int(days) if days.strip() else None
        if retention is not None and retention < 1:
            raise ValueError("must be at least 1")
    except ValueError as exc:
        logger.warning("Invalid retention value: %s", exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await compartment_manager.set_connection_history_retention(db, compartment_id, retention)
    ctx = await connection_monitor_ctx(db, compartment_id)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=toast_trigger(_t("History retention updated")),
        )
    return {"connection_history_retention_days": retention}


# Allowlist rules


@router.post(
    "/api/compartments/{compartment_id}/connection-allowlist",
    status_code=status.HTTP_200_OK,
)
async def add_allowlist_rule(
    request: Request,
    compartment_id: SafeSlug,
    description: SafeStr = Form(""),
    container_name: SafeStr = Form(""),
    proto: SafeStr = Form(""),
    dst_ip: SafeIpAddress = Form(""),
    dst_port: SafePortStr = Form(""),
    direction: SafeStr = Form(""),
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    port: int | None
    try:
        port = int(dst_port) if dst_port.strip() else None
        if port is not None and not (1 <= port <= 65535):
            raise ValueError("port out of range")
    except ValueError as exc:
        logger.warning("Invalid port value: %s", exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    direction_val = direction if direction in ("outbound", "inbound") else None
    ip: SafeIpAddress | None = None
    if dst_ip:
        try:
            ip = SafeIpAddress.of(dst_ip, "dst_ip")
        except ValueError as exc:
            logger.warning("Invalid IP address: %s", exc)
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await compartment_manager.add_allowlist_rule(
        db,
        compartment_id,
        description,
        container_name or None,
        proto or None,
        ip,
        port,
        direction_val,
    )
    ctx = await connection_monitor_ctx(db, compartment_id)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=toast_trigger(_t("Allowlist rule added")),
        )
    return ctx["rules"][-1].model_dump() if ctx["rules"] else {}


@router.delete(
    "/api/compartments/{compartment_id}/connection-allowlist/{rule_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_allowlist_rule(
    request: Request,
    compartment_id: SafeSlug,
    rule_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    await compartment_manager.delete_allowlist_rule(db, compartment_id, rule_id)
    ctx = await connection_monitor_ctx(db, compartment_id)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=toast_trigger(_t("Allowlist rule removed")),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Connection history


@router.get("/api/compartments/{compartment_id}/connections.csv")
async def download_connections_csv(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
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
            "allowlisted",
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
                "yes" if c.allowlisted else "no",
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
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
):
    await compartment_manager.clear_connections_history(db, compartment_id)
    ctx = await connection_monitor_ctx(db, compartment_id)
    if is_htmx(request):
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=toast_trigger(_t("Connection history cleared")),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/api/compartments/{compartment_id}/connections/{connection_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_connection(
    request: Request,
    compartment_id: SafeSlug,
    connection_id: SafeUUID,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    await compartment_manager.delete_connection(db, compartment_id, connection_id)
    if is_htmx(request):
        ctx = await connection_monitor_ctx(db, compartment_id)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/connection_monitor.html",
            ctx,
            headers=toast_trigger(_t("Connection record removed")),
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
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
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
    user: SafeUsername = Depends(require_auth),
    _: object = Depends(require_compartment),
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

"""Log streaming, terminal, and podman-info routes."""

import asyncio
import fcntl
import json
import logging
import os
import pty
import struct
import subprocess
import termios
from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..models.api.artifact import ArtifactCreate
from ..models.api.build import BuildCreate
from ..models.api.container import ContainerCreate
from ..models.api.image import ImageCreate
from ..models.api.kube import KubeCreate
from ..models.api.network import NetworkCreate
from ..models.api.pod import PodCreate
from ..models.api.volume import VolumeCreate
from ..models.sanitized import SafeSlug, SafeStr, SafeUnitName, SafeUsername, log_safe
from ..models.version_span import (
    ARTIFACT_UNITS,
    BUILD_UNITS,
    BUNDLE,
    IMAGE_UNITS,
    KUBE_UNITS,
    PASTA,
    POD_UNITS,
    QUADLET,
    QUADLET_CLI,
    SLIRP4NETNS,
    field_availability,
    field_tooltip,
    get_field_constraints,
    get_version_spans,
    is_field_available,
    is_field_deprecated,
)
from ..podman_version import get_features, get_podman_info
from ..security.auth import require_auth
from ..security.session import get_session
from ..services import compartment_manager, systemd_manager, user_manager
from .helpers import EXEC_USER_RE

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/podman-info")
async def podman_info_root(user: SafeUsername = Depends(require_auth)):
    """Return 'podman info' as root (process-lifetime cached)."""
    return get_podman_info()


@router.get("/api/compartments/{compartment_id}/podman-info")
async def podman_info_compartment(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    """Return 'podman info' run as the compartment user (qm-{id})."""
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404)
    return await asyncio.get_event_loop().run_in_executor(
        None, user_manager.get_compartment_podman_info, compartment_id
    )


@router.get("/api/podman-features")
async def podman_features_partial(
    request: Request,
    user: SafeUsername = Depends(require_auth),
):
    """Return server-rendered feature availability table."""
    features = get_features()
    version = features.version

    def _field_breakdown(model_cls):
        """Build field availability breakdown for a model class."""
        spans = get_version_spans(model_cls)
        constraints = get_field_constraints(model_cls)
        avail = field_availability(model_cls, version)
        fields = []
        for field_name, available in sorted(avail.items()):
            span = spans[field_name]
            fc = constraints.get(field_name)
            fields.append(
                {
                    "field": field_name,
                    "key": span.quadlet_key or field_name,
                    "available": available,
                    "introduced": span.introduced,
                    "tooltip": field_tooltip(span, version) if not available else "",
                    "description": fc.description if fc and fc.description else "",
                }
            )
        return {
            "fields": fields,
            "total": len(fields),
            "unavail_count": sum(1 for f in fields if not f["available"]),
        }

    # Combined feature rows — each optionally carries a field breakdown
    feature_rows = [
        {
            "name": "Pasta networking",
            "desc": "Fast user-mode networking for rootless containers",
            "span": PASTA,
            "available": features.pasta,
            "deprecated": False,
            "breakdown": None,
        },
        {
            "name": "slirp4netns",
            "desc": "Legacy user-mode networking (replaced by pasta)",
            "span": SLIRP4NETNS,
            "available": features.slirp4netns,
            "deprecated": is_field_deprecated(SLIRP4NETNS, version),
            "breakdown": None,
        },
        {
            "name": "Container units",
            "desc": ".container unit files",
            "span": QUADLET,
            "available": features.quadlet,
            "deprecated": False,
            "breakdown": _field_breakdown(ContainerCreate),
        },
        {
            "name": "Volume units",
            "desc": ".volume unit files",
            "span": QUADLET,
            "available": features.quadlet,
            "deprecated": False,
            "breakdown": _field_breakdown(VolumeCreate),
        },
        {
            "name": "Network units",
            "desc": ".network unit files",
            "span": QUADLET,
            "available": features.quadlet,
            "deprecated": False,
            "breakdown": _field_breakdown(NetworkCreate),
        },
        {
            "name": "Kube units",
            "desc": ".kube unit files for Kubernetes YAML",
            "span": KUBE_UNITS,
            "available": is_field_available(KUBE_UNITS, version),
            "deprecated": False,
            "breakdown": _field_breakdown(KubeCreate),
        },
        {
            "name": "Image units",
            "desc": ".image unit files for pre-pulling images",
            "span": IMAGE_UNITS,
            "available": features.image_units,
            "deprecated": False,
            "breakdown": _field_breakdown(ImageCreate),
        },
        {
            "name": "Pod units",
            "desc": ".pod unit files for pod management",
            "span": POD_UNITS,
            "available": features.pod_units,
            "deprecated": False,
            "breakdown": _field_breakdown(PodCreate),
        },
        {
            "name": "Build units",
            "desc": ".build unit files for Containerfile builds",
            "span": BUILD_UNITS,
            "available": features.build_units,
            "deprecated": False,
            "breakdown": _field_breakdown(BuildCreate),
        },
        {
            "name": "Quadlet CLI",
            "desc": "podman quadlet install/list/print/rm",
            "span": QUADLET_CLI,
            "available": features.quadlet_cli,
            "deprecated": False,
            "breakdown": None,
        },
        {
            "name": "Artifact units",
            "desc": ".artifact unit files for OCI artifacts",
            "span": ARTIFACT_UNITS,
            "available": features.artifact_units,
            "deprecated": False,
            "breakdown": _field_breakdown(ArtifactCreate),
        },
        {
            "name": "Bundle format",
            "desc": "Multi-unit .quadlets import/export",
            "span": BUNDLE,
            "available": features.bundle,
            "deprecated": False,
            "breakdown": None,
        },
    ]

    # Sort: features with field breakdowns first (alphabetical),
    # then features without (alphabetical).
    feature_rows.sort(key=lambda r: (r["breakdown"] is None, r["name"].lower()))

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/podman_features.html",
        {
            "features": features,
            "feature_rows": feature_rows,
        },
    )


@router.get("/api/compartments/{compartment_id}/journal")
async def stream_compartment_journal(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404)

    async def event_stream():
        async for line in systemd_manager.stream_journal_xe(compartment_id):
            yield f"data: {line}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/compartments/{compartment_id}/agent/logs")
async def stream_agent_logs(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404)

    unit = SafeUnitName.of("quadletman-agent.service", "agent_unit")

    async def event_stream():
        async for line in systemd_manager.stream_journal(compartment_id, unit):
            yield f"data: {line}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/compartments/{compartment_id}/agent/restart")
async def restart_agent(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404)

    unit = SafeUnitName.of("quadletman-agent.service", "agent_unit")
    loop = asyncio.get_event_loop()
    # Re-deploy agent unit file in case it was deleted, then reload + restart
    await loop.run_in_executor(None, systemd_manager.ensure_agent_unit, compartment_id)
    await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)
    await loop.run_in_executor(None, systemd_manager.restart_unit, compartment_id, unit)
    return {"ok": True}


@router.get("/api/compartments/{compartment_id}/containers/{container_name}/logs")
async def stream_logs(
    compartment_id: SafeSlug,
    container_name: SafeUnitName,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404)
    container = next((c for c in comp.containers if c.qm_name == container_name), None)
    if container is None:
        raise HTTPException(status_code=404)
    log_driver = container.log_driver

    _FILE_DRIVERS = {"json-file", "k8s-file"}

    if log_driver in _FILE_DRIVERS:
        podman_container_name = SafeStr.of(
            f"{compartment_id}-{container_name}", "podman_container_name"
        )

        async def event_stream():
            async for line in systemd_manager.stream_podman_logs(
                compartment_id, podman_container_name
            ):
                yield f"data: {line}\n\n"
    else:
        unit = SafeUnitName.of(f"{container_name}.service", "unit_name")

        async def event_stream():
            async for line in systemd_manager.stream_journal(compartment_id, unit):
                yield f"data: {line}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.websocket("/api/compartments/{compartment_id}/containers/{container_name}/terminal")
async def container_terminal(
    websocket: WebSocket,
    compartment_id: SafeSlug,
    container_name: SafeUnitName,
    exec_user: SafeStr | None = Query(default=None, pattern=r"^(root|\d+)$"),
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
    if not origin_host or origin_host.lower() != host.lower():
        await websocket.close(code=4403)
        return

    qm_session = websocket.cookies.get("qm_session")
    if not qm_session or not get_session(SafeStr.of(qm_session, "qm_session")):
        await websocket.close(code=4401)
        return

    if exec_user is not None and (
        not EXEC_USER_RE.match(exec_user) or (exec_user.isdigit() and int(exec_user) > 65535)
    ):
        await websocket.close(code=4400)
        return

    await websocket.accept()
    loop = asyncio.get_event_loop()

    # Quadlet sets ContainerName={compartment_id}-{container_name} in the unit file
    podman_container_name = SafeStr.of(
        f"{compartment_id}-{container_name}", "podman_container_name"
    )
    safe_exec_user = SafeStr.of(exec_user, "exec_user") if exec_user is not None else None
    cmd = systemd_manager.exec_pty_cmd(compartment_id, podman_container_name, safe_exec_user)
    master_fd: int | None = None
    proc: subprocess.Popen | None = None

    try:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True, cwd="/"
        )
        os.close(slave_fd)
    except OSError as exc:
        logger.warning(
            "WebSocket exec PTY failed for %s/%s: %s",
            log_safe(compartment_id),
            log_safe(container_name),
            exc,
        )
        with suppress(Exception):
            await websocket.send_bytes(b"\r\n\x1b[31m[Terminal connection failed]\x1b[0m\r\n")
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

    # Send exit + SIGHUP to clean up the shell inside the container.
    # podman exec sessions outlive the client unless explicitly terminated.
    with suppress(OSError):
        os.write(master_fd, b"exit\n")
    with suppress(OSError):
        os.close(master_fd)
    with suppress(Exception):
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@router.websocket("/api/compartments/{compartment_id}/shell")
async def compartment_shell(
    websocket: WebSocket,
    compartment_id: SafeSlug,
):
    """WebSocket endpoint that opens an interactive bash shell as the qm-* compartment user.

    The qm-* users have /bin/false as their login shell, so this endpoint explicitly
    invokes /bin/bash via sudo. Auth and CSRF validation mirror container_terminal().
    """
    # Origin check — CSRF defence for WebSocket
    origin = websocket.headers.get("origin", "")
    ws_host = websocket.headers.get("host", "")
    origin_host = origin.split("://", 1)[-1] if "://" in origin else origin
    if not origin_host or origin_host.lower() != ws_host.lower():
        await websocket.close(code=4403)
        return

    qm_session = websocket.cookies.get("qm_session")
    if not qm_session or not get_session(SafeStr.of(qm_session, "qm_session")):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    loop = asyncio.get_event_loop()

    cmd = systemd_manager.shell_pty_cmd(compartment_id)
    master_fd: int | None = None
    proc: subprocess.Popen | None = None

    def _setup_controlling_tty():
        """Make the PTY slave the controlling terminal for a new session.

        Called in the child process after fork, before exec.  Without this,
        bash cannot call tcsetpgrp() and prints "cannot set terminal process
        group" / "no job control" warnings.
        """
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    try:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            preexec_fn=_setup_controlling_tty,
            cwd="/",
        )
        os.close(slave_fd)
    except OSError as exc:
        logger.warning("WebSocket shell PTY failed for %s: %s", log_safe(compartment_id), exc)
        with suppress(Exception):
            await websocket.send_bytes(b"\r\n\x1b[31m[Shell connection failed]\x1b[0m\r\n")
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
        os.write(master_fd, b"exit\n")
    with suppress(OSError):
        os.close(master_fd)
    with suppress(Exception):
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

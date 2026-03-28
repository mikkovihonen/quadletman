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
import time
from collections import defaultdict
from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import TEMPLATES as _TEMPLATES
from ..config import settings
from ..db.engine import get_db
from ..i18n import gettext as _t
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
from ..podman import get_features, get_podman_info
from ..security.session import get_session
from ..services import compartment_manager, systemd_manager, user_manager
from .helpers import EXEC_USER_RE, require_auth, run_blocking

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# WebSocket connection limiter — prevents resource exhaustion from many
# concurrent terminal sessions opened by a single client IP.
# ---------------------------------------------------------------------------
_ws_connections: dict[str, int] = defaultdict(int)
_WS_MAX_MSG = settings.ws_max_message_bytes
_WS_RECHECK = settings.ws_session_recheck_interval


def _ws_connect(ip: str) -> bool:
    """Increment connection count for *ip*. Return False if over limit."""
    if _ws_connections[ip] >= settings.ws_max_connections_per_ip:
        return False
    _ws_connections[ip] += 1
    return True


def _ws_disconnect(ip: str) -> None:
    """Decrement connection count for *ip*."""
    _ws_connections[ip] -= 1
    if _ws_connections[ip] <= 0:
        _ws_connections.pop(ip, None)


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
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    return await run_blocking(user_manager.get_compartment_podman_info, compartment_id)


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
            "name": _t("Pasta networking"),
            "desc": _t("Fast user-mode networking for rootless containers"),
            "span": PASTA,
            "available": features.pasta,
            "deprecated": False,
            "breakdown": None,
        },
        {
            "name": "slirp4netns",
            "desc": _t("Legacy user-mode networking (replaced by pasta)"),
            "span": SLIRP4NETNS,
            "available": features.slirp4netns,
            "deprecated": is_field_deprecated(SLIRP4NETNS, version),
            "breakdown": None,
        },
        {
            "name": _t("Container units"),
            "desc": _t(".container unit files"),
            "span": QUADLET,
            "available": features.quadlet,
            "deprecated": False,
            "breakdown": _field_breakdown(ContainerCreate),
        },
        {
            "name": _t("Volume units"),
            "desc": _t(".volume unit files"),
            "span": QUADLET,
            "available": features.quadlet,
            "deprecated": False,
            "breakdown": _field_breakdown(VolumeCreate),
        },
        {
            "name": _t("Network units"),
            "desc": _t(".network unit files"),
            "span": QUADLET,
            "available": features.quadlet,
            "deprecated": False,
            "breakdown": _field_breakdown(NetworkCreate),
        },
        {
            "name": _t("Kube units"),
            "desc": _t(".kube unit files for Kubernetes YAML"),
            "span": KUBE_UNITS,
            "available": is_field_available(KUBE_UNITS, version),
            "deprecated": False,
            "breakdown": _field_breakdown(KubeCreate),
        },
        {
            "name": _t("Image units"),
            "desc": _t(".image unit files for pre-pulling images"),
            "span": IMAGE_UNITS,
            "available": features.image_units,
            "deprecated": False,
            "breakdown": _field_breakdown(ImageCreate),
        },
        {
            "name": _t("Pod units"),
            "desc": _t(".pod unit files for pod management"),
            "span": POD_UNITS,
            "available": features.pod_units,
            "deprecated": False,
            "breakdown": _field_breakdown(PodCreate),
        },
        {
            "name": _t("Build units"),
            "desc": _t(".build unit files for Containerfile builds"),
            "span": BUILD_UNITS,
            "available": features.build_units,
            "deprecated": False,
            "breakdown": _field_breakdown(BuildCreate),
        },
        {
            "name": _t("Quadlet CLI"),
            "desc": _t("podman quadlet install/list/print/rm"),
            "span": QUADLET_CLI,
            "available": features.quadlet_cli,
            "deprecated": False,
            "breakdown": None,
        },
        {
            "name": _t("Artifact units"),
            "desc": _t(".artifact unit files for OCI artifacts"),
            "span": ARTIFACT_UNITS,
            "available": features.artifact_units,
            "deprecated": False,
            "breakdown": _field_breakdown(ArtifactCreate),
        },
        {
            "name": _t("Bundle format"),
            "desc": _t("Multi-unit .quadlets import/export"),
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


@router.get("/api/app/config")
async def app_config(
    request: Request,
    user: SafeUsername = Depends(require_auth),
):
    env_items = [
        ("QUADLETMAN_DB_PATH", str(settings.db_path), _t("Path to the SQLite database file")),
        (
            "QUADLETMAN_VOLUMES_BASE",
            str(settings.volumes_base),
            _t("Base directory for compartment volume storage"),
        ),
        ("QUADLETMAN_HOST", str(settings.host), _t("IP address the HTTP server binds to")),
        ("QUADLETMAN_PORT", settings.port, _t("TCP port the HTTP server listens on")),
        (
            "QUADLETMAN_UNIX_SOCKET",
            str(settings.unix_socket) or "(not set)",
            _t("Unix socket path; when set, host/port are ignored"),
        ),
        (
            "QUADLETMAN_AGENT_SOCKET",
            str(settings.agent_socket),
            _t("Unix socket for per-user monitoring agents"),
        ),
        (
            "QUADLETMAN_SERVICE_USER_PREFIX",
            str(settings.service_user_prefix),
            _t("Prefix for compartment Linux user accounts"),
        ),
        (
            "QUADLETMAN_ALLOWED_GROUPS",
            ", ".join(str(g) for g in settings.allowed_groups),
            _t("Linux groups allowed to log in"),
        ),
        (
            "QUADLETMAN_LOG_LEVEL",
            str(settings.log_level),
            _t("Logging verbosity (DEBUG, INFO, WARNING, ERROR)"),
        ),
        (
            "QUADLETMAN_SECURE_COOKIES",
            settings.secure_cookies,
            _t("Set Secure flag on cookies (enable when using HTTPS)"),
        ),
        (
            "QUADLETMAN_PROCESS_MONITOR_INTERVAL",
            f"{settings.process_monitor_interval}s",
            _t("Seconds between process allowlist checks"),
        ),
        (
            "QUADLETMAN_CONNECTION_MONITOR_INTERVAL",
            f"{settings.connection_monitor_interval}s",
            _t("Seconds between connection allowlist checks"),
        ),
        (
            "QUADLETMAN_IMAGE_UPDATE_CHECK_INTERVAL",
            f"{settings.image_update_check_interval}s",
            _t("Seconds between image update checks"),
        ),
        (
            "QUADLETMAN_SUBPROCESS_TIMEOUT",
            f"{settings.subprocess_timeout}s",
            _t("Default timeout for systemctl and podman commands"),
        ),
        (
            "QUADLETMAN_IMAGE_PULL_TIMEOUT",
            f"{settings.image_pull_timeout}s",
            _t("Timeout for image pull and auto-update operations"),
        ),
        (
            "QUADLETMAN_WEBHOOK_TIMEOUT",
            f"{settings.webhook_timeout}s",
            _t("Timeout for webhook HTTP POST delivery"),
        ),
        (
            "QUADLETMAN_POLL_INTERVAL",
            f"{settings.poll_interval}s",
            _t("Seconds between container state polls"),
        ),
        (
            "QUADLETMAN_METRICS_INTERVAL",
            f"{settings.metrics_interval}s",
            _t("Seconds between metrics history samples"),
        ),
    ]
    runtime_items = [
        ("uid", os.getuid(), _t("User ID the application is running as")),
        ("pid", os.getpid(), _t("Process ID of the running application")),
    ]
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/app_config.html",
        {"env_items": env_items, "runtime_items": runtime_items},
    )


@router.get("/api/app/logs")
async def stream_app_logs(
    user: SafeUsername = Depends(require_auth),
):
    if not await systemd_manager.is_app_service_active():
        msg = _t(
            "Application log is not available — quadletman is not running as a systemd service."
        )
        hint = _t("When installed from a package, journal logs will appear here.")

        async def unavailable_stream():
            yield f"data: __unavailable__:{msg} {hint}\n\n"

        return StreamingResponse(unavailable_stream(), media_type="text/event-stream")

    source = systemd_manager.stream_app_journal()

    async def event_stream():
        try:
            async for line in source:
                yield f"data: {line}\n\n"
        finally:
            await source.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/compartments/{compartment_id}/journal")
async def stream_compartment_journal(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))

    source = systemd_manager.stream_journal_xe(compartment_id)

    async def event_stream():
        try:
            async for line in source:
                yield f"data: {line}\n\n"
        finally:
            await source.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/compartments/{compartment_id}/agent/logs")
async def stream_agent_logs(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))

    unit = SafeUnitName.of("quadletman-agent.service", "agent_unit")
    source = systemd_manager.stream_journal(compartment_id, unit)

    async def event_stream():
        try:
            async for line in source:
                yield f"data: {line}\n\n"
        finally:
            await source.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/compartments/{compartment_id}/agent/restart")
async def restart_agent(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))

    unit = SafeUnitName.of("quadletman-agent.service", "agent_unit")
    # Re-deploy agent unit file in case it was deleted, then reload + restart
    await run_blocking(systemd_manager.ensure_agent_unit, compartment_id)
    await run_blocking(systemd_manager.daemon_reload, compartment_id)
    await run_blocking(systemd_manager.restart_unit, compartment_id, unit)
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
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    container = next((c for c in comp.containers if c.qm_name == container_name), None)
    if container is None:
        raise HTTPException(status_code=404, detail=_t("Container not found"))
    log_driver = container.log_driver

    _FILE_DRIVERS = {"json-file", "k8s-file"}

    if log_driver in _FILE_DRIVERS:
        podman_container_name = SafeStr.of(
            f"{compartment_id}-{container_name}", "podman_container_name"
        )
        source = systemd_manager.stream_podman_logs(compartment_id, podman_container_name)
    else:
        unit = SafeUnitName.of(f"{container_name}.service", "unit_name")
        source = systemd_manager.stream_journal(compartment_id, unit)

    async def event_stream():
        try:
            async for line in source:
                yield f"data: {line}\n\n"
        finally:
            await source.aclose()

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

    if not user_manager.user_exists(compartment_id):
        await websocket.close(code=4404)
        return

    client_ip = websocket.client.host if websocket.client else "unknown"
    if not _ws_connect(client_ip):
        logger.warning("WebSocket connection limit reached for IP %s", client_ip)
        await websocket.close(code=4029)
        return

    await websocket.accept()
    logger.info(
        "Terminal opened: %s/%s (user=%s, ip=%s)",
        log_safe(compartment_id),
        log_safe(container_name),
        log_safe(exec_user or "default"),
        client_ip,
    )
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
        _ws_disconnect(client_ip)
        logger.warning(
            "WebSocket exec PTY failed for %s/%s: %s",
            log_safe(compartment_id),
            log_safe(container_name),
            exc,
        )
        try:
            await websocket.send_bytes(b"\r\n\x1b[31m[Terminal connection failed]\x1b[0m\r\n")
        except Exception as ws_exc:
            logger.warning("Could not send terminal error to WebSocket: %s", ws_exc)
        await websocket.close(code=1011)
        if master_fd is not None:
            with suppress(OSError):
                os.close(master_fd)
        return

    async def _read_loop() -> None:
        last_session_check = time.monotonic()
        try:
            while True:
                data = await loop.run_in_executor(None, os.read, master_fd, 4096)
                if not data:
                    break
                # Periodically re-validate session (catch revoked sessions)
                now = time.monotonic()
                if now - last_session_check > _WS_RECHECK:
                    if not get_session(SafeStr.of(qm_session, "qm_session")):
                        logger.warning("Session invalidated during WebSocket terminal — closing")
                        break
                    last_session_check = now
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
                    if len(msg["bytes"]) > _WS_MAX_MSG:
                        logger.warning(
                            "WebSocket message too large (%d bytes) — closing", len(msg["bytes"])
                        )
                        break
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
    try:
        _, pending = await asyncio.wait_for(
            asyncio.wait([read_task, write_task], return_when=asyncio.FIRST_COMPLETED),
            timeout=settings.terminal_session_timeout,
        )
    except TimeoutError:
        pending = {read_task, write_task}
    for t in pending:
        t.cancel()
    with suppress(asyncio.CancelledError):
        await asyncio.gather(*pending)

    _ws_disconnect(client_ip)
    logger.info(
        "Terminal closed: %s/%s (ip=%s)",
        log_safe(compartment_id),
        log_safe(container_name),
        client_ip,
    )

    # Send exit + SIGHUP to clean up the shell inside the container.
    # podman exec sessions outlive the client unless explicitly terminated.
    with suppress(OSError):
        os.write(master_fd, b"exit\n")
    with suppress(OSError):
        os.close(master_fd)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            with suppress(Exception):  # Best-effort: process may have exited already
                proc.wait(timeout=2)


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

    if not user_manager.user_exists(compartment_id):
        await websocket.close(code=4404)
        return

    client_ip = websocket.client.host if websocket.client else "unknown"
    if not _ws_connect(client_ip):
        logger.warning("WebSocket connection limit reached for IP %s", client_ip)
        await websocket.close(code=4029)
        return

    await websocket.accept()
    logger.info("Shell opened: %s (ip=%s)", log_safe(compartment_id), client_ip)
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
        _ws_disconnect(client_ip)
        logger.warning("WebSocket shell PTY failed for %s: %s", log_safe(compartment_id), exc)
        try:
            await websocket.send_bytes(b"\r\n\x1b[31m[Shell connection failed]\x1b[0m\r\n")
        except Exception as ws_exc:
            logger.warning("Could not send shell error to WebSocket: %s", ws_exc)
        await websocket.close(code=1011)
        if master_fd is not None:
            with suppress(OSError):
                os.close(master_fd)
        return

    async def _read_loop() -> None:
        last_session_check = time.monotonic()
        try:
            while True:
                data = await loop.run_in_executor(None, os.read, master_fd, 4096)
                if not data:
                    break
                now = time.monotonic()
                if now - last_session_check > _WS_RECHECK:
                    if not get_session(SafeStr.of(qm_session, "qm_session")):
                        logger.warning("Session invalidated during WebSocket shell — closing")
                        break
                    last_session_check = now
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
                    if len(msg["bytes"]) > _WS_MAX_MSG:
                        logger.warning(
                            "WebSocket message too large (%d bytes) — closing", len(msg["bytes"])
                        )
                        break
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
    try:
        _, pending = await asyncio.wait_for(
            asyncio.wait([read_task, write_task], return_when=asyncio.FIRST_COMPLETED),
            timeout=settings.terminal_session_timeout,
        )
    except TimeoutError:
        pending = {read_task, write_task}
    for t in pending:
        t.cancel()
    with suppress(asyncio.CancelledError):
        await asyncio.gather(*pending)

    _ws_disconnect(client_ip)
    logger.info("Shell closed: %s (ip=%s)", log_safe(compartment_id), client_ip)

    with suppress(OSError):
        os.write(master_fd, b"exit\n")
    with suppress(OSError):
        os.close(master_fd)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            with suppress(Exception):  # Best-effort: process may have exited already
                proc.wait(timeout=2)

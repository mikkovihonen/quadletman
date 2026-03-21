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

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..db.engine import get_db
from ..models.sanitized import SafeSlug, SafeStr, SafeUnitName, SafeUsername
from ..podman_version import get_podman_info
from ..services import compartment_manager, systemd_manager, user_manager
from ..session import get_session
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
    container = next((c for c in comp.containers if c.name == container_name), None)
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

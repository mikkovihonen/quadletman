import asyncio
import grp
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from uvicorn.logging import DefaultFormatter

from .auth import NotAuthenticated
from .config import settings
from .database import get_db, init_db
from .i18n import resolve_lang, set_translations
from .routers.api import router as api_router
from .routers.ui import router as ui_router
from .services import compartment_manager, notification_service, user_manager

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logging.root.handlers[0].setFormatter(DefaultFormatter("%(levelprefix)s %(name)s: %(message)s"))
logger = logging.getLogger(__name__)

_AUDIT_LOG_PATH = Path("/var/log/quadletman/host.log")
if _AUDIT_LOG_PATH.parent.is_dir() and os.access(_AUDIT_LOG_PATH.parent, os.W_OK):
    _audit_handler = logging.FileHandler(_AUDIT_LOG_PATH)
    _audit_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logging.getLogger("quadletman.host").addHandler(_audit_handler)


def _set_socket_permissions(socket_path: Path) -> None:
    """Set Unix socket ownership and mode so allowed-group members can connect.

    Tries each group in settings.allowed_groups in order and uses the first one
    that exists on this system (covers both 'sudo' on Debian/Ubuntu and 'wheel'
    on RHEL/Fedora).  Falls back to root-only (0600) if none of the groups exist.

    chown is skipped when not running as root (e.g. during development or tests).
    """
    gid = -1  # -1 means "leave unchanged" for os.chown
    for group_name in settings.allowed_groups:
        with suppress(KeyError):
            gid = grp.getgrnam(group_name).gr_gid
            logger.info(
                "Unix socket %s: group set to %r (gid %d), mode 0660", socket_path, group_name, gid
            )
            break
    if gid == -1:
        logger.warning(
            "Unix socket %s: none of the allowed groups %s found — falling back to root-only (0600)",
            socket_path,
            settings.allowed_groups,
        )
        os.chmod(socket_path, 0o600)
    else:
        if os.getuid() == 0:
            os.chown(socket_path, 0, gid)
        else:
            logger.warning("Unix socket %s: not running as root, skipping chown", socket_path)
        os.chmod(socket_path, 0o660)


def _socket_permission_watcher(socket_path: Path) -> None:
    """Background thread: waits for the socket file to appear then sets permissions.

    uvicorn creates the socket after the ASGI lifespan startup completes, so
    permissions cannot be set synchronously during lifespan.
    """
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if socket_path.exists():
            _set_socket_permissions(socket_path)
            return
        time.sleep(0.05)
    logger.warning("Unix socket %s did not appear within 30 s — permissions not set", socket_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.test_auth_user:
        logger.critical(
            "SECURITY WARNING: QUADLETMAN_TEST_AUTH_USER is set to %r — "
            "PAM authentication is completely bypassed. "
            "This setting must NEVER be used in production.",
            settings.test_auth_user,
        )
    logger.info("quadletman starting up")
    await init_db()
    await _migrate_containers_conf()
    monitor_task = asyncio.create_task(notification_service.monitor_loop(get_db))
    metrics_task = asyncio.create_task(notification_service.metrics_loop(get_db))
    process_task = asyncio.create_task(notification_service.process_monitor_loop(get_db))
    connection_task = asyncio.create_task(notification_service.connection_monitor_loop(get_db))
    yield
    monitor_task.cancel()
    metrics_task.cancel()
    process_task.cancel()
    connection_task.cancel()
    with suppress(asyncio.CancelledError):
        await monitor_task
    with suppress(asyncio.CancelledError):
        await metrics_task
    with suppress(asyncio.CancelledError):
        await process_task
    with suppress(asyncio.CancelledError):
        await connection_task
    logger.info("quadletman shutting down")


async def _migrate_containers_conf() -> None:
    """Rewrite containers.conf for all existing compartments on startup.

    Ensures the network_backend setting reflects the current Podman version,
    fixing compartments created before this feature was added or after a Podman upgrade.
    """
    gen = get_db()
    db = await gen.__anext__()
    try:
        compartments = await compartment_manager.list_compartments(db)
    finally:
        with suppress(StopAsyncIteration):
            await gen.__anext__()

    loop = asyncio.get_event_loop()
    for comp in compartments:
        try:
            await loop.run_in_executor(None, user_manager.write_containers_conf, comp.id)
        except Exception as exc:
            logger.warning("Could not update containers.conf for %s: %s", comp.id, exc)


app = FastAPI(
    title="quadletman",
    description="Manage Podman Quadlet container services",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    if request.headers.get("HX-Request") == "true":
        return JSONResponse(
            {"detail": "Session expired"},
            status_code=401,
            headers={"HX-Redirect": "/login"},
        )
    return RedirectResponse("/login", status_code=303)


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security-related HTTP response headers on every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "frame-ancestors 'none'",
        )
        if settings.secure_cookies:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


class I18nMiddleware(BaseHTTPMiddleware):
    """Resolve the best available locale from Accept-Language and install translations."""

    async def dispatch(self, request: Request, call_next) -> Response:
        lang = resolve_lang(request.headers.get("Accept-Language"))
        set_translations(lang)
        request.state.lang = lang
        return await call_next(request)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection for all state-changing requests.

    For authenticated requests (those carrying a qm_session cookie) that use
    mutating HTTP methods, the client must include an X-CSRF-Token header whose
    value matches the qm_csrf cookie.  The /login route is explicitly exempt
    (a stale session cookie must not prevent re-login).
    """

    _EXEMPT_PATHS = {"/login"}

    async def dispatch(self, request: Request, call_next) -> Response:
        if (
            request.method not in _SAFE_METHODS
            and request.url.path not in self._EXEMPT_PATHS
            and request.cookies.get("qm_session")
        ):
            csrf_cookie = request.cookies.get("qm_csrf", "")
            csrf_header = request.headers.get("X-CSRF-Token", "")
            if not (
                csrf_cookie and csrf_header and secrets.compare_digest(csrf_cookie, csrf_header)
            ):
                return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        return await call_next(request)


app.add_middleware(CSRFMiddleware)
app.add_middleware(I18nMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware)
app.include_router(ui_router)
app.include_router(api_router)

_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


def run():
    if settings.unix_socket:
        socket_path = Path(settings.unix_socket)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        threading.Thread(
            target=_socket_permission_watcher, args=(socket_path,), daemon=True
        ).start()
        uvicorn.run(
            "quadletman.main:app",
            uds=settings.unix_socket,
            log_level=settings.log_level.lower(),
        )
        # Remove the socket file on clean exit so it does not block a restart.
        with suppress(OSError):
            socket_path.unlink()
    else:
        uvicorn.run(
            "quadletman.main:app",
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
        )


if __name__ == "__main__":
    run()

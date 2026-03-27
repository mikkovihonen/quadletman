import asyncio
import grp
import logging
import os
import secrets
import signal
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

from .config import settings
from .db.engine import engine, get_db
from .db.migrate import init_db
from .i18n import gettext as _t
from .i18n import resolve_lang, set_translations
from .models.sanitized import SafeStr
from .podman import check_version, clear_caches, get_cached_version_str
from .routers.api import init_podman_globals
from .routers.api import router as api_router
from .routers.ui import router as ui_router
from .security import session as session_store
from .security.auth import NotAuthenticated, set_admin_credentials
from .security.session import reaper_loop as _session_reaper_loop
from .services import compartment_manager, user_manager
from .services import host as host_module

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logging.root.handlers[0].setFormatter(DefaultFormatter("%(levelprefix)s %(name)s: %(message)s"))
logger = logging.getLogger(__name__)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_STATIC_DIR = Path(__file__).parent / "static"

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
        # 0o660: group write required so wheel/sudo users can connect over an SSH tunnel
        # codeql[py/overly-permissive-file] intentional — group-write needed for socket access
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


async def _check_podman_version(*, force: bool = False) -> None:
    """Compare installed Podman version against cache; refresh if changed."""
    loop = asyncio.get_event_loop()
    cached = get_cached_version_str()
    detected = await loop.run_in_executor(None, check_version)
    if detected is None:
        # podman missing, broken, or timed out — keep existing cache
        return
    if detected != cached or force:
        logger.warning(
            "Podman version changed: %r → %r — refreshing feature flags and template globals",
            cached,
            detected,
        )
        await loop.run_in_executor(None, clear_caches)
        await loop.run_in_executor(None, init_podman_globals)


async def _podman_version_watch_loop() -> None:
    """Periodically check whether the installed Podman version has changed."""
    interval = settings.version_check_interval
    if interval <= 0:
        logger.info("Podman version watch disabled (version_check_interval=%d)", interval)
        return
    await asyncio.sleep(30)  # let the app fully start
    while True:
        try:
            await _check_podman_version()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Podman version check error: %s", exc)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Deferred imports: notification_service and agent_api are imported here (not at
    # module level) to break a circular dependency chain. Both modules import
    # compartment_manager at the top, which in turn imports user_manager,
    # quadlet_writer, systemd_manager, etc. If main.py imported them at the top,
    # Python would deadlock during module initialization.
    from .services import agent_api, notification_service

    if settings.test_auth_user:
        if settings.secure_cookies:
            raise RuntimeError(
                "QUADLETMAN_TEST_AUTH_USER cannot be used with QUADLETMAN_SECURE_COOKIES=true. "
                "This combination suggests a production environment with auth bypass enabled."
            )
        logger.critical(
            "SECURITY WARNING: QUADLETMAN_TEST_AUTH_USER is set to %r — "
            "PAM authentication is completely bypassed. "
            "This setting must NEVER be used in production.",
            settings.test_auth_user,
        )
    logger.info("quadletman starting up (uid=%d)", os.getuid())
    init_podman_globals()
    await init_db()
    await _migrate_containers_conf()

    _bg_tasks: list[asyncio.Task] = []
    _agent_server: asyncio.Server | None = None

    # Podman version watch and session reaper — run in both root and non-root mode
    _bg_tasks.append(asyncio.create_task(_podman_version_watch_loop()))
    _bg_tasks.append(asyncio.create_task(_session_reaper_loop()))

    # SIGHUP triggers an immediate version re-check
    loop = asyncio.get_running_loop()

    def _handle_sighup() -> None:
        logger.info("SIGHUP received — refreshing Podman version detection")
        asyncio.ensure_future(_check_podman_version(force=True))

    with suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGHUP, _handle_sighup)

    if os.getuid() == 0:
        # Root mode: use centralized monitoring loops (backward compatible)
        logger.info("Running as root — using centralized monitoring loops")
        _bg_tasks.append(asyncio.create_task(notification_service.monitor_loop(get_db)))
        _bg_tasks.append(asyncio.create_task(notification_service.metrics_loop(get_db)))
        _bg_tasks.append(asyncio.create_task(notification_service.process_monitor_loop(get_db)))
        _bg_tasks.append(asyncio.create_task(notification_service.connection_monitor_loop(get_db)))
        _bg_tasks.append(
            asyncio.create_task(notification_service.image_update_monitor_loop(get_db))
        )
    else:
        # Non-root mode: start agent API socket, per-user agents report via it
        logger.info("Running as non-root — starting agent API for per-user monitoring agents")
        _agent_server = await agent_api.start_agent_api(str(settings.agent_socket), get_db)

    yield

    with suppress(NotImplementedError, ValueError):
        loop.remove_signal_handler(signal.SIGHUP)
    for task in _bg_tasks:
        task.cancel()
    for task in _bg_tasks:
        with suppress(asyncio.CancelledError):
            await task
    if _agent_server is not None:
        _agent_server.close()
        await _agent_server.wait_closed()
    await engine.dispose()
    logger.info("quadletman shutting down")


async def _migrate_containers_conf() -> None:
    """Rewrite containers.conf for all existing compartments on startup.

    Ensures the network_backend setting reflects the current Podman version,
    fixing compartments created before this feature was added or after a Podman upgrade.

    Skipped in non-root mode: admin escalation requires a web session which
    is not available during startup.  The migration will run on next
    compartment create/update instead.
    """
    if os.getuid() != 0:
        return

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


@app.exception_handler(compartment_manager.CompartmentBusy)
async def compartment_busy_handler(request: Request, exc: compartment_manager.CompartmentBusy):
    return JSONResponse(
        {"detail": _t("Compartment is busy — please try again shortly")},
        status_code=409,
    )


@app.exception_handler(compartment_manager.FileWriteFailed)
async def file_write_failed_handler(request: Request, exc: compartment_manager.FileWriteFailed):
    if exc.rolled_back:
        detail = _t("Failed to write unit files — changes have been rolled back.")
    else:
        detail = _t(
            "Failed to write unit files — database was updated but unit files are out of sync. "
            "Use Resync to restore consistency."
        )
    return JSONResponse({"detail": detail}, status_code=500)


@app.exception_handler(host_module.AdminSessionRequired)
async def admin_session_required_handler(request: Request, exc: host_module.AdminSessionRequired):
    return JSONResponse(
        {
            "detail": "Admin credentials required for this operation.",
            "code": "admin_credentials_required",
        },
        status_code=403,
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
        raw_lang = request.headers.get("Accept-Language")
        lang = resolve_lang(
            SafeStr.of(raw_lang, "Accept-Language") if raw_lang is not None else None
        )
        set_translations(lang)
        request.state.lang = lang
        return await call_next(request)


class AdminCredentialMiddleware(BaseHTTPMiddleware):
    """Populate the admin-credential ContextVar for the current request.

    When the request carries a valid session cookie that has stored credentials,
    the (username, password) pair is made available to ``host.py`` via the
    ``get_admin_credentials()`` function for privilege escalation.

    The ContextVar is reset after each request so credentials never leak across
    requests even in edge cases.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        sid = request.cookies.get("qm_session")
        if sid and os.getuid() != 0:
            creds = session_store.get_session_credentials(SafeStr.of(sid, "qm_session"))
            set_admin_credentials(creds)
        try:
            return await call_next(request)
        finally:
            set_admin_credentials(None)


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
            # Always run constant-time comparison even when tokens are empty,
            # so the response time does not reveal whether a token was present.
            if not secrets.compare_digest(csrf_cookie, csrf_header) or not csrf_cookie:
                return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        return await call_next(request)


app.add_middleware(AdminCredentialMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(I18nMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware)
app.include_router(ui_router)
app.include_router(api_router)

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

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .auth import NotAuthenticated
from .config import settings
from .database import get_db, init_db
from .routers.api import router as api_router
from .routers.ui import router as ui_router
from .services import compartment_manager, user_manager

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_AUDIT_LOG_PATH = Path("/var/log/quadletman/host.log")
if _AUDIT_LOG_PATH.parent.is_dir():
    _audit_handler = logging.FileHandler(_AUDIT_LOG_PATH)
    _audit_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logging.getLogger("quadletman.host").addHandler(_audit_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("quadletman starting up")
    await init_db()
    await _migrate_containers_conf()
    yield
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
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware)
app.include_router(ui_router)
app.include_router(api_router)

_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


def run():
    uvicorn.run(
        "quadletman.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()

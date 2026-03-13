import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .auth import NotAuthenticated
from .config import settings
from .database import get_db, init_db
from .routers.api import router as api_router
from .routers.ui import router as ui_router
from .services import service_manager, user_manager

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("quadletman starting up")
    await init_db()
    await _migrate_containers_conf()
    yield
    logger.info("quadletman shutting down")


async def _migrate_containers_conf() -> None:
    """Rewrite containers.conf for all existing services on startup.

    Ensures the network_backend setting reflects the current Podman version,
    fixing services created before this feature was added or after a Podman upgrade.
    """
    import asyncio

    gen = get_db()
    db = await gen.__anext__()
    try:
        services = await service_manager.list_services(db)
    finally:
        with suppress(StopAsyncIteration):
            await gen.__anext__()

    loop = asyncio.get_event_loop()
    for svc in services:
        try:
            await loop.run_in_executor(None, user_manager.write_containers_conf, svc.id)
        except Exception as exc:
            logger.warning("Could not update containers.conf for %s: %s", svc.id, exc)


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


app.add_middleware(GZipMiddleware)
app.include_router(ui_router)
app.include_router(api_router)

_STATIC_DIR = Path(__file__).parent.parent / "static"
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

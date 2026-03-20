"""REST API + HTMX-aware routes for quadletman."""

import asyncio
import hashlib
import tempfile
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..models.sanitized import SafeStr
from ..podman_version import get_features, get_log_drivers, get_network_drivers, get_podman_info
from ..services import compartment_manager
from ..services.selinux import is_selinux_active
from ..session import delete_session
from . import compartments as _compartments_router
from . import containers as _containers_router
from . import host as _host_router
from . import logs as _logs_router
from . import secrets as _secrets_router
from . import templates as _templates_router
from . import timers as _timers_router
from . import volumes as _volumes_router

router = APIRouter()

_src_dir = Path(__file__).parent.parent / "static" / "src"
_src_hash = hashlib.md5(
    b"".join(p.read_bytes() for p in sorted(_src_dir.glob("*.js")))
).hexdigest()[:8]
_TEMPLATES.env.globals["podman"] = get_features()
_TEMPLATES.env.globals["static_v"] = _src_hash
_TEMPLATES.env.globals["net_drivers"] = get_network_drivers()
_TEMPLATES.env.globals["log_drivers"] = get_log_drivers()
_TEMPLATES.env.globals["selinux_active"] = is_selinux_active()
_dist = get_podman_info().get("host", {}).get("distribution", {})
_TEMPLATES.env.globals["host_distro"] = (
    f"{_dist.get('distribution', '')} {_dist.get('version', '')}".strip()
)
_TEMPLATES.env.filters["urlencode"] = urllib.parse.quote

router.include_router(_compartments_router.router)
router.include_router(_containers_router.router)
router.include_router(_volumes_router.router)
router.include_router(_logs_router.router)
router.include_router(_host_router.router)
router.include_router(_secrets_router.router)
router.include_router(_timers_router.router)
router.include_router(_templates_router.router)


@router.post("/api/logout")
async def logout(qm_session: str = Cookie(default=None)):
    """Invalidate the server-side session and clear the session cookie."""
    if qm_session:
        delete_session(SafeStr.of(qm_session, "qm_session"))
    resp = Response(status_code=204)
    resp.delete_cookie("qm_session")
    resp.delete_cookie("qm_csrf")
    return resp


@router.get("/api/dashboard")
async def get_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: str = Depends(require_auth),
):
    services = await compartment_manager.list_compartments(db)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/dashboard.html",
        {"services": services, "user": user},
    )


@router.get("/api/help")
async def get_help(request: Request, user: str = Depends(require_auth)):
    return _TEMPLATES.TemplateResponse(request, "partials/help.html", {})


@router.get("/api/backup/db")
async def download_db_backup(user: str = Depends(require_auth)) -> FileResponse:
    """Stream a hot backup of the SQLite database using the SQLite Online Backup API.

    Uses VACUUM INTO so the backup is consistent even while the DB is
    in WAL mode with concurrent writes in flight.
    """
    from ..config import settings

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tmp = tempfile.mktemp(suffix=".db", prefix=f"quadletman-backup-{ts}-")

    def _backup() -> None:
        # VACUUM INTO creates a compacted, consistent copy without needing exclusive lock.
        import sqlite3

        src = sqlite3.connect(settings.db_path)
        try:
            src.execute(f"VACUUM INTO '{tmp}'")  # noqa: S608 — path is internal, not user-supplied
        finally:
            src.close()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _backup)

    filename = f"quadletman-backup-{ts}.db"
    return FileResponse(
        tmp,
        media_type="application/octet-stream",
        filename=filename,
        background=None,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

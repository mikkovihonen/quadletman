"""REST API + HTMX-aware routes for quadletman."""

import asyncio
import hashlib
import os
import shutil
import tempfile
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from ..auth import require_auth
from ..config import TEMPLATES as _TEMPLATES
from ..db.engine import get_db
from ..keyring import is_available as _keyring_available
from ..models.api import (
    AllowlistRuleCreate,
    ArtifactCreate,
    BuildCreate,
    CompartmentCreate,
    ContainerCreate,
    ImageCreate,
    NetworkCreate,
    NotificationHookCreate,
    PodCreate,
    SecretCreate,
    TimerCreate,
    VolumeCreate,
)
from ..models.api.container import BindMount
from ..models.api.volume import VolumeMount
from ..models.constraints import DIRECTION_CHOICES, PROTO_CHOICES
from ..models.sanitized import SafeStr, SafeUsername
from ..models.version_span import (
    SLIRP4NETNS,
    field_availability,
    field_tooltips,
    value_availability,
)
from ..podman_version import get_features, get_host_distro, get_log_drivers, get_network_drivers
from ..services import compartment_manager
from ..services.selinux import is_selinux_active
from ..session import delete_session
from . import builds as _builds_router
from . import compartments as _compartments_router
from . import containers as _containers_router
from . import host as _host_router
from . import logs as _logs_router
from . import networks as _networks_router
from . import secrets as _secrets_router
from . import templates as _templates_router
from . import timers as _timers_router
from . import volumes as _volumes_router
from .helpers.common import (
    choices_for_template,
    field_choices_for_template,
    field_constraints_for_template,
)

router = APIRouter()

_src_dir = Path(__file__).parent.parent / "static" / "src"
_src_hash = hashlib.md5(
    b"".join(p.read_bytes() for p in sorted(_src_dir.glob("*.js")))
).hexdigest()[:8]
# ---------------------------------------------------------------------------
# Jinja2 template globals — pre-computed at app startup, available to all
# templates without being passed in per-request context.
#
# podman / podman_slirp4netns  Detected Podman version and feature flags.
# static_v                     Cache-busting hash for JS/CSS assets.
# net_drivers / log_drivers    System-wide driver lists from `podman info`.
# selinux_active               Whether SELinux is enforcing/permissive.
# host_distro                  Host OS name + version for the footer.
#
# *_v   (e.g. container_v)    {field: bool} — is this field available on the
#                              detected Podman version?  Templates use
#                              `container_v.get("field", true)` to disable
#                              form inputs for unsupported fields.
# *_vt  (e.g. container_vt)   {field: str} — human-readable tooltip explaining
#                              the version requirement ("Requires Podman X.Y+").
# *_vc  (e.g. volume_vc)      {field: {value: bool}} — per-value availability
#                              for fields with value_constraints.
#
# *_fc  (e.g. container_fc)   {field: [choice_dicts]} — pre-built option lists
#                              for static FieldChoices fields.  Each dict has
#                              {value, label, is_default, available, tooltip}.
#                              Templates render them via the select_choices
#                              macro.  Dynamic choices (log_driver, vol_driver,
#                              etc.) are computed per-request in router helpers.
# direction_choices /          Standalone choice lists for AllowlistRule form
# proto_choices                fields that are not on a *Create model.
# ---------------------------------------------------------------------------
_TEMPLATES.env.globals["keyring_available"] = _keyring_available()
_TEMPLATES.env.globals["podman_slirp4netns"] = SLIRP4NETNS
_TEMPLATES.env.globals["static_v"] = _src_hash
_TEMPLATES.env.globals["selinux_active"] = is_selinux_active()
_TEMPLATES.env.globals["direction_choices"] = choices_for_template(DIRECTION_CHOICES)
_TEMPLATES.env.globals["proto_choices"] = choices_for_template(PROTO_CHOICES)
# Pre-computed FieldConstraints for HTML5 constraint attributes (pattern, maxlength, etc.).
_TEMPLATES.env.globals["container_cn"] = field_constraints_for_template(ContainerCreate)
_TEMPLATES.env.globals["build_cn"] = field_constraints_for_template(BuildCreate)
_TEMPLATES.env.globals["image_unit_cn"] = field_constraints_for_template(ImageCreate)
_TEMPLATES.env.globals["volume_cn"] = field_constraints_for_template(VolumeCreate)
_TEMPLATES.env.globals["pod_cn"] = field_constraints_for_template(PodCreate)
_TEMPLATES.env.globals["timer_cn"] = field_constraints_for_template(TimerCreate)
_TEMPLATES.env.globals["secret_cn"] = field_constraints_for_template(SecretCreate)
_TEMPLATES.env.globals["notification_cn"] = field_constraints_for_template(NotificationHookCreate)
_TEMPLATES.env.globals["compartment_cn"] = field_constraints_for_template(CompartmentCreate)
_TEMPLATES.env.globals["network_cn"] = field_constraints_for_template(NetworkCreate)
_TEMPLATES.env.globals["allowlist_cn"] = field_constraints_for_template(AllowlistRuleCreate)
_TEMPLATES.env.globals["volume_mount_cn"] = field_constraints_for_template(VolumeMount)
_TEMPLATES.env.globals["bind_mount_cn"] = field_constraints_for_template(BindMount)
_TEMPLATES.env.globals["artifact_cn"] = field_constraints_for_template(ArtifactCreate)
_TEMPLATES.env.filters["urlencode"] = urllib.parse.quote


def init_podman_globals() -> None:
    """Populate Jinja2 template globals that depend on Podman version detection.

    Called from ``main.py`` lifespan rather than at import time so that the
    application environment is fully initialised before shelling out to podman.
    """
    _podman = get_features()
    _g = _TEMPLATES.env.globals
    _g["podman"] = _podman
    _g["net_drivers"] = get_network_drivers()
    _g["log_drivers"] = get_log_drivers()
    _g["host_distro"] = get_host_distro()
    _g["container_v"] = field_availability(ContainerCreate, _podman.version)
    _g["container_vt"] = field_tooltips(ContainerCreate, _podman.version)
    _g["image_unit_v"] = field_availability(ImageCreate, _podman.version)
    _g["image_unit_vt"] = field_tooltips(ImageCreate, _podman.version)
    _g["build_v"] = field_availability(BuildCreate, _podman.version)
    _g["build_vt"] = field_tooltips(BuildCreate, _podman.version)
    _g["pod_v"] = field_availability(PodCreate, _podman.version)
    _g["pod_vt"] = field_tooltips(PodCreate, _podman.version)
    _g["volume_v"] = field_availability(VolumeCreate, _podman.version)
    _g["volume_vt"] = field_tooltips(VolumeCreate, _podman.version)
    _g["volume_vc"] = value_availability(VolumeCreate, _podman.version)
    _g["network_v"] = field_availability(NetworkCreate, _podman.version)
    _g["network_vt"] = field_tooltips(NetworkCreate, _podman.version)
    _g["container_fc"] = field_choices_for_template(ContainerCreate, _podman.version)
    _g["build_fc"] = field_choices_for_template(BuildCreate, _podman.version)
    _g["image_unit_fc"] = field_choices_for_template(ImageCreate, _podman.version)
    _g["pod_fc"] = field_choices_for_template(PodCreate, _podman.version)
    _g["artifact_v"] = field_availability(ArtifactCreate, _podman.version)
    _g["artifact_vt"] = field_tooltips(ArtifactCreate, _podman.version)
    _g["volume_fc"] = field_choices_for_template(VolumeCreate, _podman.version)
    _g["notification_fc"] = field_choices_for_template(NotificationHookCreate, _podman.version)


router.include_router(_builds_router.router)
router.include_router(_compartments_router.router)
router.include_router(_containers_router.router)
router.include_router(_volumes_router.router)
router.include_router(_logs_router.router)
router.include_router(_networks_router.router)
router.include_router(_host_router.router)
router.include_router(_secrets_router.router)
router.include_router(_timers_router.router)
router.include_router(_templates_router.router)


@router.post("/api/logout")
async def logout(qm_session: SafeStr = Cookie(default=None)):
    """Invalidate the server-side session and clear the session cookie."""
    if qm_session:
        delete_session(qm_session)
    resp = Response(status_code=204)
    resp.delete_cookie("qm_session")
    resp.delete_cookie("qm_csrf")
    return resp


@router.get("/api/dashboard")
async def get_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: SafeUsername = Depends(require_auth),
):
    services = await compartment_manager.list_compartments(db)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/dashboard.html",
        {"services": services, "user": user},
    )


@router.get("/api/help")
async def get_help(request: Request, user: SafeUsername = Depends(require_auth)):
    return _TEMPLATES.TemplateResponse(request, "partials/help.html", {})


@router.get("/api/session-info-partial")
async def get_session_info(request: Request, user: SafeUsername = Depends(require_auth)):
    return _TEMPLATES.TemplateResponse(request, "partials/session_info.html", {"user": user})


@router.get("/api/backup/db")
async def download_db_backup(user: SafeUsername = Depends(require_auth)) -> FileResponse:
    """Stream a hot backup of the SQLite database using the SQLite Online Backup API.

    Uses VACUUM INTO so the backup is consistent even while the DB is
    in WAL mode with concurrent writes in flight.
    """
    from ..config import settings

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tmp_dir = tempfile.mkdtemp(prefix="quadletman-backup-")
    tmp = os.path.join(tmp_dir, f"quadletman-backup-{ts}.db")

    def _backup() -> None:
        # VACUUM INTO creates a compacted, consistent copy without needing exclusive lock.
        import sqlite3

        src = sqlite3.connect(settings.db_path)
        try:
            src.execute(f"VACUUM INTO '{tmp}'")  # noqa: S608 — path is internal, not user-supplied
        finally:
            src.close()
        # Restrict backup file permissions — it contains the full production database
        os.chmod(tmp, 0o600)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _backup)

    filename = f"quadletman-backup-{ts}.db"
    return FileResponse(
        tmp,
        media_type="application/octet-stream",
        filename=filename,
        background=BackgroundTask(shutil.rmtree, tmp_dir, True),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

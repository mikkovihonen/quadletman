"""UI routes serving full HTML pages."""

import hashlib
import logging
from pathlib import Path

import pam
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import session as session_store
from ..auth import _user_in_allowed_group, require_auth
from ..config import settings
from ..podman_version import get_features, get_podman_info

logger = logging.getLogger(__name__)
router = APIRouter()
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_TEMPLATES.env.globals["podman"] = get_features()
_ui_utils = Path(__file__).parent.parent / "static" / "vendor" / "ui-utils.js"
_TEMPLATES.env.globals["static_v"] = hashlib.md5(_ui_utils.read_bytes()).hexdigest()[:8]
_dist = get_podman_info().get("host", {}).get("distribution", {})
_TEMPLATES.env.globals["host_distro"] = (
    f"{_dist.get('distribution', '')} {_dist.get('version', '')}".strip()
)


def _safe_next(url: str) -> str:
    """Prevent open redirect — only allow relative paths on this host."""
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return "/"


@router.get("/login", include_in_schema=False)
async def login_page(request: Request, error: str = ""):
    return _TEMPLATES.TemplateResponse("login.html", {"request": request, "error": error})


@router.post("/login", include_in_schema=False)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/"),
):
    p = pam.pam()
    if p.authenticate(username, password) and _user_in_allowed_group(username):
        logger.info("Authenticated user: %s", username)
        sid, csrf = session_store.create_session(username)
        resp = RedirectResponse(url=_safe_next(next), status_code=303)
        cookie_kwargs = {
            "samesite": "strict",
            "max_age": 8 * 3600,
            "secure": settings.secure_cookies,
        }
        resp.set_cookie("qm_session", sid, httponly=True, **cookie_kwargs)
        resp.set_cookie("qm_csrf", csrf, httponly=False, **cookie_kwargs)
        return resp
    logger.warning("Authentication failed for user: %s", username)
    return _TEMPLATES.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": "Invalid credentials or insufficient privileges",
            "next": next,
        },
        status_code=401,
    )


@router.get("/", include_in_schema=False)
async def index(request: Request, user: str = Depends(require_auth)):
    return _TEMPLATES.TemplateResponse("index.html", {"request": request, "user": user})


@router.get("/compartments/{compartment_id}", include_in_schema=False)
async def compartment_page(
    request: Request, compartment_id: str, user: str = Depends(require_auth)
):
    return _TEMPLATES.TemplateResponse("index.html", {"request": request, "user": user})


@router.get("/events", include_in_schema=False)
async def events_page(request: Request, user: str = Depends(require_auth)):
    return _TEMPLATES.TemplateResponse("index.html", {"request": request, "user": user})


@router.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

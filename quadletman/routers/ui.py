"""UI routes serving full HTML pages."""

import logging
import os
import pwd

import pam
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from .. import session as session_store
from ..auth import _user_in_allowed_group, require_auth
from ..config import TEMPLATES as _TEMPLATES
from ..config import settings
from ..models.sanitized import SafeRedirectPath, SafeSlug, SafeStr, SafeUsername, log_safe
from .helpers import check_login_rate_limit, record_login_attempt

logger = logging.getLogger(__name__)
router = APIRouter()

_TEMPLATES.env.globals["app_user"] = pwd.getpwuid(os.getuid()).pw_name


@router.get("/login", include_in_schema=False)
async def login_page(request: Request, error: SafeStr = SafeStr.trusted("", "default")):
    return _TEMPLATES.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login", include_in_schema=False)
async def login_submit(
    request: Request,
    username: SafeUsername = Form(...),
    password: SafeStr = Form(...),
    next: SafeRedirectPath = Form(default=SafeRedirectPath.trusted("/", "default")),
):
    client_ip = request.client.host if request.client else "unknown"
    if not check_login_rate_limit(client_ip):
        logger.warning("Login rate limit exceeded for IP: %s", client_ip)
        return _TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {"error": "Too many login attempts. Please try again later.", "next": next},
            status_code=429,
        )
    # Record every attempt (success or failure) to prevent credential-stuffing
    # attacks that rotate across multiple valid accounts.
    record_login_attempt(client_ip)
    p = pam.pam()
    if p.authenticate(username, password) and _user_in_allowed_group(username):
        logger.info("Authenticated user: %s", log_safe(username))
        sid, csrf = session_store.create_session(username, password=password)
        resp = RedirectResponse(url=next, status_code=303)
        cookie_kwargs = {
            "samesite": "strict",
            "max_age": 8 * 3600,
            "secure": settings.secure_cookies,
        }
        resp.set_cookie("qm_session", sid, httponly=True, **cookie_kwargs)
        resp.set_cookie("qm_csrf", csrf, httponly=False, **cookie_kwargs)
        return resp
    logger.warning("Authentication failed for user: %s from IP: %s", log_safe(username), client_ip)
    return _TEMPLATES.TemplateResponse(
        request,
        "login.html",
        {"error": "Invalid credentials or insufficient privileges", "next": next},
        status_code=401,
    )


@router.get("/", include_in_schema=False)
async def index(request: Request, user: SafeUsername = Depends(require_auth)):
    return _TEMPLATES.TemplateResponse(request, "index.html", {"user": user})


@router.get("/compartments/{compartment_id}", include_in_schema=False)
async def compartment_page(
    request: Request, compartment_id: SafeSlug, user: SafeUsername = Depends(require_auth)
):
    return _TEMPLATES.TemplateResponse(request, "index.html", {"user": user})


@router.get("/events", include_in_schema=False)
async def events_page(request: Request, user: SafeUsername = Depends(require_auth)):
    return _TEMPLATES.TemplateResponse(request, "index.html", {"user": user})


@router.get("/help", include_in_schema=False)
async def help_page(request: Request, user: SafeUsername = Depends(require_auth)):
    return _TEMPLATES.TemplateResponse(request, "index.html", {"user": user})


@router.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

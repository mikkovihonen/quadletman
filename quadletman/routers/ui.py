"""UI routes serving full HTML pages."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from ..auth import require_auth
from ..podman_version import get_features

router = APIRouter()
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_TEMPLATES.env.globals["podman"] = get_features()


@router.get("/", include_in_schema=False)
async def index(request: Request, user: str = Depends(require_auth)):
    return _TEMPLATES.TemplateResponse("index.html", {"request": request, "user": user})


@router.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

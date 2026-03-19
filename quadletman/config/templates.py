"""Shared Jinja2Templates instance with i18n extension installed.

Both routers (api.py and ui.py) import TEMPLATES from here so there is a
single Jinja2 environment with the i18n globals pre-configured.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from ..i18n import gettext, ngettext


def _fmt_bytes(b: int) -> str:
    if b >= 1_000_000_000:
        return f"{b / 1_000_000_000:.1f} GB"
    if b >= 1_000_000:
        return f"{b / 1_000_000:.1f} MB"
    if b >= 1_000:
        return f"{b / 1_000:.1f} KB"
    return f"{b} B"


_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

TEMPLATES = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Install i18n extension on the shared environment.
# The extension enables {% trans %}…{% endtrans %} syntax.
TEMPLATES.env.add_extension("jinja2.ext.i18n")

# Install contextvars-backed callables as Jinja2 env globals so every template
# can use _() and ngettext() without explicitly passing them in the context.
# The callables read the per-request ContextVar set by I18nMiddleware.
TEMPLATES.env.install_gettext_callables(  # type: ignore[attr-defined]
    gettext,
    ngettext,
    newstyle=False,
)

TEMPLATES.env.filters["fmt_bytes"] = _fmt_bytes

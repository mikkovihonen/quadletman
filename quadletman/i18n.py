"""Lightweight i18n helpers for quadletman.

Uses a contextvars.ContextVar to store per-request translations, which is safe
for async request handlers.  The middleware in main.py calls set_translations()
at the start of each request; all other code calls gettext() / ngettext().

Usage in Python code:
    from quadletman.i18n import gettext as _
    raise HTTPException(404, _("Compartment not found"))

Usage in Jinja2 templates (via env globals — no import needed):
    {{ _("Compartment not found") }}
    {{ ngettext("%(n)d container", "%(n)d containers", count) % {"n": count} }}
"""

from contextvars import ContextVar
from pathlib import Path

from babel.support import NullTranslations, Translations

_LOCALE_DIR = Path(__file__).parent / "locale"
_DOMAIN = "quadletman"

# Available locale codes — extend as .po catalogs are added
AVAILABLE_LANGS: frozenset[str] = frozenset({"en"})
DEFAULT_LANG = "en"

_translations_var: ContextVar[NullTranslations | None] = ContextVar("qm_translations", default=None)

# Cache loaded Translations objects so each locale is loaded at most once
_cache: dict[str, NullTranslations] = {}


def _load(lang: str) -> NullTranslations:
    if lang not in _cache:
        try:
            _cache[lang] = Translations.load(str(_LOCALE_DIR), [lang], domain=_DOMAIN)
        except Exception:
            _cache[lang] = NullTranslations()
    return _cache[lang]


def resolve_lang(accept_language: str | None) -> str:
    """Return the best available locale for the given Accept-Language header value."""
    if not accept_language:
        return DEFAULT_LANG
    # Parse "en-US,en;q=0.9,fi;q=0.8" → ordered list of language tags
    parts = []
    for item in accept_language.split(","):
        tag, _, q = item.strip().partition(";q=")
        lang = tag.strip().split("-")[0].lower()
        try:
            quality = float(q) if q else 1.0
        except ValueError:
            quality = 0.0
        parts.append((quality, lang))
    parts.sort(reverse=True)
    for _, lang in parts:
        if lang in AVAILABLE_LANGS:
            return lang
    return DEFAULT_LANG


def set_translations(lang: str) -> None:
    """Install translations for the current async context (called by middleware)."""
    _translations_var.set(_load(lang))


def _get() -> NullTranslations:
    t = _translations_var.get()
    return t if t is not None else NullTranslations()


def gettext(message: str) -> str:
    return _get().gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _get().ngettext(singular, plural, n)

"""Tests for quadletman/i18n.py — locale resolution, translation loading, gettext."""

from quadletman import i18n
from quadletman.models.sanitized import SafeStr


def _s(v: str) -> SafeStr:
    return SafeStr.trusted(v, "test")


class TestResolveLang:
    def test_returns_default_for_none(self):
        assert str(i18n.resolve_lang(None)) == "en"

    def test_returns_default_for_empty(self):
        assert str(i18n.resolve_lang(_s(""))) == "en"

    def test_returns_fi_for_finnish(self):
        assert str(i18n.resolve_lang(_s("fi"))) == "fi"

    def test_parses_accept_language_header(self):
        assert str(i18n.resolve_lang(_s("fi-FI,fi;q=0.9,en;q=0.8"))) == "fi"

    def test_falls_back_to_english(self):
        assert str(i18n.resolve_lang(_s("de-DE,de;q=0.9"))) == "en"

    def test_handles_quality_values(self):
        assert str(i18n.resolve_lang(_s("de;q=0.9,fi;q=0.8"))) == "fi"

    def test_handles_invalid_quality(self):
        result = i18n.resolve_lang(_s("fi;q=bad,en;q=0.5"))
        assert str(result) in ("fi", "en")


class TestSetTranslations:
    def test_set_and_get_translations(self):
        i18n.set_translations(_s("en"))
        result = i18n.gettext("test string")
        assert isinstance(result, str)

    def test_ngettext_singular(self):
        i18n.set_translations(_s("en"))
        result = i18n.ngettext("%(n)d item", "%(n)d items", 1)
        assert "item" in result

    def test_ngettext_plural(self):
        i18n.set_translations(_s("en"))
        result = i18n.ngettext("%(n)d item", "%(n)d items", 5)
        assert "items" in result


class TestLoad:
    def test_loads_english(self):
        trans = i18n._load(_s("en"))
        assert trans is not None

    def test_loads_finnish(self):
        trans = i18n._load(_s("fi"))
        assert trans is not None

    def test_caches_loaded_translations(self):
        # Clear cache
        i18n._cache.clear()
        t1 = i18n._load(_s("en"))
        t2 = i18n._load(_s("en"))
        assert t1 is t2

    def test_unknown_lang_returns_null(self):
        trans = i18n._load(_s("xx"))
        # Should return NullTranslations without error
        assert trans is not None

"""Tests for _sanitize_db_row and the DB fix persistence pipeline."""

import re
import uuid
from pathlib import Path

import pytest

from quadletman.db.orm import ContainerRow
from quadletman.models.api.common import (
    _current_db_fixes,
    _sanitize_db_row,
    _validate_row,
    _validate_rows,
)
from quadletman.models.api.container import Container


class TestSanitizeDbRow:
    """Unit tests for _sanitize_db_row."""

    def test_valid_values_unchanged(self):
        d = _make_container_dict(environment_file="/etc/myapp/env")
        fixes = _sanitize_db_row(d, Container)
        assert fixes == {}
        assert d["environment_file"] == "/etc/myapp/env"

    def test_empty_values_unchanged(self):
        d = _make_container_dict(environment_file="")
        fixes = _sanitize_db_row(d, Container)
        assert fixes == {}

    def test_invalid_value_reset_to_default(self):
        d = _make_container_dict(environment_file="not-absolute")
        fixes = _sanitize_db_row(d, Container)
        assert "environment_file" in fixes
        assert d["environment_file"] == ""
        assert fixes["environment_file"] == ""

    def test_multiple_invalid_fields(self):
        d = _make_container_dict(
            environment_file="bad",
            memory_limit="not-a-size",
        )
        fixes = _sanitize_db_row(d, Container)
        assert "environment_file" in fixes
        assert "memory_limit" in fixes
        assert d["environment_file"] == ""
        assert d["memory_limit"] == ""

    def test_sets_context_var(self):
        _current_db_fixes.set({})
        d = _make_container_dict(environment_file="bad")
        _sanitize_db_row(d, Container)
        assert _current_db_fixes.get({}).get("environment_file") == ""

    def test_context_var_empty_when_no_fixes(self):
        _current_db_fixes.set({"stale": "value"})
        d = _make_container_dict(environment_file="/valid/path")
        _sanitize_db_row(d, Container)
        # Context var should NOT be updated when there are no fixes
        # (it retains the stale value — callers reset before calling)
        assert _current_db_fixes.get({}) == {"stale": "value"}


class TestValidateRowPersistence:
    """Integration tests for _validate_row / _validate_rows DB persistence."""

    @pytest.mark.asyncio
    async def test_validate_row_detects_fixes(self):
        """_validate_row should detect fixes via context var."""
        row = _make_container_dict(environment_file="bad-value")
        mock_db = MockAsyncSession()
        instance = await _validate_row(mock_db, Container, ContainerRow.__table__, row)
        assert instance is not None
        assert instance.environment_file == ""
        # Should have called execute + commit to persist the fix
        assert mock_db.execute_count == 1
        assert mock_db.commit_count == 1

    @pytest.mark.asyncio
    async def test_validate_row_no_fixes_no_db_write(self):
        """_validate_row should not write to DB when values are valid."""
        row = _make_container_dict(environment_file="/etc/myapp/env")
        mock_db = MockAsyncSession()
        instance = await _validate_row(mock_db, Container, ContainerRow.__table__, row)
        assert instance is not None
        assert instance.environment_file == "/etc/myapp/env"
        assert mock_db.execute_count == 0
        assert mock_db.commit_count == 0

    @pytest.mark.asyncio
    async def test_validate_row_none_returns_none(self):
        mock_db = MockAsyncSession()
        result = await _validate_row(mock_db, Container, ContainerRow.__table__, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_rows_persists_each_fix(self):
        rows = [
            _make_container_dict(environment_file="bad1"),
            _make_container_dict(environment_file="/valid/path"),
            _make_container_dict(environment_file="bad2"),
        ]
        mock_db = MockAsyncSession()
        results = await _validate_rows(mock_db, Container, ContainerRow.__table__, rows)
        assert len(results) == 3
        assert results[0].environment_file == ""
        assert results[1].environment_file == "/valid/path"
        assert results[2].environment_file == ""
        # Two bad rows → two DB writes
        assert mock_db.execute_count == 2
        assert mock_db.commit_count == 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockAsyncSession:
    """Minimal mock for AsyncSession that tracks execute/commit calls."""

    def __init__(self):
        self.execute_count = 0
        self.commit_count = 0

    async def execute(self, stmt):
        self.execute_count += 1

    async def commit(self):
        self.commit_count += 1


def _make_container_dict(**overrides) -> dict:
    """Build a minimal valid Container DB row dict with overrides."""
    row_id = str(uuid.uuid4())
    base = {
        "id": row_id,
        "compartment_id": "test",
        "name": "web",
        "image": "nginx",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "sort_order": 0,
        "environment": "{}",
        "ports": "[]",
        "volumes": "[]",
        "labels": "{}",
        "depends_on": "[]",
        "bind_mounts": "[]",
        "uid_map": "[]",
        "gid_map": "[]",
        "drop_caps": "[]",
        "add_caps": "[]",
        "mask_paths": "[]",
        "unmask_paths": "[]",
        "dns": "[]",
        "dns_search": "[]",
        "dns_option": "[]",
        "sysctl": "{}",
        "log_opt": "{}",
        "secrets": "[]",
        "devices": "[]",
        "network_aliases": "[]",
        "annotation": "[]",
        "expose_host_port": "[]",
        "tmpfs": "[]",
        "mount": "[]",
        "ulimits": "[]",
        "global_args": "[]",
        "group_add": "[]",
        "add_host": "[]",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Enforcement: no raw model_validate in compartment_manager.py
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# enforce_model_safety: branded-type default validation
# ---------------------------------------------------------------------------


class TestEnforceModelSafetyDefaults:
    """Ensure @enforce_model_safety validates branded-type field defaults."""

    def test_valid_default_passes(self):
        """A model with a valid .trusted() default should import fine."""
        from pydantic import BaseModel

        from quadletman.models.sanitized import SafeAbsPathOrEmpty, enforce_model_safety

        @enforce_model_safety
        class _Good(BaseModel):
            path: SafeAbsPathOrEmpty = SafeAbsPathOrEmpty.trusted("", "default")

        assert _Good().path == ""

    def test_valid_nonempty_default_passes(self):
        from pydantic import BaseModel

        from quadletman.models.sanitized import SafeAbsPathOrEmpty, enforce_model_safety

        @enforce_model_safety
        class _GoodNonEmpty(BaseModel):
            path: SafeAbsPathOrEmpty = SafeAbsPathOrEmpty.trusted("/etc/app/env", "default")

        assert _GoodNonEmpty().path == "/etc/app/env"

    def test_invalid_default_raises_at_import_time(self):
        from pydantic import BaseModel

        from quadletman.models.sanitized import SafeAbsPathOrEmpty, enforce_model_safety

        with pytest.raises(TypeError, match="fails SafeAbsPathOrEmpty.of"):

            @enforce_model_safety
            class _Bad(BaseModel):
                path: SafeAbsPathOrEmpty = SafeAbsPathOrEmpty.trusted(
                    "not-absolute", "deliberately bad"
                )

    def test_required_field_skipped(self):
        """Required fields (no default) should not be checked."""
        from pydantic import BaseModel

        from quadletman.models.sanitized import SafeAbsPathOrEmpty, enforce_model_safety

        @enforce_model_safety
        class _Required(BaseModel):
            path: SafeAbsPathOrEmpty  # no default — should not raise

        # Just verify the decorator didn't blow up
        assert hasattr(_Required, "_sanitized_enforce_model_safety")


_PROJECT_ROOT = Path(__file__).resolve().parent.parent / "quadletman"
# Matches Model.model_validate(dict(...)) — the pattern _validate_row replaces.
_RAW_VALIDATE_RE = re.compile(r"\.model_validate\(\s*dict\(")
# Files that legitimately contain model_validate(dict()) — the helpers themselves.
_ALLOWED_FILES = {
    str(_PROJECT_ROOT / "models" / "api" / "common.py"),
    str(_PROJECT_ROOT / "models" / "sanitized.py"),
}


class TestNoRawModelValidate:
    """Ensure service and router code uses _validate_row/_validate_rows exclusively.

    Raw .model_validate(dict(row)) bypasses the DB sanitization pipeline,
    meaning invalid legacy values would crash the app instead of being
    auto-corrected.  Only the helpers in models/api/common.py may call it.
    """

    def test_no_raw_model_validate_in_services(self):
        self._scan_directory(_PROJECT_ROOT / "services")

    def test_no_raw_model_validate_in_routers(self):
        self._scan_directory(_PROJECT_ROOT / "routers")

    @staticmethod
    def _scan_directory(directory: Path):
        violations = []
        for py_file in sorted(directory.rglob("*.py")):
            if str(py_file) in _ALLOWED_FILES:
                continue
            source = py_file.read_text()
            matches = _RAW_VALIDATE_RE.findall(source)
            if matches:
                violations.append(
                    f"  {py_file.relative_to(_PROJECT_ROOT)}: {len(matches)} occurrence(s)"
                )
        assert not violations, (
            "Found raw .model_validate(dict(...)) calls — use _validate_row / "
            "_validate_rows instead:\n" + "\n".join(violations)
        )

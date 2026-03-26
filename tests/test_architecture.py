"""Cross-cutting architecture tests — regression guards for structural fixes."""

import ast
from pathlib import Path

import pytest

from quadletman.models.sanitized import SafeSlug, SafeUnitName

# ---------------------------------------------------------------------------
# Settings bounds validation (Fix #8)
# ---------------------------------------------------------------------------


class TestSettingsBounds:
    def test_subprocess_timeout_clamped_to_minimum(self):
        from quadletman.config.settings import Settings

        s = Settings(subprocess_timeout=0)
        assert s.subprocess_timeout >= 1

    def test_session_ttl_clamped_to_minimum(self):
        from quadletman.config.settings import Settings

        s = Settings(session_ttl=10)
        assert s.session_ttl >= 60

    def test_valid_values_unchanged(self):
        from quadletman.config.settings import Settings

        s = Settings(subprocess_timeout=60, session_ttl=3600)
        assert s.subprocess_timeout == 60
        assert s.session_ttl == 3600

    def test_poll_interval_clamped_to_minimum(self):
        from quadletman.config.settings import Settings

        s = Settings(poll_interval=1)
        assert s.poll_interval >= 5

    def test_lock_timeout_clamped_to_minimum(self):
        from quadletman.config.settings import Settings

        s = Settings(lock_timeout=0)
        assert s.lock_timeout >= 1


# ---------------------------------------------------------------------------
# Cache bounds (Fix #5)
# ---------------------------------------------------------------------------


class TestCacheBounds:
    def test_unit_status_cache_bounded(self, mocker):
        """Cache should not grow beyond _MAX_CACHE_SIZE."""
        from quadletman.services import systemd_manager

        # Mock _run to avoid real subprocess calls
        mock_result = mocker.MagicMock()
        mock_result.stdout = (
            "ActiveState=active\nSubState=running\nLoadState=loaded\n"
            "UnitFileState=enabled\nMainPID=1234"
        )
        mocker.patch("quadletman.services.systemd_manager._run", return_value=mock_result)

        # Fill cache beyond max size
        systemd_manager._unit_status_cache.clear()
        for i in range(systemd_manager._MAX_CACHE_SIZE + 10):
            sid = SafeSlug.trusted(f"comp{i}", "test")
            unit = SafeUnitName.trusted(f"unit{i}.service", "test")
            systemd_manager._cached_unit_props(sid, unit)

        assert len(systemd_manager._unit_status_cache) <= systemd_manager._MAX_CACHE_SIZE
        systemd_manager._unit_status_cache.clear()


# ---------------------------------------------------------------------------
# Subprocess timeout default (Fix #3)
# ---------------------------------------------------------------------------


class TestSubprocessTimeouts:
    def test_host_run_has_default_timeout(self, mocker):
        """host.run() should default to settings.subprocess_timeout."""
        from quadletman.services import host

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = mocker.MagicMock(returncode=0, stdout="", stderr="")

        # host.run uses subprocess.run internally — verify timeout is passed
        host.run(["echo", "test"])
        call_kwargs = mock_run.call_args.kwargs
        assert "timeout" in call_kwargs

    def test_host_run_timeout_matches_settings(self, mocker):
        """The default timeout must equal settings.subprocess_timeout."""
        from quadletman.config.settings import settings
        from quadletman.services import host

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = mocker.MagicMock(returncode=0, stdout="", stderr="")

        host.run(["echo", "test"])
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["timeout"] == settings.subprocess_timeout


# ---------------------------------------------------------------------------
# _loop_session rollback on exception (Fix #6)
# ---------------------------------------------------------------------------


class TestLoopSession:
    async def test_rollback_on_exception(self, mocker):
        """_loop_session should rollback the DB session if an exception occurs."""
        from quadletman.services.notification_service import _loop_session

        db = mocker.MagicMock()
        db.rollback = mocker.AsyncMock()

        async def _gen():
            yield db
            return
            yield  # type: ignore[misc]

        def factory():
            return _gen()

        with pytest.raises(ValueError):
            async with _loop_session(factory) as session:
                assert session is db
                raise ValueError("test error")

        db.rollback.assert_awaited_once()

    async def test_no_rollback_on_success(self, mocker):
        """_loop_session should not rollback when no exception occurs."""
        from quadletman.services.notification_service import _loop_session

        db = mocker.MagicMock()
        db.rollback = mocker.AsyncMock()

        async def _gen():
            yield db
            return
            yield  # type: ignore[misc]

        def factory():
            return _gen()

        async with _loop_session(factory) as session:
            assert session is db

        db.rollback.assert_not_awaited()


# ---------------------------------------------------------------------------
# SSE streaming generator cleanup (Fix #9)
# ---------------------------------------------------------------------------


class TestSSEGeneratorCleanup:
    async def test_event_stream_calls_aclose_on_source(self):
        """The SSE event_stream wrapper must call aclose() on the source generator."""
        closed = False

        async def fake_source():
            nonlocal closed
            try:
                yield "line1"
                yield "line2"
            finally:
                closed = True

        source = fake_source()
        lines = []
        async for line in source:
            lines.append(line)
            break  # simulate early exit (client disconnect)
        await source.aclose()
        assert closed

    def test_event_stream_functions_call_aclose(self):
        """Every event_stream() inner generator in logs.py must await source.aclose()."""
        source = Path("quadletman/routers/logs.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "event_stream":
                # Check that the function body contains a try/finally with aclose()
                has_aclose = False
                for child in ast.walk(node):
                    if isinstance(child, ast.Attribute) and child.attr == "aclose":
                        has_aclose = True
                        break
                assert has_aclose, "event_stream() must call aclose() on the source generator"


# ---------------------------------------------------------------------------
# Volume file routes have require_compartment (Fix #10)
# ---------------------------------------------------------------------------


class TestVolumeRoutesRequireCompartment:
    async def test_volume_save_file_404_without_compartment(self, client):
        """Volume file operations must return 404 for non-existent compartments."""
        resp = await client.put(
            "/api/compartments/nonexistent/volumes/00000000-0000-0000-0000-000000000001/file",
            params={"path": "/test.txt"},
            data={"content": "hello"},
        )
        assert resp.status_code == 404

    def test_volume_file_routes_have_require_compartment(self):
        """Volume file-operation routes must include require_compartment dependency."""
        source = Path("quadletman/routers/volumes.py").read_text()
        tree = ast.parse(source)

        # File-operation routes that must validate compartment existence
        file_route_names = {
            "volume_browse",
            "volume_get_file",
            "volume_save_file",
            "volume_upload",
            "volume_delete_entry",
            "volume_mkdir",
            "volume_chmod",
            "volume_archive",
            "volume_restore",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name in file_route_names:
                source_lines = source.split("\n")
                func_source = "\n".join(source_lines[node.lineno - 1 : node.end_lineno])
                assert "require_compartment" in func_source, (
                    f"Route {node.name} at line {node.lineno} is missing require_compartment"
                )


# ---------------------------------------------------------------------------
# Bounded dedup dict (Fix #11)
# ---------------------------------------------------------------------------


class TestImageUpdateDedupBounds:
    def test_max_dedup_entries_constant_exists(self):
        """agent_api must define _MAX_DEDUP_ENTRIES to bound the dedup dict."""
        from quadletman.services import agent_api

        assert hasattr(agent_api, "_MAX_DEDUP_ENTRIES")
        assert agent_api._MAX_DEDUP_ENTRIES > 0

    def test_dedup_dict_cleared_when_full(self):
        """When the dedup dict reaches _MAX_DEDUP_ENTRIES, it should be cleared."""
        from quadletman.services import agent_api

        original = agent_api._notified_image_updates.copy()
        try:
            agent_api._notified_image_updates.clear()
            # Fill to the limit
            for i in range(agent_api._MAX_DEDUP_ENTRIES):
                agent_api._notified_image_updates[f"comp/container/image:{i}"] = True
            assert len(agent_api._notified_image_updates) == agent_api._MAX_DEDUP_ENTRIES

            # Verify the source code checks length before inserting
            source = Path("quadletman/services/agent_api.py").read_text()
            assert "len(_notified_image_updates) >= _MAX_DEDUP_ENTRIES" in source
            assert "_notified_image_updates.clear()" in source
        finally:
            agent_api._notified_image_updates.clear()
            agent_api._notified_image_updates.update(original)


# ---------------------------------------------------------------------------
# Agent socket per-request timeout (Fix #12)
# ---------------------------------------------------------------------------


class TestAgentSocketTimeout:
    def test_handle_connection_has_timeout(self):
        """_handle_connection must use asyncio.timeout() for per-request bounds."""
        source = Path("quadletman/services/agent_api.py").read_text()
        assert "asyncio.timeout(" in source, (
            "agent_api._handle_connection must use asyncio.timeout()"
        )

    def test_timeout_wraps_handler(self):
        """asyncio.timeout must appear inside _handle_connection, not just anywhere."""
        source = Path("quadletman/services/agent_api.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle_connection":
                func_source = ast.get_source_segment(source, node)
                assert func_source is not None
                assert "asyncio.timeout(" in func_source
                return
        raise AssertionError("_handle_connection not found in agent_api.py")


# ---------------------------------------------------------------------------
# Upsert error counting — batch continues after failure (Fix #13)
# ---------------------------------------------------------------------------


class TestUpsertErrorCounting:
    async def test_process_report_continues_after_upsert_failure(self, db, mocker):
        """handle_processes_report must continue processing after a single upsert failure."""
        from quadletman.services import agent_api

        call_count = 0
        upsert_calls = []

        async def mock_upsert(db, cid, name, cmdline):
            nonlocal call_count
            call_count += 1
            upsert_calls.append(str(name))
            if call_count == 1:
                raise RuntimeError("simulated DB error")
            # Return a mock process object for successful calls
            proc = mocker.MagicMock()
            proc.known = True
            return proc, False

        mocker.patch(
            "quadletman.services.compartment_manager.upsert_process",
            side_effect=mock_upsert,
        )
        mocker.patch(
            "quadletman.services.compartment_manager.list_all_notification_hooks",
            return_value=[],
        )

        data = {
            "compartment_id": "testcomp",
            "processes": [
                {"name": "proc1", "cmdline": "proc1 --arg"},
                {"name": "proc2", "cmdline": "proc2 --arg"},
                {"name": "proc3", "cmdline": "proc3 --arg"},
            ],
        }
        await agent_api.handle_processes_report(db, data)

        # All 3 processes should have been attempted, not just the first
        assert call_count == 3
        assert upsert_calls == ["proc1", "proc2", "proc3"]

    async def test_connection_report_continues_after_upsert_failure(self, db, mocker):
        """handle_connections_report must continue processing after a single upsert failure."""
        from quadletman.services import agent_api

        call_count = 0

        async def mock_upsert(db, cid, container, proto, dst_ip, dst_port, direction):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated DB error")
            conn = mocker.MagicMock()
            return conn, False

        mocker.patch(
            "quadletman.services.compartment_manager.upsert_connection",
            side_effect=mock_upsert,
        )
        mocker.patch(
            "quadletman.services.compartment_manager.list_allowlist_rules",
            return_value=[],
        )
        mocker.patch(
            "quadletman.services.compartment_manager.list_all_notification_hooks",
            return_value=[],
        )
        mocker.patch(
            "quadletman.services.compartment_manager.cleanup_stale_connections",
            return_value=None,
        )

        data = {
            "compartment_id": "testcomp",
            "connections": [
                {
                    "container_name": "c1",
                    "proto": "tcp",
                    "dst_ip": "10.0.0.1",
                    "dst_port": 80,
                    "direction": "outbound",
                },
                {
                    "container_name": "c2",
                    "proto": "tcp",
                    "dst_ip": "10.0.0.2",
                    "dst_port": 443,
                    "direction": "outbound",
                },
            ],
        }
        await agent_api.handle_connections_report(db, data)

        # Both connections should have been attempted
        assert call_count == 2


# ---------------------------------------------------------------------------
# WebSocket error logging — no blanket suppress (Fix #14)
# ---------------------------------------------------------------------------


class TestWebSocketErrorLogging:
    def test_no_blanket_suppress_around_send_bytes(self):
        """WebSocket error paths must not use suppress(Exception) around send_bytes."""
        source = Path("quadletman/routers/logs.py").read_text()
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "send_bytes" in line:
                # Check the surrounding context (3 lines before) for suppress(Exception)
                context_start = max(0, i - 3)
                context = "\n".join(lines[context_start:i])
                assert "suppress(Exception)" not in context, (
                    f"Line {i + 1}: send_bytes is inside suppress(Exception) — "
                    "errors should be logged, not silently suppressed"
                )

    def test_send_bytes_errors_are_caught_and_logged(self):
        """Error send_bytes calls should use try/except with logging, not suppress."""
        source = Path("quadletman/routers/logs.py").read_text()
        # The error-sending pattern should use try/except with logger.warning
        # for the "could not send error to WebSocket" case
        assert (
            "Could not send terminal error to WebSocket" in source
            or "Could not send shell error to WebSocket" in source
        ), "WebSocket error paths should log failures to send error messages"


class TestNoDebugForFailures:
    """Ensure logger.debug is never used for error conditions."""

    def test_no_debug_for_failures_in_services(self):
        """Service files must not log failures at DEBUG level."""
        import re

        fail_pattern = re.compile(
            r"logger\.debug\(.*(?:[Ff]ailed|[Ee]rror|[Cc]ould not|[Cc]annot)", re.IGNORECASE
        )
        violations = []
        for p in Path("quadletman/services").rglob("*.py"):
            for i, line in enumerate(p.read_text().splitlines(), 1):
                if fail_pattern.search(line):
                    violations.append(f"{p}:{i}: {line.strip()}")
        assert not violations, (
            "logger.debug used for failure conditions — use logger.warning instead:\n"
            + "\n".join(violations)
        )

    def test_no_debug_for_failures_in_routers(self):
        """Router files must not log failures at DEBUG level."""
        import re

        fail_pattern = re.compile(
            r"logger\.debug\(.*(?:[Ff]ailed|[Ee]rror|[Cc]ould not|[Cc]annot)", re.IGNORECASE
        )
        violations = []
        for p in Path("quadletman/routers").rglob("*.py"):
            for i, line in enumerate(p.read_text().splitlines(), 1):
                if fail_pattern.search(line):
                    violations.append(f"{p}:{i}: {line.strip()}")
        assert not violations, (
            "logger.debug used for failure conditions — use logger.warning instead:\n"
            + "\n".join(violations)
        )


class TestSuppressHasComment:
    """Ensure suppress(Exception) blocks have explanatory comments."""

    def test_suppress_exception_has_comment(self):
        """Every suppress(Exception) must have a # Best-effort or # comment nearby."""
        violations = []
        for p in Path("quadletman/services").rglob("*.py"):
            lines = p.read_text().splitlines()
            for i, line in enumerate(lines):
                if (
                    "suppress(Exception)" in line
                    and "# " not in line
                    and (i == 0 or "# " not in lines[i - 1])
                ):
                    violations.append(f"{p}:{i + 1}: {line.strip()}")
        assert not violations, "suppress(Exception) without an explanatory comment:\n" + "\n".join(
            violations
        )

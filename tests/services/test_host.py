"""Tests for quadletman/services/host.py — audit decorator and host wrappers."""

import asyncio
import logging

from quadletman.models import sanitized
from quadletman.models.sanitized import SafeAbsPath, SafeSlug, SafeUnitName
from quadletman.services import host

_p = lambda v: SafeAbsPath.trusted(v, "test fixture")  # noqa: E731

# ---------------------------------------------------------------------------
# @host.audit — sync functions
# ---------------------------------------------------------------------------


class TestAuditSync:
    def test_logs_call_at_info(self, caplog):
        @host.audit("TEST_ACTION", lambda sid, *_: str(sid))
        @sanitized.enforce
        def my_fn(service_id: SafeSlug) -> str:
            return "ok"

        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            my_fn(SafeSlug.of("mycomp"))

        assert any("CALL" in r.message and "TEST_ACTION" in r.message for r in caplog.records)

    def test_returns_function_result(self):
        @host.audit("TEST_ACTION")
        @sanitized.enforce
        def my_fn(x: int) -> int:
            return x * 2

        assert my_fn(21) == 42

    def test_no_params_debug_line_when_no_branded_args(self, caplog):
        @host.audit("TEST_ACTION")
        @sanitized.enforce
        def my_fn(x: int) -> None:
            pass

        with caplog.at_level(logging.DEBUG, logger="quadletman.host"):
            my_fn(42)

        assert not any("PARAMS" in r.message for r in caplog.records)

    def test_params_debug_line_for_validated_arg(self, caplog):
        @host.audit("TEST_ACTION", lambda sid, *_: str(sid))
        @sanitized.enforce
        def my_fn(service_id: SafeSlug) -> None:
            pass

        with caplog.at_level(logging.DEBUG, logger="quadletman.host"):
            my_fn(SafeSlug.of("mycomp"))

        params_records = [r for r in caplog.records if "PARAMS" in r.message]
        assert len(params_records) == 1
        assert "service_id=SafeSlug(validated:" in params_records[0].message

    def test_params_debug_line_for_trusted_arg_includes_reason(self, caplog):
        @host.audit("TEST_ACTION", lambda sid, *_: str(sid))
        @sanitized.enforce
        def my_fn(service_id: SafeSlug) -> None:
            pass

        with caplog.at_level(logging.DEBUG, logger="quadletman.host"):
            my_fn(SafeSlug.trusted("mycomp", "DB-sourced compartment_id"))

        params_records = [r for r in caplog.records if "PARAMS" in r.message]
        assert len(params_records) == 1
        assert "service_id=SafeSlug(trusted:DB-sourced compartment_id)" in params_records[0].message

    def test_params_debug_line_multiple_branded_args(self, caplog):
        @host.audit("TEST_ACTION", lambda sid, unit, *_: f"{sid}/{unit}")
        @sanitized.enforce
        def my_fn(service_id: SafeSlug, unit: SafeUnitName) -> None:
            pass

        with caplog.at_level(logging.DEBUG, logger="quadletman.host"):
            my_fn(
                SafeSlug.of("mycomp"),
                SafeUnitName.trusted("mycontainer.service", "internally constructed unit name"),
            )

        params_records = [r for r in caplog.records if "PARAMS" in r.message]
        assert len(params_records) == 1
        msg = params_records[0].message
        assert "service_id=SafeSlug(validated:" in msg
        assert "unit=SafeUnitName(trusted:internally constructed unit name)" in msg

    def test_params_not_emitted_at_info_level(self, caplog):
        @host.audit("TEST_ACTION", lambda sid, *_: str(sid))
        @sanitized.enforce
        def my_fn(service_id: SafeSlug) -> None:
            pass

        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            my_fn(SafeSlug.of("mycomp"))

        assert not any("PARAMS" in r.message for r in caplog.records)

    def test_static_target_string(self, caplog):
        @host.audit("TEST_ACTION", "fixed-target")
        @sanitized.enforce
        def my_fn() -> None:
            pass

        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            my_fn()

        assert any("fixed-target" in r.message for r in caplog.records)

    def test_none_target(self, caplog):
        @host.audit("TEST_ACTION")
        @sanitized.enforce
        def my_fn() -> None:
            pass

        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            my_fn()

        assert any("CALL" in r.message and "TEST_ACTION" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# @host.audit — async functions
# ---------------------------------------------------------------------------


class TestAuditAsync:
    def test_logs_call_at_info(self, caplog):
        @host.audit("ASYNC_ACTION", lambda sid, *_: str(sid))
        @sanitized.enforce
        async def my_fn(service_id: SafeSlug) -> str:
            return "ok"

        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            asyncio.get_event_loop().run_until_complete(my_fn(SafeSlug.of("mycomp")))

        assert any("CALL" in r.message and "ASYNC_ACTION" in r.message for r in caplog.records)

    def test_params_debug_line_for_trusted_arg(self, caplog):
        @host.audit("ASYNC_ACTION", lambda sid, *_: str(sid))
        @sanitized.enforce
        async def my_fn(service_id: SafeSlug) -> None:
            pass

        with caplog.at_level(logging.DEBUG, logger="quadletman.host"):
            asyncio.get_event_loop().run_until_complete(
                my_fn(SafeSlug.trusted("mycomp", "DB-sourced compartment_id"))
            )

        params_records = [r for r in caplog.records if "PARAMS" in r.message]
        assert len(params_records) == 1
        assert "trusted:DB-sourced compartment_id" in params_records[0].message

    def test_returns_coroutine_result(self):
        @host.audit("ASYNC_ACTION")
        @sanitized.enforce
        async def my_fn(x: int) -> int:
            return x + 1

        result = asyncio.get_event_loop().run_until_complete(my_fn(41))
        assert result == 42


# ---------------------------------------------------------------------------
# host filesystem wrappers
# ---------------------------------------------------------------------------


class TestHostWrappers:
    def test_makedirs(self, mocker, caplog, tmp_path):
        mock = mocker.patch("quadletman.services.host.os.makedirs")
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.makedirs(_p(str(tmp_path / "new")), exist_ok=True)
        mock.assert_called_once()
        assert any("MKDIR" in r.message for r in caplog.records)

    def test_unlink(self, mocker, caplog):
        mock = mocker.patch("quadletman.services.host.os.unlink")
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.unlink(_p("/tmp/somefile"))
        mock.assert_called_once_with("/tmp/somefile")
        assert any("UNLINK" in r.message for r in caplog.records)

    def test_symlink(self, mocker, caplog):
        mock = mocker.patch("quadletman.services.host.os.symlink")
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.symlink(_p("/dev/null"), _p("/tmp/mask"))
        mock.assert_called_once_with("/dev/null", "/tmp/mask")
        assert any("SYMLINK" in r.message for r in caplog.records)

    def test_chmod(self, mocker, caplog):
        mock = mocker.patch("quadletman.services.host.os.chmod")
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.chmod(_p("/tmp/f"), 0o600)
        mock.assert_called_once_with("/tmp/f", 0o600)
        assert any("CHMOD" in r.message for r in caplog.records)

    def test_chown(self, mocker, caplog):
        mock = mocker.patch("quadletman.services.host.os.chown")
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.chown(_p("/tmp/f"), 1000, 1000)
        mock.assert_called_once_with("/tmp/f", 1000, 1000)
        assert any("CHOWN" in r.message for r in caplog.records)

    def test_rename(self, mocker, caplog):
        mock = mocker.patch("quadletman.services.host.os.rename")
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.rename(_p("/tmp/a"), _p("/tmp/b"))
        mock.assert_called_once_with("/tmp/a", "/tmp/b")
        assert any("RENAME" in r.message for r in caplog.records)

    def test_rmtree(self, mocker, caplog):
        mock = mocker.patch("quadletman.services.host.shutil.rmtree")
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.rmtree(_p("/tmp/dir"))
        mock.assert_called_once()
        assert any("RMTREE" in r.message for r in caplog.records)

    def test_write_text(self, mocker, caplog, tmp_path):
        mocker.patch("quadletman.services.host.os.chown")
        mocker.patch("quadletman.services.host.os.chmod")
        path = str(tmp_path / "out.txt")
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.write_text(_p(path), "hello", 1000, 1000)
        assert (tmp_path / "out.txt").read_text() == "hello"
        assert any("WRITE" in r.message for r in caplog.records)

    def test_append_text(self, caplog, tmp_path):
        path = tmp_path / "log.txt"
        path.write_text("first\n")
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.append_text(_p(str(path)), "second\n")
        assert path.read_text() == "first\nsecond\n"
        assert any("APPEND" in r.message for r in caplog.records)

    def test_write_lines(self, caplog, tmp_path):
        path = tmp_path / "lines.txt"
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.write_lines(_p(str(path)), ["line1\n", "line2\n"])
        assert path.read_text() == "line1\nline2\n"
        assert any("WRITE" in r.message for r in caplog.records)

    def test_run_logs_cmd(self, mocker, caplog):
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.run(["echo", "hello"])
        assert any("CMD" in r.message and "echo" in r.message for r in caplog.records)

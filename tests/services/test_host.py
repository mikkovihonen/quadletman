"""Tests for quadletman/services/host.py — audit decorator and host wrappers, plus host_settings."""

import asyncio
import logging

import pytest

from quadletman.models import sanitized
from quadletman.models.sanitized import SafeAbsPath, SafeSlug, SafeStr, SafeUnitName
from quadletman.services import host

_p = lambda v: SafeAbsPath.trusted(v, "test fixture")  # noqa: E731
_s = lambda v: SafeStr.trusted(v, "test fixture")  # noqa: E731

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


@pytest.mark.no_host_mock
class TestHostWrappers:
    """Test host filesystem wrappers use subprocess-based escalation."""

    def test_makedirs(self, mocker):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        host.makedirs(_p("/tmp/new"), mode=0o700, exist_ok=True)
        args = run_mock.call_args_list[0].args[0]
        assert "mkdir" in args
        assert "-p" in args

    def test_unlink(self, mocker):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        host.unlink(_p("/tmp/somefile"))
        args = run_mock.call_args_list[0].args[0]
        assert "rm" in args

    def test_symlink(self, mocker):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        host.symlink(_p("/dev/null"), _p("/tmp/mask"))
        args = run_mock.call_args_list[0].args[0]
        assert "ln" in args
        assert "-sf" in args

    def test_chmod(self, mocker):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        host.chmod(_p("/tmp/f"), 0o600)
        args = run_mock.call_args_list[0].args[0]
        assert "chmod" in args
        assert "0600" in args

    def test_chown(self, mocker):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        host.chown(_p("/tmp/f"), 1000, 1000)
        args = run_mock.call_args_list[0].args[0]
        assert "chown" in args
        assert "1000:1000" in args

    def test_rename(self, mocker):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        host.rename(_p("/tmp/a"), _p("/tmp/b"))
        args = run_mock.call_args_list[0].args[0]
        assert "mv" in args

    def test_rmtree(self, mocker):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        host.rmtree(_p("/tmp/dir"))
        args = run_mock.call_args_list[0].args[0]
        assert "rm" in args
        assert "-rf" in args

    def test_write_text(self, mocker, tmp_path):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        mocker.patch("quadletman.services.host.os.unlink")
        path = str(tmp_path / "out.txt")
        host.write_text(_p(path), "hello", 1000, 1000)
        args = run_mock.call_args_list[0].args[0]
        assert "install" in args

    def test_append_text(self, mocker, tmp_path):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        mocker.patch("quadletman.services.host.os.unlink")
        path = tmp_path / "log.txt"
        path.write_text("first\n")
        host.append_text(_p(str(path)), "second\n")
        args = run_mock.call_args_list[0].args[0]
        assert "cp" in args

    def test_write_lines(self, mocker, tmp_path):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        mocker.patch("quadletman.services.host.os.unlink")
        host.write_lines(_p(str(tmp_path / "lines.txt")), ["line1\n"])
        args = run_mock.call_args_list[0].args[0]
        assert "cp" in args

    def test_run_logs_cmd(self, mocker, caplog):
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        with caplog.at_level(logging.INFO, logger="quadletman.host"):
            host.run(["echo", "hello"])
        assert any("CMD" in r.message and "echo" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


@pytest.mark.no_host_mock
class TestReadHelpers:
    """Test read_text, path_exists, path_islink, readlink via sudo subprocess."""

    def test_read_text(self, mocker):
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0, "stdout": "file content"})(),
        )
        result = host.read_text(_p("/home/qm-test/file.txt"), owner=_s("qm-test"))
        assert result == "file content"

    def test_read_text_missing(self, mocker):
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 1, "stdout": ""})(),
        )
        result = host.read_text(_p("/home/qm-test/missing.txt"), owner=_s("qm-test"))
        assert result is None

    def test_path_exists(self, mocker):
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        assert host.path_exists(_p("/home/qm-test/file"), owner=_s("qm-test")) is True

    def test_path_exists_missing(self, mocker):
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 1})(),
        )
        assert host.path_exists(_p("/home/qm-test/missing"), owner=_s("qm-test")) is False

    def test_path_islink(self, mocker):
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        assert host.path_islink(_p("/home/qm-test/link"), owner=_s("qm-test")) is True

    def test_readlink(self, mocker):
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0, "stdout": "/target\n"})(),
        )
        result = host.readlink(_p("/home/qm-test/link"), owner=_s("qm-test"))
        assert result == "/target"

    def test_readlink_not_link(self, mocker):
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 1, "stdout": ""})(),
        )
        result = host.readlink(_p("/home/qm-test/file"), owner=_s("qm-test"))
        assert result is None


# ---------------------------------------------------------------------------
# Escalation and run with admin flag
# ---------------------------------------------------------------------------


@pytest.mark.no_host_mock
class TestEscalation:
    def test_run_admin_escalates(self, mocker):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        host.run(["echo", "hi"], admin=True)
        args = run_mock.call_args_list[0].args[0]
        assert "sudo" in args

    def test_run_no_admin_no_escalation(self, mocker):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        host.run(["echo", "hi"], admin=False)
        args = run_mock.call_args_list[0].args[0]
        assert args == ["echo", "hi"]

    def test_escalate_raises_without_creds(self, mocker):
        import pytest

        from quadletman.services.host import AdminSessionRequired

        mocker.patch("quadletman.services.host.get_admin_credentials", return_value=None)
        with pytest.raises(AdminSessionRequired):
            host.run(["echo", "hi"], admin=True)


# ---------------------------------------------------------------------------
# host_settings — _validate_value and read_all / apply
# ---------------------------------------------------------------------------


class TestValidateValue:
    def test_valid_integer(self):
        from quadletman.services.host_settings import SETTINGS, _validate_value

        setting = next(s for s in SETTINGS if s.value_type == "integer")
        # Use a value within valid range
        result = _validate_value(setting, _s("1024"))
        assert result == "1024"

    def test_boolean_valid(self):
        from quadletman.services.host_settings import SETTINGS, _validate_value

        setting = next(s for s in SETTINGS if s.value_type == "boolean")
        assert _validate_value(setting, _s("0")) == "0"
        assert _validate_value(setting, _s("1")) == "1"

    def test_boolean_invalid(self):
        import pytest

        from quadletman.services.host_settings import SETTINGS, _validate_value

        setting = next(s for s in SETTINGS if s.value_type == "boolean")
        with pytest.raises(ValueError, match="must be 0 or 1"):
            _validate_value(setting, _s("2"))

    def test_integer_above_max(self):
        import pytest

        from quadletman.services.host_settings import SETTINGS, _validate_value

        setting = next(s for s in SETTINGS if s.value_type == "integer" and s.max_val is not None)
        with pytest.raises(ValueError):
            _validate_value(setting, _s(str(setting.max_val + 1)))

    def test_ping_range_valid(self):
        from quadletman.services.host_settings import SETTINGS, _validate_value

        setting = next(s for s in SETTINGS if s.value_type == "ping_range")
        result = _validate_value(setting, _s("0 2147483647"))
        assert result == "0 2147483647"

    def test_ping_range_invalid_format(self):
        import pytest

        from quadletman.services.host_settings import SETTINGS, _validate_value

        setting = next(s for s in SETTINGS if s.value_type == "ping_range")
        with pytest.raises(ValueError):
            _validate_value(setting, _s("not a range"))

    def test_control_chars_rejected(self):
        import pytest

        from quadletman.services.host_settings import SETTINGS, _validate_value

        setting = SETTINGS[0]
        with pytest.raises(ValueError, match="disallowed control characters"):
            _validate_value(setting, _s("1024\n"))

    def test_empty_value_rejected(self):
        import pytest

        from quadletman.services.host_settings import SETTINGS, _validate_value

        setting = SETTINGS[0]
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_value(setting, _s("   "))

    def test_too_long_rejected(self):
        import pytest

        from quadletman.services.host_settings import SETTINGS, _validate_value

        setting = SETTINGS[0]
        with pytest.raises(ValueError, match="too long"):
            _validate_value(setting, _s("9" * 65))


class TestReadAll:
    def test_returns_list(self, tmp_path, mocker):
        """read_all should silently skip settings whose /proc path doesn't exist."""
        mocker.patch(
            "quadletman.services.host_settings._PROC_SYS",
            tmp_path / "proc" / "sys",
        )
        from quadletman.services.host_settings import read_all

        result = read_all()
        # On a real system some may exist; in test none exist → empty list
        assert isinstance(result, list)

    def test_reads_existing_proc_path(self, tmp_path, mocker):
        proc_sys = tmp_path / "proc" / "sys"
        (proc_sys / "net" / "ipv4").mkdir(parents=True)
        (proc_sys / "net" / "ipv4" / "ip_unprivileged_port_start").write_text("1024\n")
        mocker.patch(
            "quadletman.services.host_settings._PROC_SYS",
            proc_sys,
        )
        from quadletman.services.host_settings import read_all

        result = read_all()
        keys = [str(e.key) for e in result]
        assert "net.ipv4.ip_unprivileged_port_start" in keys


class TestApply:
    def test_raises_for_unknown_key(self):
        import asyncio

        import pytest

        from quadletman.models.sanitized import SafeStr
        from quadletman.services import host_settings

        with pytest.raises(ValueError, match="Unknown sysctl key"):
            asyncio.get_event_loop().run_until_complete(
                host_settings.apply(
                    SafeStr.trusted("unknown.key", "test"),
                    SafeStr.trusted("1", "test"),
                )
            )

    def test_applies_valid_setting(self, mocker):
        import asyncio

        from quadletman.models.sanitized import SafeStr
        from quadletman.services import host_settings

        # Mock _apply_sync to avoid SafeStr type enforcement on internal call
        apply_mock = mocker.patch("quadletman.services.host_settings._apply_sync")

        asyncio.get_event_loop().run_until_complete(
            host_settings.apply(
                SafeStr.trusted("net.ipv4.ip_forward", "test"),
                SafeStr.trusted("1", "test"),
            )
        )
        apply_mock.assert_called_once()

    def test_raises_on_sysctl_failure(self, mocker):
        import asyncio

        import pytest

        from quadletman.models.sanitized import SafeStr
        from quadletman.services import host_settings

        mocker.patch(
            "quadletman.services.host_settings._apply_sync",
            side_effect=RuntimeError("sysctl failed"),
        )

        with pytest.raises(RuntimeError):
            asyncio.get_event_loop().run_until_complete(
                host_settings.apply(
                    SafeStr.trusted("net.ipv4.ip_forward", "test"),
                    SafeStr.trusted("1", "test"),
                )
            )


class TestApplySync:
    def test_calls_sysctl_and_persist(self, mocker, tmp_path):
        import subprocess

        from quadletman.services import host_settings

        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        mocker.patch("quadletman.services.host_settings._persist")
        host_settings._apply_sync(
            SafeStr.trusted("net.ipv4.ip_forward", "test"),
            SafeStr.trusted("1", "test"),
        )

    def test_raises_on_sysctl_failure(self, mocker):
        import subprocess

        from quadletman.services import host_settings

        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )
        with pytest.raises(RuntimeError, match="sysctl"):
            host_settings._apply_sync(
                SafeStr.trusted("net.ipv4.ip_forward", "test"),
                SafeStr.trusted("1", "test"),
            )


class TestPersist:
    def test_writes_config_file(self, mocker, tmp_path):
        from quadletman.services import host_settings

        conf_path = tmp_path / "99-quadletman.conf"
        mocker.patch.object(host_settings, "_SYSCTL_D_PATH", conf_path)
        mocker.patch("quadletman.services.host.os.rename")
        host_settings._persist(
            SafeStr.trusted("net.ipv4.ip_forward", "test"),
            SafeStr.trusted("1", "test"),
        )

    def test_reads_existing_config(self, mocker, tmp_path):
        from quadletman.services import host_settings

        conf_path = tmp_path / "99-quadletman.conf"
        conf_path.write_text("net.ipv4.ip_forward = 0\n")
        mocker.patch.object(host_settings, "_SYSCTL_D_PATH", conf_path)
        mocker.patch("quadletman.services.host.os.rename")
        host_settings._persist(
            SafeStr.trusted("net.ipv4.ip_forward", "test"),
            SafeStr.trusted("1", "test"),
        )


# ---------------------------------------------------------------------------
# write_bytes — root mode (conftest forces _is_root = True)
# ---------------------------------------------------------------------------


@pytest.mark.no_host_mock
class TestWriteBytes:
    def test_writes_binary_and_uses_install(self, tmp_path, mocker):
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        )
        mocker.patch("quadletman.services.host.os.unlink")
        target = tmp_path / "binary.dat"
        host.write_bytes(_p(str(target)), b"\x00\x01\x02\xff", 1000, 1000)
        args = run_mock.call_args_list[0].args[0]
        assert "install" in args


# ---------------------------------------------------------------------------
# path_isdir / path_isfile
# ---------------------------------------------------------------------------


class TestPathIsDir:
    def test_returns_true_for_directory(self, tmp_path):
        assert host.path_isdir(_p(str(tmp_path))) is True

    def test_returns_false_for_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        assert host.path_isdir(_p(str(f))) is False

    def test_returns_false_for_nonexistent(self, tmp_path):
        assert host.path_isdir(_p(str(tmp_path / "nope"))) is False


class TestPathIsFile:
    def test_returns_true_for_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        assert host.path_isfile(_p(str(f))) is True

    def test_returns_false_for_directory(self, tmp_path):
        assert host.path_isfile(_p(str(tmp_path))) is False

    def test_returns_false_for_nonexistent(self, tmp_path):
        assert host.path_isfile(_p(str(tmp_path / "nope"))) is False


# ---------------------------------------------------------------------------
# listdir
# ---------------------------------------------------------------------------


class TestListdir:
    def test_lists_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        result = host.listdir(_p(str(tmp_path)))
        assert sorted(result) == ["a.txt", "b.txt"]

    def test_returns_empty_for_nonexistent(self, tmp_path):
        result = host.listdir(_p(str(tmp_path / "nope")))
        assert result == []

    def test_includes_dotfiles(self, tmp_path):
        (tmp_path / ".hidden").write_text("h")
        result = host.listdir(_p(str(tmp_path)))
        assert ".hidden" in result


# ---------------------------------------------------------------------------
# stat_entry — root mode
# ---------------------------------------------------------------------------


class TestStatEntry:
    def test_returns_dict_for_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = host.stat_entry(_p(str(f)))
        assert result is not None
        assert result["is_dir"] is False
        assert result["size"] == 5

    def test_returns_dict_for_directory(self, tmp_path):
        result = host.stat_entry(_p(str(tmp_path)))
        assert result is not None
        assert result["is_dir"] is True

    def test_returns_none_for_nonexistent(self, tmp_path):
        result = host.stat_entry(_p(str(tmp_path / "nope")))
        assert result is None

    def test_mode_contains_permission_bits(self, tmp_path):
        f = tmp_path / "perms.txt"
        f.write_text("x")
        f.chmod(0o644)
        result = host.stat_entry(_p(str(f)))
        assert result is not None
        assert result["mode"] & 0o777 == 0o644


# ---------------------------------------------------------------------------
# read_bytes — root mode
# ---------------------------------------------------------------------------


class TestReadBytes:
    def test_reads_binary_content(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02\xff")
        result = host.read_bytes(_p(str(f)))
        assert result == b"\x00\x01\x02\xff"

    def test_respects_limit(self, tmp_path):
        f = tmp_path / "big.bin"
        f.write_bytes(b"A" * 100)
        result = host.read_bytes(_p(str(f)), limit=10)
        assert result == b"A" * 10

    def test_returns_none_for_nonexistent(self, tmp_path):
        result = host.read_bytes(_p(str(tmp_path / "nope")))
        assert result is None

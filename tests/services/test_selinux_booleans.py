"""Tests for quadletman/services/selinux_booleans.py."""

import subprocess

import pytest

from quadletman.models.sanitized import SafeStr
from quadletman.services import selinux_booleans

_s = lambda v: SafeStr.trusted(v, "test fixture")  # noqa: E731


class TestReadAll:
    def test_returns_none_when_selinux_inactive(self, mocker):
        mocker.patch("quadletman.services.selinux_booleans.is_selinux_active", return_value=False)
        run_mock = mocker.patch("quadletman.services.selinux_booleans.subprocess.run")
        result = selinux_booleans._read_all_sync()
        assert result is None
        run_mock.assert_not_called()

    def test_returns_entries_for_existing_booleans(self, mocker):
        mocker.patch("quadletman.services.selinux_booleans.is_selinux_active", return_value=True)

        def fake_run(cmd, **kwargs):
            name = cmd[1]
            if name == "virt_use_nfs":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="virt_use_nfs --> on\n", stderr=""
                )
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error")

        mocker.patch("quadletman.services.selinux_booleans.subprocess.run", side_effect=fake_run)
        result = selinux_booleans._read_all_sync()
        assert result is not None
        names = [e.name for e in result]
        assert "virt_use_nfs" in names
        entry = next(e for e in result if e.name == "virt_use_nfs")
        assert entry.enabled is True

    def test_skips_boolean_when_getsebool_file_not_found(self, mocker):
        mocker.patch("quadletman.services.selinux_booleans.is_selinux_active", return_value=True)
        mocker.patch(
            "quadletman.services.selinux_booleans.subprocess.run",
            side_effect=FileNotFoundError,
        )
        result = selinux_booleans._read_all_sync()
        assert result == []

    def test_returns_empty_list_when_no_booleans_exist(self, mocker):
        mocker.patch("quadletman.services.selinux_booleans.is_selinux_active", return_value=True)
        mocker.patch(
            "quadletman.services.selinux_booleans.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )
        result = selinux_booleans._read_all_sync()
        assert result == []

    def test_skips_boolean_when_getsebool_returns_empty_stdout(self, mocker):
        mocker.patch("quadletman.services.selinux_booleans.is_selinux_active", return_value=True)

        def fake_run(cmd, **kwargs):
            name = cmd[1]
            if name == "virt_use_nfs":
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        mocker.patch("quadletman.services.selinux_booleans.subprocess.run", side_effect=fake_run)
        result = selinux_booleans._read_all_sync()
        assert result is not None
        assert not any(e.name == "virt_use_nfs" for e in result)

    def test_parses_off_value_correctly(self, mocker):
        mocker.patch("quadletman.services.selinux_booleans.is_selinux_active", return_value=True)

        def fake_run(cmd, **kwargs):
            name = cmd[1]
            if name == "virt_use_nfs":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="virt_use_nfs --> off\n", stderr=""
                )
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        mocker.patch("quadletman.services.selinux_booleans.subprocess.run", side_effect=fake_run)
        result = selinux_booleans._read_all_sync()
        assert result is not None
        entry = next((e for e in result if e.name == "virt_use_nfs"), None)
        assert entry is not None
        assert entry.enabled is False


class TestSetBoolean:
    def test_raises_value_error_for_unknown_name(self):
        with pytest.raises(ValueError, match="Unknown SELinux boolean"):
            selinux_booleans._set_boolean_sync(_s("not_a_real_boolean"), True)

    def test_calls_setsebool_with_persistent_flag(self, mocker):
        mock_run = mocker.patch(
            "quadletman.services.selinux_booleans.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        selinux_booleans._set_boolean_sync(_s("virt_use_nfs"), True)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["setsebool", "-P", "virt_use_nfs", "on"]

    def test_off_value_sends_off_string(self, mocker):
        mock_run = mocker.patch(
            "quadletman.services.selinux_booleans.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        selinux_booleans._set_boolean_sync(_s("virt_use_nfs"), False)
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "off"

    def test_raises_runtime_error_on_failure(self, mocker):
        mocker.patch(
            "quadletman.services.selinux_booleans.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="permission denied"),
        )
        with pytest.raises(RuntimeError, match="setsebool -P virt_use_nfs failed"):
            selinux_booleans._set_boolean_sync(_s("virt_use_nfs"), True)

    def test_raises_runtime_error_when_setsebool_not_found(self, mocker):
        mocker.patch(
            "quadletman.services.selinux_booleans.subprocess.run",
            side_effect=FileNotFoundError,
        )
        with pytest.raises(RuntimeError, match="setsebool not found"):
            selinux_booleans._set_boolean_sync(_s("virt_use_nfs"), True)


class TestAsyncWrappers:
    def test_read_all_async_delegates_to_sync(self, mocker):
        mock_sync = mocker.patch(
            "quadletman.services.selinux_booleans._read_all_sync",
            return_value=[],
        )
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(selinux_booleans.read_all())
        assert result == []
        mock_sync.assert_called_once()

    def test_set_boolean_async_delegates_to_sync(self, mocker):
        mock_sync = mocker.patch(
            "quadletman.services.selinux_booleans._set_boolean_sync",
        )
        import asyncio

        asyncio.get_event_loop().run_until_complete(
            selinux_booleans.set_boolean(_s("virt_use_nfs"), True)
        )
        mock_sync.assert_called_once_with(_s("virt_use_nfs"), True)

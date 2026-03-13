"""Tests for quadletman/services/systemd_manager.py — command construction and execution."""

import subprocess

import pytest

from quadletman.services import systemd_manager


@pytest.fixture
def mock_user(mocker):
    """Stub out pwd lookups so _base_cmd works without real system users."""
    mocker.patch(
        "quadletman.services.systemd_manager.get_uid",
        return_value=1001,
    )
    mocker.patch(
        "quadletman.services.user_manager._username",
        return_value="qm-testsvc",
    )
    mocker.patch(
        "quadletman.services.systemd_manager._username",
        return_value="qm-testsvc",
    )


class TestBaseCmd:
    def test_contains_sudo_and_username(self, mocker):
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1234)
        mocker.patch("quadletman.services.systemd_manager._username", return_value="qm-mysvc")
        cmd = systemd_manager._base_cmd("mysvc")
        assert cmd[0] == "sudo"
        assert "qm-mysvc" in cmd
        assert any("XDG_RUNTIME_DIR=/run/user/1234" in part for part in cmd)
        assert any("DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1234/bus" in part for part in cmd)


class TestDaemonReload:
    def test_calls_daemon_reload(self, mocker, mock_user):
        run_mock = mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        systemd_manager.daemon_reload("testsvc")
        args = run_mock.call_args.args[0]
        assert "daemon-reload" in args

    def test_raises_on_nonzero_returncode(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="failure"),
        )
        with pytest.raises(RuntimeError, match="daemon-reload failed"):
            systemd_manager.daemon_reload("testsvc")


class TestStartUnit:
    def test_calls_reset_failed_then_start(self, mocker, mock_user):
        run_mock = mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        mocker.patch(
            "quadletman.services.systemd_manager.get_journal_lines",
            return_value="",
        )
        systemd_manager.start_unit("testsvc", "mycontainer.service")
        all_args = [c.args[0] for c in run_mock.call_args_list]
        assert any("reset-failed" in a for a in all_args)
        assert any("start" in a for a in all_args)

    def test_raises_on_start_failure(self, mocker, mock_user):
        call_count = 0

        def side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            # reset-failed succeeds, start fails
            returncode = 0 if call_count == 1 else 1
            return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="unit error")

        mocker.patch("quadletman.services.systemd_manager.subprocess.run", side_effect=side_effect)
        mocker.patch(
            "quadletman.services.systemd_manager.get_journal_lines",
            return_value="journal output",
        )
        with pytest.raises(RuntimeError):
            systemd_manager.start_unit("testsvc", "mycontainer.service")


class TestStopUnit:
    def test_calls_stop(self, mocker, mock_user):
        run_mock = mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        systemd_manager.stop_unit("testsvc", "mycontainer.service")
        all_args = [c.args[0] for c in run_mock.call_args_list]
        assert any("stop" in a for a in all_args)

    def test_raises_on_failure(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )
        with pytest.raises(RuntimeError, match="Failed to stop"):
            systemd_manager.stop_unit("testsvc", "mycontainer.service")


class TestGetUnitStatus:
    def test_parses_properties(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout="ActiveState=active\nSubState=running\nLoadState=loaded\n",
                stderr="",
            ),
        )
        props = systemd_manager.get_unit_status("testsvc", "mycontainer.service")
        assert props["ActiveState"] == "active"
        assert props["SubState"] == "running"

    def test_returns_empty_dict_on_no_output(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        props = systemd_manager.get_unit_status("testsvc", "mycontainer.service")
        assert props == {}


class TestEnableDisableUnit:
    def test_disable_creates_symlink(self, mocker, mock_user, tmp_path):
        home = tmp_path / "qm-testsvc"
        systemd_dir = home / ".config" / "systemd" / "user"
        systemd_dir.mkdir(parents=True)
        mocker.patch(
            "quadletman.services.systemd_manager.get_home",
            return_value=str(home),
        )
        systemd_manager.disable_unit("testsvc", "mycontainer")
        mask = systemd_dir / "mycontainer.service"
        assert mask.is_symlink()
        import os

        assert os.readlink(mask) == "/dev/null"

    def test_enable_removes_mask_symlink(self, mocker, mock_user, tmp_path):
        import os

        home = tmp_path / "qm-testsvc"
        systemd_dir = home / ".config" / "systemd" / "user"
        systemd_dir.mkdir(parents=True)
        mask = systemd_dir / "mycontainer.service"
        os.symlink("/dev/null", mask)
        mocker.patch(
            "quadletman.services.systemd_manager.get_home",
            return_value=str(home),
        )
        systemd_manager.enable_unit("testsvc", "mycontainer")
        assert not mask.exists()

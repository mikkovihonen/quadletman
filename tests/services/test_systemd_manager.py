"""Tests for quadletman/services/systemd_manager.py — command construction and execution."""

import subprocess

import pytest

from quadletman.sanitized import SafeSlug, SafeUnitName
from quadletman.services import systemd_manager

# Convenience helpers so test literals read naturally
_sid = lambda v: SafeSlug.trusted(v, "test fixture")  # noqa: E731
_unit = lambda v: SafeUnitName.trusted(v, "test fixture")  # noqa: E731


@pytest.fixture
def mock_user(mocker):
    """Stub out pwd lookups so _base_cmd works without real system users."""
    mocker.patch(
        "quadletman.services.systemd_manager.get_uid",
        return_value=1001,
    )
    mocker.patch(
        "quadletman.services.user_manager._username",
        return_value="qm-testcomp",
    )
    mocker.patch(
        "quadletman.services.systemd_manager._username",
        return_value="qm-testcomp",
    )


class TestExecPtyCmd:
    def test_basic_command_structure(self, mocker):
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1234)
        mocker.patch("quadletman.services.systemd_manager._username", return_value="qm-mycomp")
        cmd = systemd_manager.exec_pty_cmd("mycomp", "mycontainer")
        assert "sudo" in cmd
        assert "podman" in cmd
        assert "exec" in cmd
        assert "-it" in cmd
        assert "mycontainer" in cmd
        assert "/bin/sh" in cmd
        assert "--user" not in cmd

    def test_includes_user_flag_when_provided(self, mocker):
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1234)
        mocker.patch("quadletman.services.systemd_manager._username", return_value="qm-mycomp")
        cmd = systemd_manager.exec_pty_cmd("mycomp", "mycontainer", exec_user="1000")
        user_idx = cmd.index("--user")
        assert cmd[user_idx + 1] == "1000"

    def test_root_user_flag(self, mocker):
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1234)
        mocker.patch("quadletman.services.systemd_manager._username", return_value="qm-mycomp")
        cmd = systemd_manager.exec_pty_cmd("mycomp", "mycontainer", exec_user="root")
        user_idx = cmd.index("--user")
        assert cmd[user_idx + 1] == "root"

    def test_container_name_before_shell(self, mocker):
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1234)
        mocker.patch("quadletman.services.systemd_manager._username", return_value="qm-mycomp")
        cmd = systemd_manager.exec_pty_cmd("mycomp", "mycontainer")
        assert cmd[-1] == "/bin/sh"
        assert cmd[-2] == "mycontainer"


class TestBaseCmd:
    def test_contains_sudo_and_username(self, mocker):
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1234)
        mocker.patch("quadletman.services.systemd_manager._username", return_value="qm-mycomp")
        cmd = systemd_manager._base_cmd("mycomp")
        assert cmd[0] == "sudo"
        assert "qm-mycomp" in cmd
        assert any("XDG_RUNTIME_DIR=/run/user/1234" in part for part in cmd)
        assert any("DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1234/bus" in part for part in cmd)


class TestDaemonReload:
    def test_calls_daemon_reload(self, mocker, mock_user):
        run_mock = mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        systemd_manager.daemon_reload(_sid("testcomp"))
        args = run_mock.call_args.args[0]
        assert "daemon-reload" in args

    def test_raises_on_nonzero_returncode(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="failure"),
        )
        with pytest.raises(RuntimeError, match="daemon-reload failed"):
            systemd_manager.daemon_reload(_sid("testcomp"))


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
        systemd_manager.start_unit(_sid("testcomp"), _unit("mycontainer.service"))
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
            systemd_manager.start_unit(_sid("testcomp"), _unit("mycontainer.service"))


class TestStopUnit:
    def test_calls_stop(self, mocker, mock_user):
        run_mock = mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        systemd_manager.stop_unit(_sid("testcomp"), _unit("mycontainer.service"))
        all_args = [c.args[0] for c in run_mock.call_args_list]
        assert any("stop" in a for a in all_args)

    def test_raises_on_failure(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )
        with pytest.raises(RuntimeError, match="Failed to stop"):
            systemd_manager.stop_unit(_sid("testcomp"), _unit("mycontainer.service"))


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
        props = systemd_manager.get_unit_status("testcomp", "mycontainer.service")
        assert props["ActiveState"] == "active"
        assert props["SubState"] == "running"

    def test_returns_empty_dict_on_no_output(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        props = systemd_manager.get_unit_status("testcomp", "mycontainer.service")
        assert props == {}


class TestRestartUnit:
    def test_calls_restart(self, mocker, mock_user):
        run_mock = mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        systemd_manager.restart_unit(_sid("testcomp"), _unit("mycontainer.service"))
        all_args = [c.args[0] for c in run_mock.call_args_list]
        assert any("restart" in a for a in all_args)

    def test_raises_on_failure(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )
        with pytest.raises(RuntimeError, match="Failed to restart"):
            systemd_manager.restart_unit(_sid("testcomp"), _unit("mycontainer.service"))


class TestListImages:
    def test_returns_sorted_images(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="nginx:latest\nalpine:3\nnginx:stable\n", stderr=""
            ),
        )
        images = systemd_manager.list_images("testcomp")
        assert images == sorted({"nginx:latest", "alpine:3", "nginx:stable"})

    def test_excludes_none_tags(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="nginx:latest\n<none>:<none>\n", stderr=""
            ),
        )
        images = systemd_manager.list_images("testcomp")
        assert all("<none>" not in img for img in images)

    def test_returns_empty_on_error(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )
        assert systemd_manager.list_images("testcomp") == []


class TestGetJournalLines:
    def test_returns_stdout(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="log line 1\nlog line 2\n", stderr=""
            ),
        )
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1001)
        result = systemd_manager.get_journal_lines("testcomp", "mycontainer.service")
        assert "log line 1" in result

    def test_raises_on_unsafe_unit(self, mocker, mock_user):
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1001)
        with pytest.raises(ValueError, match="Unsafe unit name"):
            systemd_manager.get_journal_lines("testcomp", "../etc/passwd")


class TestGetServiceStatus:
    def test_returns_status_list(self, mocker, mock_user, tmp_path):
        mocker.patch(
            "quadletman.services.systemd_manager._cached_unit_props",
            return_value={"ActiveState": "active", "SubState": "running", "LoadState": "loaded"},
        )
        mocker.patch(
            "quadletman.services.systemd_manager._cached_unit_text",
            return_value="● mycontainer.service",
        )
        mocker.patch(
            "quadletman.services.systemd_manager.get_home",
            return_value=str(tmp_path),
        )
        result = systemd_manager.get_service_status("testcomp", ["mycontainer"])
        assert len(result) == 1
        assert result[0]["active_state"] == "active"
        assert result[0]["container"] == "mycontainer"

    def test_empty_container_list(self, mocker, mock_user):
        result = systemd_manager.get_service_status("testcomp", [])
        assert result == []


class TestTimerStatus:
    def test_parses_timer_properties(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout="ActiveState=active\nSubState=waiting\nLastTriggerUSec=123\n"
                "NextElapseUSecRealtime=456\nResult=success\n",
                stderr="",
            ),
        )
        result = systemd_manager.get_timer_status("testcomp", "mytimer")
        assert result["active_state"] == "active"
        assert result["last_trigger"] == "123"
        assert result["next_elapse"] == "456"

    def test_returns_empty_on_error(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="not found"),
        )
        result = systemd_manager.get_timer_status("testcomp", "mytimer")
        assert result == {}


class TestEnableDisableUnit:
    def test_disable_creates_symlink(self, mocker, mock_user, tmp_path):
        home = tmp_path / "qm-testcomp"
        systemd_dir = home / ".config" / "systemd" / "user"
        systemd_dir.mkdir(parents=True)
        mocker.patch(
            "quadletman.services.systemd_manager.get_home",
            return_value=str(home),
        )
        systemd_manager.disable_unit(_sid("testcomp"), _unit("mycontainer"))
        mask = systemd_dir / "mycontainer.service"
        assert mask.is_symlink()
        import os

        assert os.readlink(mask) == "/dev/null"

    def test_enable_removes_mask_symlink(self, mocker, mock_user, tmp_path):
        import os

        home = tmp_path / "qm-testcomp"
        systemd_dir = home / ".config" / "systemd" / "user"
        systemd_dir.mkdir(parents=True)
        mask = systemd_dir / "mycontainer.service"
        os.symlink("/dev/null", mask)
        mocker.patch(
            "quadletman.services.systemd_manager.get_home",
            return_value=str(home),
        )
        systemd_manager.enable_unit(_sid("testcomp"), _unit("mycontainer"))
        assert not mask.exists()

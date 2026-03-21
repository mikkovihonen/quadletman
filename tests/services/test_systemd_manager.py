"""Tests for quadletman/services/systemd_manager.py — command construction and execution."""

import subprocess

import pytest

from quadletman.models.sanitized import SafeResourceName, SafeSlug, SafeStr, SafeUnitName
from quadletman.services import systemd_manager

# Convenience helpers so test literals read naturally
_sid = lambda v: SafeSlug.trusted(v, "test fixture")  # noqa: E731
_unit = lambda v: SafeUnitName.trusted(v, "test fixture")  # noqa: E731
_str = lambda v: SafeStr.trusted(v, "test fixture")  # noqa: E731
_res = lambda v: SafeResourceName.trusted(v, "test fixture")  # noqa: E731


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
        cmd = systemd_manager.exec_pty_cmd(_sid("mycomp"), _str("mycontainer"))
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
        cmd = systemd_manager.exec_pty_cmd(_sid("mycomp"), _str("mycontainer"), _str("1000"))
        user_idx = cmd.index("--user")
        assert cmd[user_idx + 1] == "1000"

    def test_root_user_flag(self, mocker):
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1234)
        mocker.patch("quadletman.services.systemd_manager._username", return_value="qm-mycomp")
        cmd = systemd_manager.exec_pty_cmd(_sid("mycomp"), _str("mycontainer"), _str("root"))
        user_idx = cmd.index("--user")
        assert cmd[user_idx + 1] == "root"

    def test_container_name_before_shell(self, mocker):
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1234)
        mocker.patch("quadletman.services.systemd_manager._username", return_value="qm-mycomp")
        cmd = systemd_manager.exec_pty_cmd(_sid("mycomp"), _str("mycontainer"))
        assert cmd[-1] == "/bin/sh"
        assert cmd[-2] == "mycontainer"


class TestBaseCmd:
    def test_contains_sudo_and_username(self, mocker):
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1234)
        mocker.patch("quadletman.services.systemd_manager._username", return_value="qm-mycomp")
        cmd = systemd_manager._base_cmd(_sid("mycomp"))
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
        props = systemd_manager.get_unit_status(_sid("testcomp"), _unit("mycontainer.service"))
        assert props["ActiveState"] == "active"
        assert props["SubState"] == "running"

    def test_returns_empty_dict_on_no_output(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        props = systemd_manager.get_unit_status(_sid("testcomp"), _unit("mycontainer.service"))
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
        images = systemd_manager.list_images(_sid("testcomp"))
        assert images == sorted({"nginx:latest", "alpine:3", "nginx:stable"})

    def test_excludes_none_tags(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="nginx:latest\n<none>:<none>\n", stderr=""
            ),
        )
        images = systemd_manager.list_images(_sid("testcomp"))
        assert all("<none>" not in img for img in images)

    def test_returns_empty_on_error(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )
        assert systemd_manager.list_images(_sid("testcomp")) == []


class TestGetJournalLines:
    def test_returns_stdout(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="log line 1\nlog line 2\n", stderr=""
            ),
        )
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1001)
        result = systemd_manager.get_journal_lines(_sid("testcomp"), _unit("mycontainer.service"))
        assert "log line 1" in result

    def test_raises_on_raw_str_params(self, mocker, mock_user):
        mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1001)
        with pytest.raises(TypeError):
            systemd_manager.get_journal_lines("testcomp", "mycontainer.service")


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
        result = systemd_manager.get_service_status(_sid("testcomp"), [_str("mycontainer")])
        assert len(result) == 1
        assert result[0]["active_state"] == "active"
        assert result[0]["container"] == "mycontainer"

    def test_empty_container_list(self, mocker, mock_user):
        result = systemd_manager.get_service_status(_sid("testcomp"), [])
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
        result = systemd_manager.get_timer_status(_sid("testcomp"), _str("mytimer"))
        assert result["active_state"] == "active"
        assert result["last_trigger"] == "123"
        assert result["next_elapse"] == "456"

    def test_returns_empty_on_error(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="not found"),
        )
        result = systemd_manager.get_timer_status(_sid("testcomp"), _str("mytimer"))
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


class TestListImagesDetail:
    def test_parses_image_json(self, mocker, mock_user):
        import json

        images = [
            {
                "Id": "abc123def456",
                "Names": ["nginx:latest"],
                "Size": 50000,
                "Created": "2024-01-01",
            }
        ]
        mocker.patch(
            "quadletman.services.systemd_manager._run",
            return_value=type("R", (), {"returncode": 0, "stdout": json.dumps(images)})(),
        )
        result = systemd_manager.list_images_detail(_sid("testcomp"))
        assert len(result) == 1
        assert result[0]["id"] == "abc123def456"[:12]
        assert "nginx:latest" in result[0]["names"]

    def test_returns_empty_on_failure(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager._run",
            return_value=type("R", (), {"returncode": 1, "stdout": ""})(),
        )
        result = systemd_manager.list_images_detail(_sid("testcomp"))
        assert result == []

    def test_handles_invalid_json(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager._run",
            return_value=type("R", (), {"returncode": 0, "stdout": "not json"})(),
        )
        result = systemd_manager.list_images_detail(_sid("testcomp"))
        assert result == []

    def test_marks_dangling_images(self, mocker, mock_user):
        import json

        images = [
            {
                "Id": "dangling123",
                "Names": [],
                "RepoTags": ["<none>:<none>"],
                "Size": 1000,
                "Created": "",
            }
        ]
        mocker.patch(
            "quadletman.services.systemd_manager._run",
            return_value=type("R", (), {"returncode": 0, "stdout": json.dumps(images)})(),
        )
        result = systemd_manager.list_images_detail(_sid("testcomp"))
        assert result[0]["dangling"] is True


class TestPruneImages:
    def test_returns_count_and_space(self, mocker, mock_user):
        output = "Deleted: sha256:abc\nDeleted: sha256:def\nTotal reclaimed space: 50MB"
        mocker.patch(
            "quadletman.services.systemd_manager._run",
            return_value=type("R", (), {"returncode": 0, "stdout": output, "stderr": ""})(),
        )
        result = systemd_manager.prune_images(_sid("testcomp"))
        assert result["count"] == 2
        assert "50MB" in result["space"]

    def test_raises_on_failure(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager._run",
            return_value=type("R", (), {"returncode": 1, "stdout": "", "stderr": "error msg"})(),
        )
        with pytest.raises(RuntimeError, match="error msg"):
            systemd_manager.prune_images(_sid("testcomp"))


class TestPullImage:
    def test_returns_stdout_on_success(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager._run",
            return_value=type(
                "R", (), {"returncode": 0, "stdout": "Pulled nginx:latest", "stderr": ""}
            )(),
        )
        result = systemd_manager.pull_image(_sid("testcomp"), _str("nginx:latest"))
        assert "Pulled" in result

    def test_raises_on_failure(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager._run",
            return_value=type(
                "R", (), {"returncode": 1, "stdout": "", "stderr": "manifest not found"}
            )(),
        )
        with pytest.raises(RuntimeError, match="manifest not found"):
            systemd_manager.pull_image(_sid("testcomp"), _str("invalid:image"))


class TestInspectContainer:
    def test_returns_parsed_dict(self, mocker, mock_user):
        import json

        data = [{"Id": "abc123", "Name": "/testcomp-web", "State": {"Status": "running"}}]
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=type("R", (), {"returncode": 0, "stdout": json.dumps(data)})(),
        )
        result = systemd_manager.inspect_container(_sid("testcomp"), _str("web"))
        assert result["Id"] == "abc123"

    def test_returns_empty_on_failure(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=type("R", (), {"returncode": 1, "stdout": ""})(),
        )
        result = systemd_manager.inspect_container(_sid("testcomp"), _str("web"))
        assert result == {}

    def test_returns_empty_on_empty_list(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=type("R", (), {"returncode": 0, "stdout": "[]"})(),
        )
        result = systemd_manager.inspect_container(_sid("testcomp"), _str("web"))
        assert result == {}


class TestSystemPrune:
    def test_returns_stdout(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="Deleted containers\nReclaimed 50MB", stderr=""
            ),
        )
        result = systemd_manager.system_prune(_sid("testcomp"))
        assert "Reclaimed" in result

    def test_command_includes_system_prune(self, mocker, mock_user):
        run_mock = mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        systemd_manager.system_prune(_sid("testcomp"))
        cmd = run_mock.call_args.args[0]
        assert "system" in cmd
        assert "prune" in cmd
        assert "-f" in cmd


class TestContainerTop:
    def test_parses_tabular_output(self, mocker, mock_user):
        output = "USER   PID   COMMAND\nroot   1     /bin/sh\nnobody 42    sleep 60\n"
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout=output, stderr=""),
        )
        result = systemd_manager.container_top(_sid("testcomp"), _res("web"))
        assert len(result) == 2
        assert result[0]["USER"] == "root"
        assert result[0]["COMMAND"] == "/bin/sh"
        assert result[1]["PID"] == "42"
        assert result[1]["COMMAND"] == "sleep 60"

    def test_returns_empty_on_failure(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 125, stdout="", stderr="not running"),
        )
        result = systemd_manager.container_top(_sid("testcomp"), _res("web"))
        assert result == []

    def test_returns_empty_on_header_only(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="USER PID COMMAND\n", stderr=""),
        )
        result = systemd_manager.container_top(_sid("testcomp"), _res("web"))
        assert result == []


class TestNetworkReload:
    def test_calls_network_reload(self, mocker, mock_user):
        run_mock = mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        systemd_manager.network_reload(_sid("testcomp"), _res("web"))
        cmd = run_mock.call_args.args[0]
        assert "network" in cmd
        assert "reload" in cmd
        assert "testcomp-web" in cmd


class TestSystemDf:
    def test_returns_parsed_json(self, mocker, mock_user):
        import json

        data = {"Images": [{"Size": 100}], "Containers": []}
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(data), stderr=""),
        )
        result = systemd_manager.system_df(_sid("testcomp"))
        assert "Images" in result

    def test_returns_empty_on_failure(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )
        result = systemd_manager.system_df(_sid("testcomp"))
        assert result == {}

    def test_returns_empty_on_invalid_json(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="not-json", stderr=""),
        )
        result = systemd_manager.system_df(_sid("testcomp"))
        assert result == {}


class TestGenerateKube:
    def test_returns_yaml_output(self, mocker, mock_user):
        yaml_str = "apiVersion: v1\nkind: Pod\n"
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout=yaml_str, stderr=""),
        )
        result = systemd_manager.generate_kube(_sid("testcomp"), _res("web"))
        assert "apiVersion" in result

    def test_returns_empty_on_failure(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 125, stdout="", stderr="not found"),
        )
        result = systemd_manager.generate_kube(_sid("testcomp"), _res("web"))
        assert result == ""


class TestHealthcheckRun:
    def test_healthy_container(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="healthy\n", stderr=""),
        )
        result = systemd_manager.healthcheck_run(_sid("testcomp"), _res("web"))
        assert result["healthy"] is True
        assert result["output"] == "healthy"

    def test_unhealthy_container(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="unhealthy\n", stderr=""),
        )
        result = systemd_manager.healthcheck_run(_sid("testcomp"), _res("web"))
        assert result["healthy"] is False


class TestAutoUpdate:
    def test_returns_stdout(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout='[{"unit":"web"}]', stderr=""),
        )
        result = systemd_manager.auto_update(_sid("testcomp"))
        assert "web" in result

    def test_command_includes_auto_update(self, mocker, mock_user):
        run_mock = mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        systemd_manager.auto_update(_sid("testcomp"))
        cmd = run_mock.call_args.args[0]
        assert "auto-update" in cmd
        assert "--format=json" in cmd


class TestVolumeExport:
    def test_returns_binary_data(self, mocker, mock_user):
        tar_bytes = b"\x1f\x8b\x08\x00" + b"\x00" * 100
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout=tar_bytes, stderr=b""),
        )
        result = systemd_manager.volume_export(_sid("testcomp"), _res("data"))
        assert result == tar_bytes

    def test_returns_empty_on_failure(self, mocker, mock_user):
        mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 125, stdout=b"", stderr=b"not found"),
        )
        result = systemd_manager.volume_export(_sid("testcomp"), _res("data"))
        assert result == b""


class TestVolumeImport:
    def test_calls_volume_import(self, mocker, mock_user):
        run_mock = mocker.patch(
            "quadletman.services.systemd_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        tar_data = b"\x1f\x8b\x08\x00" + b"\x00" * 50
        systemd_manager.volume_import(_sid("testcomp"), _res("data"), tar_data)
        cmd = run_mock.call_args.args[0]
        assert "volume" in cmd
        assert "import" in cmd
        assert "testcomp-data" in cmd
        assert run_mock.call_args[1].get("input") == tar_data

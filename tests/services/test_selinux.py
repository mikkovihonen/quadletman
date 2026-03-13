"""Tests for quadletman/services/selinux.py — graceful degradation and command construction."""

import subprocess

from quadletman.services import selinux


class TestIsSelinuxActive:
    def test_returns_false_when_getenforce_not_found(self, mocker):
        mocker.patch("quadletman.services.selinux.subprocess.run", side_effect=FileNotFoundError)
        assert selinux.is_selinux_active() is False

    def test_returns_false_when_disabled(self, mocker):
        mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="Disabled\n", stderr=""),
        )
        assert selinux.is_selinux_active() is False

    def test_returns_false_when_empty_output(self, mocker):
        mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        assert selinux.is_selinux_active() is False

    def test_returns_true_when_enforcing(self, mocker):
        mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="Enforcing\n", stderr=""),
        )
        assert selinux.is_selinux_active() is True

    def test_returns_true_when_permissive(self, mocker):
        mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="Permissive\n", stderr=""),
        )
        assert selinux.is_selinux_active() is True

    def test_returns_false_on_timeout(self, mocker):
        mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["getenforce"], 5),
        )
        assert selinux.is_selinux_active() is False


class TestApplyContext:
    def test_noop_when_selinux_inactive(self, mocker):
        mocker.patch("quadletman.services.selinux.is_selinux_active", return_value=False)
        run_mock = mocker.patch("quadletman.services.selinux.subprocess.run")
        selinux.apply_context("/some/path")
        run_mock.assert_not_called()

    def test_calls_semanage_and_chcon_when_active(self, mocker):
        mocker.patch("quadletman.services.selinux.is_selinux_active", return_value=True)
        run_mock = mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        selinux.apply_context("/data/mypath", "container_file_t")
        calls = [c.args[0] for c in run_mock.call_args_list]
        assert any("semanage" in str(c) for c in calls)
        assert any("chcon" in str(c) for c in calls)

    def test_uses_provided_context_type(self, mocker):
        mocker.patch("quadletman.services.selinux.is_selinux_active", return_value=True)
        run_mock = mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        selinux.apply_context("/data/path", "svirt_sandbox_file_t")
        all_args = " ".join(str(c.args) for c in run_mock.call_args_list)
        assert "svirt_sandbox_file_t" in all_args


class TestRelabel:
    def test_noop_when_selinux_inactive(self, mocker):
        mocker.patch("quadletman.services.selinux.is_selinux_active", return_value=False)
        run_mock = mocker.patch("quadletman.services.selinux.subprocess.run")
        selinux.relabel("/some/file")
        run_mock.assert_not_called()

    def test_calls_restorecon_when_active(self, mocker):
        mocker.patch("quadletman.services.selinux.is_selinux_active", return_value=True)
        run_mock = mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        selinux.relabel("/some/file")
        run_mock.assert_called_once()
        assert "restorecon" in run_mock.call_args.args[0]


class TestRemoveContext:
    def test_noop_when_selinux_inactive(self, mocker):
        mocker.patch("quadletman.services.selinux.is_selinux_active", return_value=False)
        run_mock = mocker.patch("quadletman.services.selinux.subprocess.run")
        selinux.remove_context("/some/path")
        run_mock.assert_not_called()

    def test_calls_semanage_delete_when_active(self, mocker):
        mocker.patch("quadletman.services.selinux.is_selinux_active", return_value=True)
        run_mock = mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        selinux.remove_context("/data/path")
        cmd = run_mock.call_args.args[0]
        assert "semanage" in cmd
        assert "-d" in cmd


class TestGetFileContextType:
    def test_returns_type_from_stat_output(self, mocker):
        mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="system_u:object_r:container_file_t:s0", stderr=""
            ),
        )
        result = selinux.get_file_context_type("/some/file")
        assert result == "container_file_t"

    def test_returns_none_on_failure(self, mocker):
        mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="?", stderr=""),
        )
        assert selinux.get_file_context_type("/bad") is None

    def test_returns_none_on_exception(self, mocker):
        mocker.patch(
            "quadletman.services.selinux.subprocess.run",
            side_effect=OSError("permission denied"),
        )
        assert selinux.get_file_context_type("/bad") is None

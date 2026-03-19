"""Tests for quadletman/services/secrets_manager.py — podman secret wrappers."""

import json
from unittest.mock import MagicMock

import pytest

from quadletman.models.sanitized import SafeMultilineStr, SafeSecretName, SafeSlug
from quadletman.services import secrets_manager

_sid = lambda v: SafeSlug.trusted(v, "test fixture")  # noqa: E731
_sec = lambda v: SafeSecretName.trusted(v, "test fixture")  # noqa: E731
_content = lambda v: SafeMultilineStr.trusted(v, "test fixture")  # noqa: E731


@pytest.fixture(autouse=True)
def mock_user_info(mocker):
    mocker.patch("quadletman.services.secrets_manager._username", return_value="qm-svc")
    mocker.patch("quadletman.services.secrets_manager.get_uid", return_value=1001)


class TestListPodmanSecrets:
    def test_returns_names_on_success(self, mocker):
        payload = json.dumps([{"Name": "db-pass"}, {"Name": "api-key"}])
        mocker.patch(
            "quadletman.services.secrets_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=payload),
        )
        result = secrets_manager.list_podman_secrets(_sid("svc"))
        assert result == ["db-pass", "api-key"]

    def test_returns_empty_on_nonzero_returncode(self, mocker):
        mocker.patch(
            "quadletman.services.secrets_manager.subprocess.run",
            return_value=MagicMock(returncode=1, stdout=""),
        )
        assert secrets_manager.list_podman_secrets(_sid("svc")) == []

    def test_returns_empty_on_invalid_json(self, mocker):
        mocker.patch(
            "quadletman.services.secrets_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="not-json"),
        )
        assert secrets_manager.list_podman_secrets(_sid("svc")) == []

    def test_returns_empty_on_empty_stdout(self, mocker):
        mocker.patch(
            "quadletman.services.secrets_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=""),
        )
        assert secrets_manager.list_podman_secrets(_sid("svc")) == []

    def test_skips_items_without_name(self, mocker):
        payload = json.dumps([{"Name": "ok"}, {"Id": "abc"}])
        mocker.patch(
            "quadletman.services.secrets_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=payload),
        )
        assert secrets_manager.list_podman_secrets(_sid("svc")) == ["ok"]


class TestCreatePodmanSecret:
    def test_calls_host_run(self, mocker):
        mock_run = mocker.patch(
            "quadletman.services.secrets_manager.host.run",
            return_value=MagicMock(returncode=0, stderr=""),
        )
        secrets_manager.create_podman_secret(_sid("svc"), _sec("my-secret"), _content("s3cr3t"))
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "podman" in cmd
        assert "secret" in cmd
        assert "create" in cmd
        assert "my-secret" in cmd

    def test_raises_on_nonzero_returncode(self, mocker):
        mocker.patch(
            "quadletman.services.secrets_manager.host.run",
            return_value=MagicMock(returncode=1, stderr="permission denied"),
        )
        with pytest.raises(RuntimeError, match="Failed to create secret"):
            secrets_manager.create_podman_secret(_sid("svc"), _sec("bad"), _content("val"))

    def test_passes_content_as_stdin(self, mocker):
        mock_run = mocker.patch(
            "quadletman.services.secrets_manager.host.run",
            return_value=MagicMock(returncode=0, stderr=""),
        )
        secrets_manager.create_podman_secret(_sid("svc"), _sec("tok"), _content("my-value"))
        kwargs = mock_run.call_args[1]
        assert kwargs.get("input") == "my-value"


class TestOverwritePodmanSecret:
    def test_calls_rm_then_create(self, mocker):
        mock_run = mocker.patch(
            "quadletman.services.secrets_manager.host.run",
            return_value=MagicMock(returncode=0, stderr=""),
        )
        secrets_manager.overwrite_podman_secret(
            _sid("svc"), _sec("my-secret"), _content("new-value")
        )
        assert mock_run.call_count == 2
        cmds = [mock_run.call_args_list[i][0][0] for i in range(2)]
        assert any("rm" in c for c in cmds)
        assert any("create" in c for c in cmds)

    def test_raises_on_create_failure(self, mocker):
        call_count = 0

        def side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            rc = 0 if call_count == 1 else 1
            return MagicMock(returncode=rc, stderr="write error")

        mocker.patch("quadletman.services.secrets_manager.host.run", side_effect=side_effect)
        with pytest.raises(RuntimeError, match="Failed to overwrite secret"):
            secrets_manager.overwrite_podman_secret(_sid("svc"), _sec("tok"), _content("val"))

    def test_passes_content_as_stdin(self, mocker):
        mock_run = mocker.patch(
            "quadletman.services.secrets_manager.host.run",
            return_value=MagicMock(returncode=0, stderr=""),
        )
        secrets_manager.overwrite_podman_secret(_sid("svc"), _sec("tok"), _content("secret-value"))
        create_call = mock_run.call_args_list[1]
        assert create_call[1].get("input") == "secret-value"


class TestDeletePodmanSecret:
    def test_calls_host_run(self, mocker):
        mock_run = mocker.patch(
            "quadletman.services.secrets_manager.host.run",
            return_value=MagicMock(returncode=0, stderr=""),
        )
        secrets_manager.delete_podman_secret(_sid("svc"), _sec("my-secret"))
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "secret" in cmd
        assert "rm" in cmd
        assert "my-secret" in cmd

    def test_raises_on_nonzero_returncode(self, mocker):
        mocker.patch(
            "quadletman.services.secrets_manager.host.run",
            return_value=MagicMock(returncode=1, stderr="not found"),
        )
        with pytest.raises(RuntimeError, match="Failed to delete secret"):
            secrets_manager.delete_podman_secret(_sid("svc"), _sec("ghost"))

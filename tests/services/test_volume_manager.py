"""Tests for quadletman/services/volume_manager.py — volume directory management."""

import pytest

from quadletman.services import volume_manager


@pytest.fixture(autouse=True)
def mock_host_ops(mocker):
    mocker.patch("quadletman.services.volume_manager.host.makedirs")
    mocker.patch("quadletman.services.volume_manager.host.run")
    mocker.patch("quadletman.services.volume_manager.host.rmtree")
    mocker.patch("quadletman.services.volume_manager.apply_context")
    mocker.patch("quadletman.services.volume_manager.remove_context")
    mocker.patch("quadletman.services.volume_manager._username", return_value="qm-svc")
    mocker.patch("quadletman.services.volume_manager._groupname", return_value="qm-svc")
    mocker.patch("quadletman.services.volume_manager._helper_username", return_value="qm-svc-1000")


class TestVolumePath:
    def test_includes_service_and_volume_name(self):
        path = volume_manager.volume_path("svc", "data")
        assert "svc" in path
        assert "data" in path

    def test_returns_string(self):
        assert isinstance(volume_manager.volume_path("svc", "data"), str)


class TestCreateVolumeDir:
    def test_creates_directory(self, mocker):
        volume_manager.create_volume_dir("svc", "data")
        volume_manager.host.makedirs.assert_called_once()

    def test_applies_selinux_context(self):
        volume_manager.create_volume_dir("svc", "data", "container_file_t")
        volume_manager.apply_context.assert_called_once()

    def test_custom_selinux_context_passed_through(self):
        volume_manager.create_volume_dir("svc", "data", "svirt_sandbox_file_t")
        args = volume_manager.apply_context.call_args[0]
        assert "svirt_sandbox_file_t" in args

    def test_returns_path_string(self, mocker):
        result = volume_manager.create_volume_dir("svc", "data")
        assert isinstance(result, str)

    def test_helper_user_created_for_nonzero_owner(self, mocker):
        mock_create = mocker.patch("quadletman.services.user_manager.create_helper_user")
        volume_manager.create_volume_dir("svc", "data", "container_file_t", 1000)
        mock_create.assert_called_once_with("svc", 1000)


class TestChownVolumeDir:
    def test_calls_host_run(self, mocker):
        volume_manager.chown_volume_dir("svc", "data", 0)
        volume_manager.host.run.assert_called()

    def test_creates_helper_user_for_nonzero_uid(self, mocker):
        mock_create = mocker.patch("quadletman.services.user_manager.create_helper_user")
        volume_manager.chown_volume_dir("svc", "data", 500)
        mock_create.assert_called_once_with("svc", 500)


class TestDeleteVolumeDir:
    def test_rmtree_called_when_dir_exists(self, mocker):
        mocker.patch("quadletman.services.volume_manager.os.path.isdir", return_value=True)
        volume_manager.delete_volume_dir("svc", "data")
        volume_manager.host.rmtree.assert_called_once()

    def test_no_rmtree_when_dir_missing(self, mocker):
        mocker.patch("quadletman.services.volume_manager.os.path.isdir", return_value=False)
        volume_manager.delete_volume_dir("svc", "data")
        volume_manager.host.rmtree.assert_not_called()

    def test_removes_selinux_context_before_delete(self, mocker):
        mocker.patch("quadletman.services.volume_manager.os.path.isdir", return_value=True)
        volume_manager.delete_volume_dir("svc", "data")
        volume_manager.remove_context.assert_called_once()


class TestDeleteAllServiceVolumes:
    def test_rmtree_called_when_service_dir_exists(self, mocker):
        mocker.patch("quadletman.services.volume_manager.os.path.isdir", return_value=True)
        volume_manager.delete_all_service_volumes("svc")
        volume_manager.host.rmtree.assert_called_once()

    def test_no_rmtree_when_service_dir_missing(self, mocker):
        mocker.patch("quadletman.services.volume_manager.os.path.isdir", return_value=False)
        volume_manager.delete_all_service_volumes("svc")
        volume_manager.host.rmtree.assert_not_called()


class TestEnsureVolumesBase:
    def test_calls_makedirs(self, mocker):
        volume_manager.ensure_volumes_base()
        volume_manager.host.makedirs.assert_called_once()

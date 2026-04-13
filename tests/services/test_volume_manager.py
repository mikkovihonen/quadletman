"""Tests for quadletman/services/volume_manager.py — volume directory management."""

import pytest

from quadletman.models.sanitized import (
    SafeAbsPath,
    SafeMultilineStr,
    SafeResourceName,
    SafeSELinuxContext,
    SafeSlug,
)
from quadletman.services import volume_manager

_sid = lambda v: SafeSlug.trusted(v, "test fixture")  # noqa: E731
_vol = lambda v: SafeResourceName.trusted(v, "test fixture")  # noqa: E731
_ctx = lambda v: SafeSELinuxContext.trusted(v, "test fixture")  # noqa: E731


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


class TestCreateVolumeDir:
    def test_creates_directory(self, mocker):
        volume_manager.create_volume_dir(_sid("svc"), _vol("data"))
        volume_manager.host.makedirs.assert_called_once()

    def test_applies_selinux_context(self):
        volume_manager.create_volume_dir(_sid("svc"), _vol("data"), _ctx("container_file_t"))
        volume_manager.apply_context.assert_called_once()

    def test_custom_selinux_context_passed_through(self):
        volume_manager.create_volume_dir(_sid("svc"), _vol("data"), _ctx("svirt_sandbox_file_t"))
        args = volume_manager.apply_context.call_args[0]
        assert "svirt_sandbox_file_t" in args

    def test_returns_path_string(self, mocker):
        result = volume_manager.create_volume_dir(_sid("svc"), _vol("data"))
        assert isinstance(result, str)

    def test_helper_user_created_for_nonzero_owner(self, mocker):
        mock_create = mocker.patch("quadletman.services.volume_manager.create_helper_user")
        volume_manager.create_volume_dir(_sid("svc"), _vol("data"), _ctx("container_file_t"), 1000)
        mock_create.assert_called_once_with("svc", 1000)


class TestChownVolumeDir:
    def test_calls_host_run(self, mocker):
        volume_manager.chown_volume_dir(_sid("svc"), _vol("data"), 0)
        volume_manager.host.run.assert_called()

    def test_creates_helper_user_for_nonzero_uid(self, mocker):
        mock_create = mocker.patch("quadletman.services.volume_manager.create_helper_user")
        volume_manager.chown_volume_dir(_sid("svc"), _vol("data"), 500)
        mock_create.assert_called_once_with("svc", 500)


class TestDeleteVolumeDir:
    def test_rmtree_called_when_dir_exists(self, mocker):
        mocker.patch("quadletman.services.volume_manager.os.path.isdir", return_value=True)
        volume_manager.delete_volume_dir(_sid("svc"), _vol("data"))
        volume_manager.host.rmtree.assert_called_once()

    def test_no_rmtree_when_dir_missing(self, mocker):
        mocker.patch("quadletman.services.volume_manager.os.path.isdir", return_value=False)
        volume_manager.delete_volume_dir(_sid("svc"), _vol("data"))
        volume_manager.host.rmtree.assert_not_called()

    def test_removes_selinux_context_before_delete(self, mocker):
        mocker.patch("quadletman.services.volume_manager.os.path.isdir", return_value=True)
        volume_manager.delete_volume_dir(_sid("svc"), _vol("data"))
        volume_manager.remove_context.assert_called_once()


class TestDeleteAllServiceVolumes:
    def test_rmtree_called_when_service_dir_exists(self, mocker):
        mocker.patch("quadletman.services.volume_manager.os.path.isdir", return_value=True)
        volume_manager.delete_all_service_volumes(_sid("svc"))
        volume_manager.host.rmtree.assert_called_once()

    def test_no_rmtree_when_service_dir_missing(self, mocker):
        mocker.patch("quadletman.services.volume_manager.os.path.isdir", return_value=False)
        volume_manager.delete_all_service_volumes(_sid("svc"))
        volume_manager.host.rmtree.assert_not_called()


class TestEnsureVolumesBase:
    def test_calls_makedirs(self, mocker):
        volume_manager.ensure_volumes_base()
        volume_manager.host.makedirs.assert_called_once()


# ---------------------------------------------------------------------------
# Volume file browser operations
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_file_ops(mocker):
    mocker.patch("quadletman.services.volume_manager.host.makedirs")
    mocker.patch("quadletman.services.volume_manager.host.write_text")
    mocker.patch("quadletman.services.volume_manager.host.write_bytes")
    mocker.patch("quadletman.services.volume_manager.host.rmtree")
    mocker.patch("quadletman.services.volume_manager.host.unlink")
    mocker.patch("quadletman.services.volume_manager.host.chmod")
    mocker.patch("quadletman.services.volume_manager.host.chown")
    mocker.patch("quadletman.services.volume_manager.relabel")
    mocker.patch("quadletman.services.volume_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.volume_manager.get_service_gid", return_value=1001)
    mocker.patch("quadletman.services.volume_manager.get_helper_uid", return_value=2001)


_ap = lambda v: SafeAbsPath.trusted(v, "test fixture")  # noqa: E731
_mc = lambda v: SafeMultilineStr.trusted(v, "test fixture")  # noqa: E731


class TestSaveFile:
    def test_creates_parent_and_writes(self, mock_file_ops):
        volume_manager.save_file(_sid("svc"), _ap("/vol/data/config.txt"), _mc("key=val"))
        volume_manager.host.makedirs.assert_called()
        volume_manager.host.write_text.assert_called_once()

    def test_relabels_after_write(self, mock_file_ops):
        volume_manager.save_file(_sid("svc"), _ap("/vol/data/config.txt"), _mc("x"))
        volume_manager.relabel.assert_called_once()


class TestUploadFile:
    def test_writes_binary_data(self, mock_file_ops):
        volume_manager.upload_file(_sid("svc"), _ap("/vol/data/img.png"), b"\x89PNG")
        volume_manager.host.write_bytes.assert_called_once()

    def test_relabels_after_write(self, mock_file_ops):
        volume_manager.upload_file(_sid("svc"), _ap("/vol/data/img.png"), b"\x89PNG")
        volume_manager.relabel.assert_called_once()


class TestDeleteEntry:
    def test_removes_directory(self, mock_file_ops, mocker):
        mocker.patch("os.path.isdir", return_value=True)
        volume_manager.delete_entry(_sid("svc"), _ap("/vol/data/subdir"))
        volume_manager.host.rmtree.assert_called_once()

    def test_removes_file(self, mock_file_ops, mocker):
        mocker.patch("os.path.isdir", return_value=False)
        volume_manager.delete_entry(_sid("svc"), _ap("/vol/data/file.txt"))
        volume_manager.host.unlink.assert_called_once()


class TestMkdirEntry:
    def test_creates_and_chowns(self, mock_file_ops):
        volume_manager.mkdir_entry(_sid("svc"), _ap("/vol/data/newdir"))
        volume_manager.host.makedirs.assert_called()
        volume_manager.host.chown.assert_called_once_with(_ap("/vol/data/newdir"), 1001, 1001)
        volume_manager.relabel.assert_called_once()

    def test_chowns_to_helper_user_when_owner_uid_set(self, mock_file_ops):
        volume_manager.mkdir_entry(_sid("svc"), _ap("/vol/data/newdir"), owner_uid=1000)
        volume_manager.host.chown.assert_called_once_with(_ap("/vol/data/newdir"), 2001, 1001)


class TestChmodEntry:
    def test_calls_host_chmod(self, mock_file_ops):
        volume_manager.chmod_entry(_sid("svc"), _ap("/vol/data/file.txt"), 0o755)
        volume_manager.host.chmod.assert_called_once()


# ---------------------------------------------------------------------------
# Volume owner UID propagation
# ---------------------------------------------------------------------------


class TestVolumeOwnerUidPropagation:
    """Verify that save_file, upload_file, and mkdir_entry use the correct owner."""

    def test_save_file_uses_compartment_root_by_default(self, mock_file_ops):
        volume_manager.save_file(_sid("svc"), _ap("/vol/data/f.txt"), _mc("content"))
        _, kwargs = volume_manager.host.write_text.call_args
        assert kwargs.get("uid") is None  # positional arg
        args = volume_manager.host.write_text.call_args.args
        assert args[2] == 1001  # uid = compartment root

    def test_save_file_uses_helper_user_when_owner_uid_set(self, mock_file_ops):
        volume_manager.save_file(
            _sid("svc"), _ap("/vol/data/f.txt"), _mc("content"), owner_uid=1000
        )
        args = volume_manager.host.write_text.call_args.args
        assert args[2] == 2001  # uid = helper user

    def test_upload_file_uses_compartment_root_by_default(self, mock_file_ops):
        volume_manager.upload_file(_sid("svc"), _ap("/vol/data/img.png"), b"\x89PNG")
        args = volume_manager.host.write_bytes.call_args.args
        assert args[2] == 1001  # uid = compartment root

    def test_upload_file_uses_helper_user_when_owner_uid_set(self, mock_file_ops):
        volume_manager.upload_file(
            _sid("svc"), _ap("/vol/data/img.png"), b"\x89PNG", owner_uid=1000
        )
        args = volume_manager.host.write_bytes.call_args.args
        assert args[2] == 2001  # uid = helper user

    def test_fallback_to_root_when_helper_not_found(self, mock_file_ops, mocker):
        mocker.patch("quadletman.services.volume_manager.get_helper_uid", return_value=None)
        volume_manager.upload_file(
            _sid("svc"), _ap("/vol/data/img.png"), b"\x89PNG", owner_uid=9999
        )
        args = volume_manager.host.write_bytes.call_args.args
        assert args[2] == 1001  # falls back to compartment root

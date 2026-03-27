"""Tests for quadletman/services/user_manager.py."""

import json
import subprocess
import types

import pytest

from quadletman.models.sanitized import (
    SafeAbsPath,
    SafeMultilineStr,
    SafeResourceName,
    SafeSlug,
    SafeStr,
)
from quadletman.services import user_manager

_sid = lambda v: SafeSlug.trusted(v, "test fixture")  # noqa: E731
_s = lambda v: SafeStr.trusted(v, "test fixture")  # noqa: E731

_PW = types.SimpleNamespace(
    pw_name="qm-test",
    pw_uid=1001,
    pw_gid=1001,
    pw_dir="/home/qm-test",
    pw_shell="/bin/false",
    pw_gecos="quadletman service test",
    pw_passwd="x",
)


# ---------------------------------------------------------------------------
# Username / groupname helpers
# ---------------------------------------------------------------------------


class TestUsernameFunctions:
    def test_username(self):
        result = user_manager._username(_sid("mycomp"))
        assert str(result) == "qm-mycomp"

    def test_groupname(self):
        result = user_manager._groupname(_sid("mycomp"))
        assert str(result) == "qm-mycomp"

    def test_helper_username(self):
        result = user_manager._helper_username(_sid("mycomp"), 1000)
        assert str(result) == "qm-mycomp-1000"

    def test_helper_username_zero(self):
        result = user_manager._helper_username(_sid("mycomp"), 0)
        assert str(result) == "qm-mycomp-0"


# ---------------------------------------------------------------------------
# User existence / UID / home lookups
# ---------------------------------------------------------------------------


class TestUserExists:
    def test_returns_true_when_user_exists(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        assert user_manager.user_exists(_sid("test")) is True

    def test_returns_false_when_user_missing(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", side_effect=KeyError)
        assert user_manager.user_exists(_sid("test")) is False


class TestGetUid:
    def test_returns_uid(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        assert user_manager.get_uid(_sid("test")) == 1001


class TestGetHome:
    def test_returns_home_dir(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        assert user_manager.get_home(_sid("test")) == "/home/qm-test"


class TestGetUserInfo:
    def test_returns_info(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch("quadletman.services.user_manager.get_subid_start", return_value=100000)
        info = user_manager.get_user_info(_sid("test"))
        assert info["uid"] == 1001
        assert info["gid"] == 1001
        assert info["subuid_start"] == 100000

    def test_returns_none_when_user_missing(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", side_effect=KeyError)
        info = user_manager.get_user_info(_sid("test"))
        assert info["uid"] is None
        assert info["gid"] is None


class TestGetServiceGid:
    def test_returns_gid(self, mocker):
        grp_result = types.SimpleNamespace(gr_gid=1001)
        mocker.patch("quadletman.services.user_manager.grp.getgrnam", return_value=grp_result)
        assert user_manager.get_service_gid(_sid("test")) == 1001


# ---------------------------------------------------------------------------
# fuse-overlayfs detection
# ---------------------------------------------------------------------------


class TestFindFuseOverlayfs:
    def test_finds_candidate(self, mocker):
        mocker.patch("quadletman.services.user_manager.os.path.isfile", return_value=True)
        mocker.patch("quadletman.services.user_manager.os.access", return_value=True)
        result = user_manager._find_fuse_overlayfs()
        assert result is not None
        assert "fuse-overlayfs" in result

    def test_fallback_to_which(self, mocker):
        mocker.patch("quadletman.services.user_manager.os.path.isfile", return_value=False)
        mocker.patch("quadletman.services.user_manager.os.access", return_value=False)
        mocker.patch("shutil.which", return_value="/opt/bin/fuse-overlayfs")
        result = user_manager._find_fuse_overlayfs()
        assert result == "/opt/bin/fuse-overlayfs"

    def test_returns_none_when_missing(self, mocker):
        mocker.patch("quadletman.services.user_manager.os.path.isfile", return_value=False)
        mocker.patch("quadletman.services.user_manager.os.access", return_value=False)
        mocker.patch("shutil.which", return_value=None)
        result = user_manager._find_fuse_overlayfs()
        assert result is None


# ---------------------------------------------------------------------------
# Service user creation
# ---------------------------------------------------------------------------


class TestCreateServiceUser:
    def test_idempotent_when_exists(self, mocker):
        mocker.patch("quadletman.services.user_manager.user_exists", return_value=True)
        mocker.patch("quadletman.services.user_manager.get_uid", return_value=1001)
        run_mock = mocker.patch("quadletman.services.host.subprocess.run")
        uid = user_manager.create_service_user(_sid("test"))
        assert uid == 1001
        run_mock.assert_not_called()

    def test_creates_user(self, mocker):
        mocker.patch("quadletman.services.user_manager.user_exists", return_value=False)
        mocker.patch("quadletman.services.user_manager._ensure_group")
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        mocker.patch("quadletman.services.user_manager.get_uid", return_value=1001)
        mocker.patch("quadletman.services.user_manager._setup_subuid_subgid")
        mocker.patch("quadletman.services.user_manager.os.getuid", return_value=0)
        uid = user_manager.create_service_user(_sid("test"))
        assert uid == 1001


class TestEnsureGroup:
    def test_existing_group_returns_gid(self, mocker):
        grp_result = types.SimpleNamespace(gr_gid=1001)
        mocker.patch("quadletman.services.user_manager.grp.getgrnam", return_value=grp_result)
        gid = user_manager._ensure_group(_s("qm-test"))
        assert gid == 1001

    def test_creates_missing_group(self, mocker):
        grp_result = types.SimpleNamespace(gr_gid=1002)
        mocker.patch(
            "quadletman.services.user_manager.grp.getgrnam",
            side_effect=[KeyError, grp_result],
        )
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        gid = user_manager._ensure_group(_s("qm-test"))
        assert gid == 1002


# ---------------------------------------------------------------------------
# Helper users
# ---------------------------------------------------------------------------


class TestCreateHelperUser:
    def test_idempotent_when_exists(self, mocker):
        pw = types.SimpleNamespace(pw_uid=101000)
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=pw)
        uid = user_manager.create_helper_user(_sid("test"), 1000)
        assert uid == 101000

    def test_creates_helper(self, mocker):
        mocker.patch(
            "quadletman.services.user_manager.pwd.getpwnam",
            side_effect=KeyError,
        )
        mocker.patch("quadletman.services.user_manager.get_subid_start", return_value=100000)
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        uid = user_manager.create_helper_user(_sid("test"), 1000)
        assert uid == 101000
        run_mock.assert_called_once()

    def test_raises_without_subuid(self, mocker):
        mocker.patch(
            "quadletman.services.user_manager.pwd.getpwnam",
            side_effect=KeyError,
        )
        mocker.patch("quadletman.services.user_manager.get_subid_start", return_value=None)
        with pytest.raises(RuntimeError, match="no subUID range"):
            user_manager.create_helper_user(_sid("test"), 1000)


class TestGetHelperUid:
    def test_returns_uid_when_exists(self, mocker):
        pw = types.SimpleNamespace(pw_uid=101000)
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=pw)
        assert user_manager.get_helper_uid(_sid("test"), 1000) == 101000

    def test_returns_none_when_missing(self, mocker):
        mocker.patch(
            "quadletman.services.user_manager.pwd.getpwnam",
            side_effect=KeyError,
        )
        assert user_manager.get_helper_uid(_sid("test"), 1000) is None


class TestListHelperUsers:
    def test_lists_matching_users(self, mocker):
        pw1 = types.SimpleNamespace(pw_name="qm-test-1000", pw_uid=101000)
        pw2 = types.SimpleNamespace(pw_name="qm-test-2000", pw_uid=102000)
        pw3 = types.SimpleNamespace(pw_name="qm-other-1000", pw_uid=201000)
        pw4 = types.SimpleNamespace(pw_name="root", pw_uid=0)
        mocker.patch(
            "quadletman.services.user_manager.pwd.getpwall", return_value=[pw1, pw2, pw3, pw4]
        )
        result = user_manager.list_helper_users(_sid("test"))
        assert len(result) == 2
        assert result[0]["container_uid"] == 1000
        assert result[1]["container_uid"] == 2000

    def test_empty_when_no_helpers(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwall", return_value=[])
        assert user_manager.list_helper_users(_sid("test")) == []


class TestDeleteAllHelperUsers:
    def test_deletes_matching_users(self, mocker):
        pw1 = types.SimpleNamespace(pw_name="qm-test-1000", pw_uid=101000)
        pw2 = types.SimpleNamespace(pw_name="qm-other-1000", pw_uid=201000)
        mocker.patch("quadletman.services.user_manager.pwd.getpwall", return_value=[pw1, pw2])
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        user_manager.delete_all_helper_users(_sid("test"))
        # Only pw1 should be deleted (qm-test-1000)
        assert run_mock.call_count == 1


class TestDeleteServiceGroup:
    def test_deletes_existing_group(self, mocker):
        grp_result = types.SimpleNamespace(gr_gid=1001)
        mocker.patch("quadletman.services.user_manager.grp.getgrnam", return_value=grp_result)
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        user_manager.delete_service_group(_sid("test"))
        run_mock.assert_called_once()

    def test_noop_when_group_missing(self, mocker):
        mocker.patch("quadletman.services.user_manager.grp.getgrnam", side_effect=KeyError)
        run_mock = mocker.patch("quadletman.services.host.subprocess.run")
        user_manager.delete_service_group(_sid("test"))
        run_mock.assert_not_called()


# ---------------------------------------------------------------------------
# subuid / subgid
# ---------------------------------------------------------------------------


class TestNextSubidStart:
    def test_parses_existing_entries(self, tmp_path):
        p = tmp_path / "subuid"
        p.write_text("user1:100000:65536\nuser2:165536:65536\n")
        result = user_manager._next_subid_start(SafeAbsPath.trusted(str(p), "test"))
        assert result == 165536 + 65536

    def test_empty_file(self, tmp_path):
        p = tmp_path / "subuid"
        p.write_text("")
        result = user_manager._next_subid_start(SafeAbsPath.trusted(str(p), "test"))
        assert result == 100000

    def test_missing_file(self, tmp_path):
        p = tmp_path / "does_not_exist"
        result = user_manager._next_subid_start(SafeAbsPath.trusted(str(p), "test"))
        assert result == 100000

    def test_ignores_malformed_lines(self, tmp_path):
        p = tmp_path / "subuid"
        p.write_text("badline\nuser1:100000:65536\n::\n")
        result = user_manager._next_subid_start(SafeAbsPath.trusted(str(p), "test"))
        assert result == 100000 + 65536


class TestGetSubidStart:
    def test_returns_start_for_uid(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "builtins.open",
            mocker.mock_open(read_data="qm-test:200000:65536\nother:100000:65536\n"),
        )
        result = user_manager.get_subid_start(_sid("test"), _s("uid"))
        assert result == 200000

    def test_returns_none_when_not_found(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "builtins.open",
            mocker.mock_open(read_data="other:100000:65536\n"),
        )
        result = user_manager.get_subid_start(_sid("test"), _s("uid"))
        assert result is None


# ---------------------------------------------------------------------------
# Podman info / drivers
# ---------------------------------------------------------------------------


class TestGetCompartmentPodmanInfo:
    def test_returns_parsed_json(self, mocker):
        info = {"host": {"os": "linux"}, "plugins": {"log": ["journald", "k8s-file"]}}
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.user_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(info), stderr=""),
        )
        result = user_manager.get_compartment_podman_info(_sid("test"))
        assert result["host"]["os"] == "linux"

    def test_returns_empty_on_failure(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.user_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )
        result = user_manager.get_compartment_podman_info(_sid("test"))
        assert result == {}

    def test_returns_empty_on_invalid_json(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.user_manager.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="not json", stderr=""),
        )
        result = user_manager.get_compartment_podman_info(_sid("test"))
        assert result == {}


class TestGetCompartmentLogDrivers:
    def test_returns_drivers_from_info(self, mocker):
        mocker.patch(
            "quadletman.services.user_manager.get_compartment_podman_info",
            return_value={"plugins": {"log": ["journald", "k8s-file", "none"]}},
        )
        result = user_manager.get_compartment_log_drivers(_sid("test"))
        assert result == ["journald", "k8s-file", "none"]

    def test_falls_back_to_root(self, mocker):
        mocker.patch(
            "quadletman.services.user_manager.get_compartment_podman_info",
            return_value={},
        )
        mocker.patch(
            "quadletman.services.user_manager.get_log_drivers",
            return_value=["journald"],
        )
        result = user_manager.get_compartment_log_drivers(_sid("test"))
        assert result == ["journald"]


class TestGetCompartmentDrivers:
    def test_returns_drivers_from_info(self, mocker):
        mocker.patch(
            "quadletman.services.user_manager.get_compartment_podman_info",
            return_value={
                "plugins": {
                    "network": ["bridge", "macvlan"],
                    "volume": ["local"],
                }
            },
        )
        net, vol = user_manager.get_compartment_drivers(_sid("test"))
        assert net[0] == "bridge"
        assert "macvlan" in net
        assert vol[0] == "local"

    def test_falls_back_to_root(self, mocker):
        mocker.patch(
            "quadletman.services.user_manager.get_compartment_podman_info",
            return_value={},
        )
        mocker.patch(
            "quadletman.services.user_manager.get_network_drivers",
            return_value=["bridge"],
        )
        mocker.patch(
            "quadletman.services.user_manager.get_volume_drivers",
            return_value=["local"],
        )
        net, vol = user_manager.get_compartment_drivers(_sid("test"))
        assert net == ["bridge"]
        assert vol == ["local"]


# ---------------------------------------------------------------------------
# Linger
# ---------------------------------------------------------------------------


class TestLingerEnabled:
    def test_returns_true(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch("quadletman.services.user_manager.os.path.exists", return_value=True)
        assert user_manager.linger_enabled(_sid("test")) is True

    def test_returns_false(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch("quadletman.services.user_manager.os.path.exists", return_value=False)
        assert user_manager.linger_enabled(_sid("test")) is False


class TestEnableLinger:
    def test_calls_loginctl(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        mocker.patch("quadletman.services.user_manager._wait_for_runtime_dir")
        user_manager.enable_linger(_sid("test"))
        args = run_mock.call_args_list[0].args[0]
        assert "enable-linger" in args


class TestDisableLinger:
    def test_calls_loginctl(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        user_manager.disable_linger(_sid("test"))
        args = run_mock.call_args_list[0].args[0]
        assert "disable-linger" in args


# ---------------------------------------------------------------------------
# Registry login / logout / list
# ---------------------------------------------------------------------------


class TestRegistryLogin:
    def test_successful_login(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="Login Succeeded", stderr=""),
        )
        # Should not raise
        user_manager.registry_login(_sid("test"), _s("docker.io"), _s("user"), _s("pass"))

    def test_failed_login_raises(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="unauthorized"),
        )
        with pytest.raises(RuntimeError, match="unauthorized"):
            user_manager.registry_login(_sid("test"), _s("docker.io"), _s("user"), _s("pass"))


class TestRegistryLogout:
    def test_successful_logout(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        user_manager.registry_logout(_sid("test"), _s("docker.io"))

    def test_failed_logout_raises(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="not logged in"),
        )
        with pytest.raises(RuntimeError, match="not logged in"):
            user_manager.registry_logout(_sid("test"), _s("docker.io"))


class TestListRegistryLogins:
    def test_returns_registries(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        auth_data = json.dumps({"auths": {"docker.io": {}, "ghcr.io": {}}})
        mocker.patch("quadletman.services.host.read_text", return_value=auth_data)
        result = user_manager.list_registry_logins(_sid("test"))
        assert "docker.io" in result
        assert "ghcr.io" in result

    def test_returns_empty_when_no_file(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch("quadletman.services.host.read_text", return_value=None)
        assert user_manager.list_registry_logins(_sid("test")) == []

    def test_returns_empty_on_invalid_json(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch("quadletman.services.host.read_text", return_value="not json")
        assert user_manager.list_registry_logins(_sid("test")) == []


# ---------------------------------------------------------------------------
# Delete service user
# ---------------------------------------------------------------------------


class TestDeleteServiceUser:
    def test_noop_when_user_missing(self, mocker):
        mocker.patch("quadletman.services.user_manager.user_exists", return_value=False)
        run_mock = mocker.patch("quadletman.services.host.subprocess.run")
        user_manager.delete_service_user(_sid("test"))
        run_mock.assert_not_called()

    def test_full_deletion_sequence(self, mocker):
        mocker.patch("quadletman.services.user_manager.user_exists", return_value=True)
        mocker.patch("quadletman.services.user_manager.get_home", return_value="/home/qm-test")
        mocker.patch("quadletman.services.user_manager.get_uid", return_value=1001)
        mocker.patch("quadletman.services.user_manager._remove_subuid_subgid")
        mocker.patch("quadletman.services.user_manager.os.path.isdir", return_value=True)
        mocker.patch("quadletman.services.user_manager.delete_all_helper_users")
        mocker.patch("quadletman.services.user_manager.delete_service_group")
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        rmtree_mock = mocker.patch("quadletman.services.host.shutil.rmtree")
        user_manager.delete_service_user(_sid("test"))
        # Should have made multiple host.run calls (stop, disable-linger, terminate, pkill, userdel)
        assert run_mock.call_count >= 4
        rmtree_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Write managed Containerfile
# ---------------------------------------------------------------------------


class TestWriteManagedContainerfile:
    def test_writes_containerfile(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        mocker.patch("quadletman.services.host.write_text")
        result = user_manager.write_managed_containerfile(
            _sid("test"),
            SafeResourceName.trusted("web", "test"),
            SafeMultilineStr.trusted("FROM nginx:latest", "test"),
        )
        assert "builds" in result
        assert "web" in result


# ---------------------------------------------------------------------------
# Ensure quadlet dir
# ---------------------------------------------------------------------------


class TestEnsureQuadletDir:
    def test_creates_dir(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        result = user_manager.ensure_quadlet_dir(_sid("test"))
        assert ".config/containers/systemd" in result


# ---------------------------------------------------------------------------
# Read containers.conf / storage.conf
# ---------------------------------------------------------------------------


class TestReadConfs:
    def test_read_containers_conf(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch("quadletman.services.host.read_text", return_value="[network]\n")
        result = user_manager.read_containers_conf(_sid("test"))
        assert result == "[network]\n"

    def test_read_storage_conf(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch("quadletman.services.host.read_text", return_value="[storage]\n")
        result = user_manager.read_storage_conf(_sid("test"))
        assert result == "[storage]\n"

    def test_read_containers_conf_missing(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch("quadletman.services.host.read_text", return_value=None)
        assert user_manager.read_containers_conf(_sid("test")) is None


# ---------------------------------------------------------------------------
# Podman reset / migrate
# ---------------------------------------------------------------------------


class TestPodmanReset:
    def test_runs_reset(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        user_manager.podman_reset(_sid("test"))
        args = run_mock.call_args_list[0].args[0]
        assert "reset" in args
        assert "--force" in args


class TestPodmanMigrate:
    def test_runs_migrate(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        run_mock = mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        user_manager.podman_migrate(_sid("test"))
        args = run_mock.call_args_list[0].args[0]
        assert "migrate" in args


# ---------------------------------------------------------------------------
# Write storage.conf / containers.conf
# ---------------------------------------------------------------------------


class TestWriteStorageConf:
    def test_writes_conf(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        mocker.patch("quadletman.services.host.write_text")
        mocker.patch("quadletman.services.user_manager._find_fuse_overlayfs", return_value=None)
        user_manager.write_storage_conf(_sid("test"))

    def test_writes_conf_with_fuse_overlayfs(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        mocker.patch("quadletman.services.host.write_text")
        mocker.patch(
            "quadletman.services.user_manager._find_fuse_overlayfs",
            return_value="/usr/bin/fuse-overlayfs",
        )
        user_manager.write_storage_conf(_sid("test"))


class TestWriteContainersConf:
    def test_writes_conf_with_pasta(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        mocker.patch("quadletman.services.host.write_text")
        features = types.SimpleNamespace(pasta=True, version_str="5.0.0")
        mocker.patch("quadletman.services.user_manager.get_features", return_value=features)
        user_manager.write_containers_conf(_sid("test"))

    def test_writes_conf_without_pasta(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        mocker.patch("quadletman.services.host.write_text")
        features = types.SimpleNamespace(pasta=False, version_str="3.0.0")
        mocker.patch("quadletman.services.user_manager.get_features", return_value=features)
        user_manager.write_containers_conf(_sid("test"))


# ---------------------------------------------------------------------------
# Config file management
# ---------------------------------------------------------------------------

_rn = lambda v: SafeResourceName.trusted(v, "test fixture")  # noqa: E731
_ap = lambda v: SafeAbsPath.trusted(v, "test fixture")  # noqa: E731
_mc = lambda v: SafeMultilineStr.trusted(v, "test fixture")  # noqa: E731


class TestWriteConfigFile:
    def test_creates_conf_dir_and_writes(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        makedirs = mocker.patch("quadletman.services.host.makedirs")
        write = mocker.patch("quadletman.services.host.write_text")
        dest = user_manager.write_config_file(
            _sid("test"), _s("container"), _rn("web"), _s("seccomp_profile"), _mc("{}"), _s(".json")
        )
        assert "conf/container/web/seccomp_profile.json" in dest
        makedirs.assert_called_once()
        write.assert_called_once()

    def test_returns_destination_path(self, mocker):
        mocker.patch("quadletman.services.user_manager.pwd.getpwnam", return_value=_PW)
        mocker.patch(
            "quadletman.services.host.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        mocker.patch("quadletman.services.host.makedirs")
        mocker.patch("quadletman.services.host.write_text")
        dest = user_manager.write_config_file(
            _sid("test"), _s("image"), _rn("nginx"), _s("auth_file"), _mc("{}"), _s(".json")
        )
        assert dest.endswith("auth_file.json")


class TestDeleteConfigFile:
    def test_deletes_existing_file(self, mocker):
        mocker.patch("quadletman.services.user_manager.get_home", return_value="/home/qm-test")
        mocker.patch("os.path.realpath", side_effect=lambda p: p)
        mocker.patch("os.path.isfile", return_value=True)
        unlink = mocker.patch("quadletman.services.host.os.unlink")
        user_manager.delete_config_file(
            _sid("test"), _ap("/home/qm-test/conf/container/web/env.env")
        )
        unlink.assert_called_once()

    def test_noop_for_nonexistent(self, mocker):
        mocker.patch("quadletman.services.user_manager.get_home", return_value="/home/qm-test")
        mocker.patch("os.path.realpath", side_effect=lambda p: p)
        mocker.patch("os.path.isfile", return_value=False)
        unlink = mocker.patch("quadletman.services.host.os.unlink")
        user_manager.delete_config_file(_sid("test"), _ap("/home/qm-test/conf/x"))
        unlink.assert_not_called()

    def test_rejects_path_outside_home(self, mocker):
        mocker.patch("quadletman.services.user_manager.get_home", return_value="/home/qm-test")
        mocker.patch("os.path.realpath", side_effect=lambda p: p)
        with pytest.raises(ValueError, match="outside"):
            user_manager.delete_config_file(_sid("test"), _ap("/etc/passwd"))


class TestCleanupResourceConfigDir:
    def test_removes_existing_dir(self, mocker):
        mocker.patch("quadletman.services.user_manager.get_home", return_value="/home/qm-test")
        mocker.patch("os.path.isdir", return_value=True)
        rmtree = mocker.patch("quadletman.services.host.shutil.rmtree")
        user_manager.cleanup_resource_config_dir(_sid("test"), _s("container"), _rn("web"))
        rmtree.assert_called_once()

    def test_noop_when_dir_missing(self, mocker):
        mocker.patch("quadletman.services.user_manager.get_home", return_value="/home/qm-test")
        mocker.patch("os.path.isdir", return_value=False)
        rmtree = mocker.patch("quadletman.services.host.shutil.rmtree")
        user_manager.cleanup_resource_config_dir(_sid("test"), _s("container"), _rn("web"))
        rmtree.assert_not_called()

    def test_noop_when_user_missing(self, mocker):
        mocker.patch("quadletman.services.user_manager.get_home", side_effect=KeyError("no user"))
        # Should not raise
        user_manager.cleanup_resource_config_dir(_sid("gone"), _s("container"), _rn("web"))

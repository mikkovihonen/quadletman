"""Tests for quadletman/security/auth.py — credential management and group checks."""

import types

from quadletman.models.sanitized import SafeUsername
from quadletman.security.auth import (
    _user_in_allowed_group,
    get_admin_credentials,
    set_admin_credentials,
)


class TestAdminCredentials:
    def test_set_and_get(self):
        set_admin_credentials(("admin", "pass"))
        assert get_admin_credentials() == ("admin", "pass")
        set_admin_credentials(None)  # cleanup

    def test_default_is_none(self):
        set_admin_credentials(None)
        assert get_admin_credentials() is None


class TestUserInAllowedGroup:
    def test_returns_true_for_sudo_user(self, mocker):
        user = SafeUsername.trusted("testuser", "test")
        grp_sudo = types.SimpleNamespace(gr_name="sudo", gr_mem=["testuser"])
        grp_other = types.SimpleNamespace(gr_name="other", gr_mem=[])
        mocker.patch("quadletman.security.auth.grp.getgrall", return_value=[grp_sudo, grp_other])
        pw = types.SimpleNamespace(pw_gid=1000)
        mocker.patch("quadletman.security.auth.pwd.getpwnam", return_value=pw)
        primary = types.SimpleNamespace(gr_name="testuser")
        mocker.patch("quadletman.security.auth.grp.getgrgid", return_value=primary)
        assert _user_in_allowed_group(user) is True

    def test_returns_false_for_non_member(self, mocker):
        user = SafeUsername.trusted("nobody", "test")
        grp_sudo = types.SimpleNamespace(gr_name="sudo", gr_mem=["admin"])
        mocker.patch("quadletman.security.auth.grp.getgrall", return_value=[grp_sudo])
        pw = types.SimpleNamespace(pw_gid=65534)
        mocker.patch("quadletman.security.auth.pwd.getpwnam", return_value=pw)
        primary = types.SimpleNamespace(gr_name="nogroup")
        mocker.patch("quadletman.security.auth.grp.getgrgid", return_value=primary)
        assert _user_in_allowed_group(user) is False

    def test_returns_false_on_key_error(self, mocker):
        user = SafeUsername.trusted("ghost", "test")
        mocker.patch("quadletman.security.auth.grp.getgrall", return_value=[])
        mocker.patch("quadletman.security.auth.pwd.getpwnam", side_effect=KeyError)
        assert _user_in_allowed_group(user) is False

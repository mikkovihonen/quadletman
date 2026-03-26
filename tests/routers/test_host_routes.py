"""Tests for /api/events, /api/host-settings, /api/selinux-booleans, and registry routes."""

import pytest

from quadletman.models import CompartmentCreate
from quadletman.models.sanitized import SafeSlug, SafeStr
from quadletman.models.service import BooleanEntry, SysctlEntry
from quadletman.services import compartment_manager


def _sid(s: str) -> SafeSlug:
    return SafeSlug.trusted(s, "test")


def _s(s: str) -> SafeStr:
    return SafeStr.trusted(s, "test")


@pytest.fixture(autouse=True)
def mock_system_calls(mocker):
    mocker.patch("quadletman.services.compartment_manager._setup_service_user")
    mocker.patch("quadletman.services.compartment_manager._teardown_service")
    mocker.patch("quadletman.services.compartment_manager._write_and_reload")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.daemon_reload")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_container_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_container_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_network_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_pod_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_volume_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_image_unit")
    mocker.patch("quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.compartment_manager.user_manager.sync_helper_users")
    mocker.patch("quadletman.services.compartment_manager.user_manager._setup_subuid_subgid")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.create_volume_dir")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.chown_volume_dir")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.delete_volume_dir")
    mocker.patch(
        "quadletman.services.compartment_manager.get_status",
        return_value={"service_id": "x", "containers": []},
    )
    mocker.patch(
        "quadletman.routers.helpers.common.user_manager.get_user_info",
        return_value={"uid": 1001, "home": "/home/qm-test"},
    )
    mocker.patch(
        "quadletman.routers.helpers.common.user_manager.list_helper_users", return_value=[]
    )
    # Host-route specific mocks
    mocker.patch("quadletman.routers.host.user_manager.list_registry_logins", return_value=[])
    mocker.patch("quadletman.routers.host.user_manager.registry_login")
    mocker.patch("quadletman.routers.host.user_manager.registry_logout")
    mocker.patch(
        "quadletman.routers.helpers.host.read_journalctl_lines", return_value=["journal line 1"]
    )
    mocker.patch("quadletman.routers.helpers.host.read_audit_lines", return_value=["audit line 1"])


async def _make_compartment(db, comp_id="htest"):
    await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
    return comp_id


# ---------------------------------------------------------------------------
# Registry login / logout routes
# ---------------------------------------------------------------------------


class TestRegistryLogins:
    async def test_get_logins(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/htest/registry-logins")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_get_logins_404(self, client):
        resp = await client.get("/api/compartments/ghost/registry-logins")
        assert resp.status_code == 404

    async def test_post_login_success(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/htest/registry-login",
            data={"registry": "docker.io", "username": "user", "password": "pass"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_post_login_failure(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.host.user_manager.registry_login",
            side_effect=RuntimeError("unauthorized"),
        )
        resp = await client.post(
            "/api/compartments/htest/registry-login",
            data={"registry": "docker.io", "username": "user", "password": "pass"},
        )
        assert resp.status_code == 200  # Returns HTML with error, not HTTP error
        assert "text/html" in resp.headers["content-type"]

    async def test_post_login_404_compartment(self, client):
        resp = await client.post(
            "/api/compartments/ghost/registry-login",
            data={"registry": "docker.io", "username": "user", "password": "pass"},
        )
        assert resp.status_code == 404

    async def test_post_logout_success(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/htest/registry-logout",
            data={"registry": "docker.io"},
        )
        assert resp.status_code == 200

    async def test_post_logout_failure(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.host.user_manager.registry_logout",
            side_effect=RuntimeError("not logged in"),
        )
        resp = await client.post(
            "/api/compartments/htest/registry-logout",
            data={"registry": "docker.io"},
        )
        assert resp.status_code == 200  # Returns HTML with error

    async def test_post_logout_404_compartment(self, client):
        resp = await client.post(
            "/api/compartments/ghost/registry-logout",
            data={"registry": "docker.io"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Events routes
# ---------------------------------------------------------------------------


class TestEvents:
    async def test_list_events_empty(self, client, db):
        resp = await client.get("/api/events")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_events_htmx(self, client, db):
        resp = await client.get("/api/events", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_events_systemd(self, client):
        resp = await client.get("/api/events/systemd")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_events_audit(self, client):
        resp = await client.get("/api/events/audit")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Host settings routes
# ---------------------------------------------------------------------------


class TestHostSettings:
    async def test_get_host_settings(self, client, mocker):
        entry = SysctlEntry(
            key=_s("net.ipv4.ip_forward"),
            category=_s("Network"),
            description=_s("Enable IP forwarding"),
            value=_s("1"),
            value_type=_s("boolean"),
            min_val=0,
            max_val=1,
            value_parts=[],
        )
        mocker.patch("quadletman.routers.host.host_settings.read_all", return_value=[entry])
        resp = await client.get("/api/host-settings")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["key"] == "net.ipv4.ip_forward"

    async def test_set_host_setting_success(self, client, mocker):
        mocker.patch("quadletman.routers.host.host_settings.apply")
        resp = await client.post(
            "/api/host-settings",
            json={"key": "net.ipv4.ip_forward", "value": "1"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_set_host_setting_invalid(self, client, mocker):
        mocker.patch(
            "quadletman.routers.host.host_settings.apply",
            side_effect=ValueError("Invalid value"),
        )
        resp = await client.post(
            "/api/host-settings",
            json={"key": "net.ipv4.ip_forward", "value": "bad"},
        )
        assert resp.status_code == 400

    async def test_set_host_setting_runtime_error(self, client, mocker):
        mocker.patch(
            "quadletman.routers.host.host_settings.apply",
            side_effect=RuntimeError("sysctl failed"),
        )
        resp = await client.post(
            "/api/host-settings",
            json={"key": "net.ipv4.ip_forward", "value": "1"},
        )
        assert resp.status_code == 500


class TestHostSettingsPartial:
    async def test_returns_html(self, client, mocker):
        mocker.patch("quadletman.routers.host.host_settings.read_all", return_value=[])
        resp = await client.get("/api/host-settings-partial")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# SELinux booleans routes
# ---------------------------------------------------------------------------


class TestSELinuxBooleans:
    async def test_partial_active(self, client, mocker):
        entry = BooleanEntry(
            name=_s("container_use_cephfs"),
            category=_s("Container Filesystem"),
            description=_s("Allow container use cephfs"),
            enabled=True,
        )
        mocker.patch("quadletman.routers.host.selinux_booleans.read_all", return_value=[entry])
        resp = await client.get("/api/selinux-booleans-partial")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_partial_inactive(self, client, mocker):
        mocker.patch("quadletman.routers.host.selinux_booleans.read_all", return_value=None)
        resp = await client.get("/api/selinux-booleans-partial")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_set_boolean_success(self, client, mocker):
        mocker.patch("quadletman.routers.host.selinux_booleans.set_boolean")
        resp = await client.post(
            "/api/selinux-booleans",
            json={"name": "container_use_cephfs", "enabled": True},
        )
        assert resp.status_code == 200

    async def test_set_boolean_invalid(self, client, mocker):
        mocker.patch(
            "quadletman.routers.host.selinux_booleans.set_boolean",
            side_effect=ValueError("invalid"),
        )
        resp = await client.post(
            "/api/selinux-booleans",
            json={"name": "bad", "enabled": True},
        )
        assert resp.status_code == 400

    async def test_set_boolean_runtime_error(self, client, mocker):
        mocker.patch(
            "quadletman.routers.host.selinux_booleans.set_boolean",
            side_effect=RuntimeError("setsebool failed"),
        )
        resp = await client.post(
            "/api/selinux-booleans",
            json={"name": "container_use_cephfs", "enabled": True},
        )
        assert resp.status_code == 500

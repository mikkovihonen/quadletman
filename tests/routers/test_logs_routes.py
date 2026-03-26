"""Tests for /api/podman-info, /api/podman-features, journal/agent routes."""

import types

import pytest

from quadletman.models import CompartmentCreate
from quadletman.models.sanitized import SafeSlug
from quadletman.services import compartment_manager


def _sid(s: str) -> SafeSlug:
    return SafeSlug.trusted(s, "test")


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
        "quadletman.routers.helpers.common.user_manager.user_exists",
        return_value=True,
    )
    mocker.patch(
        "quadletman.routers.helpers.common.user_manager.list_helper_users", return_value=[]
    )
    # Logs-route specific mocks
    mocker.patch(
        "quadletman.routers.logs.get_podman_info",
        return_value={"host": {"os": "linux"}, "version": {"Version": "5.3.0"}},
    )
    features = types.SimpleNamespace(
        version=(5, 3, 0),
        version_str="5.3.0",
        pasta=True,
        slirp4netns=False,
        quadlet=True,
        image_units=True,
        pod_units=True,
        build_units=True,
        quadlet_cli=True,
        artifact_units=True,
        bundle=True,
    )
    mocker.patch("quadletman.routers.logs.get_features", return_value=features)
    mocker.patch(
        "quadletman.routers.logs.user_manager.get_compartment_podman_info",
        return_value={"host": {"os": "linux"}},
    )
    mocker.patch("quadletman.routers.logs.systemd_manager.ensure_agent_unit")
    mocker.patch("quadletman.routers.logs.systemd_manager.daemon_reload")
    mocker.patch("quadletman.routers.logs.systemd_manager.restart_unit")


async def _make_compartment(db, comp_id="ltest"):
    await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
    return comp_id


# ---------------------------------------------------------------------------
# Podman info routes
# ---------------------------------------------------------------------------


class TestPodmanInfoRoot:
    async def test_returns_info(self, client):
        resp = await client.get("/api/podman-info")
        assert resp.status_code == 200
        assert resp.json()["host"]["os"] == "linux"


class TestPodmanInfoCompartment:
    async def test_returns_info(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/ltest/podman-info")
        assert resp.status_code == 200

    async def test_returns_404_missing(self, client, db):
        resp = await client.get("/api/compartments/ghost/podman-info")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Podman features partial
# ---------------------------------------------------------------------------


class TestPodmanFeaturesPartial:
    async def test_returns_html(self, client):
        resp = await client.get("/api/podman-features")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Agent restart
# ---------------------------------------------------------------------------


class TestRestartAgent:
    async def test_restarts_agent(self, client, db):
        await _make_compartment(db)
        resp = await client.post("/api/compartments/ltest/agent/restart")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_returns_404_missing(self, client, db):
        resp = await client.post("/api/compartments/ghost/agent/restart")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Stream endpoint 404s (compartment not found)
# ---------------------------------------------------------------------------


class TestStreamEndpoints404:
    async def test_journal_404(self, client, db):
        resp = await client.get("/api/compartments/ghost/journal")
        assert resp.status_code == 404

    async def test_agent_logs_404(self, client, db):
        resp = await client.get("/api/compartments/ghost/agent/logs")
        assert resp.status_code == 404

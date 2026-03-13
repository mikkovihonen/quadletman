"""Tests for quadletman/routers/api.py — REST + HTMX routes."""

import pytest

from quadletman.models import ServiceCreate
from quadletman.services import service_manager


@pytest.fixture(autouse=True)
def mock_system_calls(mocker):
    """Suppress all real OS/subprocess calls for every test in this module."""
    mocker.patch("quadletman.services.service_manager._setup_service_user")
    mocker.patch("quadletman.services.service_manager._teardown_service")
    mocker.patch("quadletman.services.service_manager._write_and_reload")
    mocker.patch("quadletman.services.service_manager.systemd_manager.start_unit")
    mocker.patch("quadletman.services.service_manager.systemd_manager.stop_unit")
    mocker.patch("quadletman.services.service_manager.systemd_manager.restart_unit")
    mocker.patch("quadletman.services.service_manager.systemd_manager.daemon_reload")
    mocker.patch("quadletman.services.service_manager.quadlet_writer.remove_container_unit")
    mocker.patch("quadletman.services.service_manager.quadlet_writer.remove_network_unit")
    mocker.patch("quadletman.services.service_manager.volume_manager.delete_volume_dir")
    mocker.patch("quadletman.services.service_manager.volume_manager.create_volume_dir")
    mocker.patch("quadletman.services.service_manager.volume_manager.chown_volume_dir")
    mocker.patch("quadletman.services.service_manager.user_manager.sync_helper_users")
    mocker.patch("quadletman.services.service_manager.user_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.service_manager.user_manager._setup_subuid_subgid")
    mocker.patch("quadletman.services.user_manager.get_uid", return_value=1001)
    mocker.patch(
        "quadletman.services.service_manager.get_status",
        return_value={"service_id": "x", "containers": []},
    )
    mocker.patch(
        "quadletman.routers.api.user_manager.get_user_info",
        return_value={"uid": 1001, "home": "/home/qm-test"},
    )
    mocker.patch("quadletman.routers.api.user_manager.list_helper_users", return_value=[])


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    async def test_dashboard_returns_200(self, client):
        resp = await client.get("/api/dashboard")
        assert resp.status_code == 200

    async def test_dashboard_returns_html(self, client):
        resp = await client.get("/api/dashboard")
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Service CRUD
# ---------------------------------------------------------------------------


class TestListServices:
    async def test_empty_list(self, client):
        resp = await client.get("/api/services")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    async def test_returns_created_service(self, client, db):
        await service_manager.create_service(db, ServiceCreate(id="mysvc", display_name="My Svc"))
        resp = await client.get("/api/services")
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()]
        assert "mysvc" in ids


class TestCreateService:
    async def test_creates_service(self, client):
        resp = await client.post(
            "/api/services",
            json={"id": "newsvc", "display_name": "New Service"},
        )
        assert resp.status_code == 201

    async def test_rejects_invalid_id(self, client):
        resp = await client.post(
            "/api/services",
            json={"id": "Invalid_ID", "display_name": "Bad"},
        )
        assert resp.status_code == 422

    async def test_rejects_qm_prefix(self, client):
        resp = await client.post(
            "/api/services",
            json={"id": "qm-foo", "display_name": "Bad"},
        )
        assert resp.status_code == 422


class TestGetService:
    async def test_returns_404_for_missing(self, client):
        resp = await client.get("/api/services/nonexistent")
        assert resp.status_code == 404

    async def test_returns_service(self, client, db):
        await service_manager.create_service(db, ServiceCreate(id="svc1", display_name="S1"))
        resp = await client.get("/api/services/svc1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "svc1"


class TestDeleteService:
    async def test_deletes_service(self, client, db):
        await service_manager.create_service(db, ServiceCreate(id="todel", display_name="Del"))
        resp = await client.delete("/api/services/todel")
        assert resp.status_code in (200, 204)
        # Gone from DB
        assert await service_manager.get_service(db, "todel") is None

    async def test_returns_404_for_missing(self, client):
        resp = await client.delete("/api/services/ghost")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# HTMX vs JSON paths
# ---------------------------------------------------------------------------


class TestHtmxVsJson:
    async def test_services_list_returns_json_by_default(self, client):
        resp = await client.get("/api/services")
        assert resp.headers["content-type"].startswith("application/json")

    async def test_service_detail_returns_html_for_htmx(self, client, db):
        await service_manager.create_service(db, ServiceCreate(id="htmxsvc", display_name="H"))
        resp = await client.get(
            "/api/services/htmxsvc",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Container routes
# ---------------------------------------------------------------------------


class TestContainerRoutes:
    async def test_add_container(self, client, db):
        await service_manager.create_service(db, ServiceCreate(id="csvc", display_name="C"))
        resp = await client.post(
            "/api/services/csvc/containers",
            json={"name": "web", "image": "nginx:latest"},
        )
        assert resp.status_code == 201

    async def test_container_invalid_name_rejected(self, client, db):
        await service_manager.create_service(db, ServiceCreate(id="csvc2", display_name="C"))
        resp = await client.post(
            "/api/services/csvc2/containers",
            json={"name": "Web_Container!", "image": "nginx"},
        )
        assert resp.status_code == 422

    async def test_delete_container_idempotent(self, client, db):
        # The delete endpoint is idempotent — deleting a non-existent container is a no-op
        await service_manager.create_service(db, ServiceCreate(id="csvc3", display_name="C"))
        resp = await client.delete("/api/services/csvc3/containers/nonexistent-id")
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Lifecycle routes
# ---------------------------------------------------------------------------


class TestLifecycleRoutes:
    async def _make_service_with_container(self, client, db, svc_id: str):
        await service_manager.create_service(db, ServiceCreate(id=svc_id, display_name="S"))
        await service_manager.add_container(
            db,
            svc_id,
            __import__("quadletman.models", fromlist=["ContainerCreate"]).ContainerCreate(
                name="web", image="nginx"
            ),
        )

    async def test_start_calls_start_unit(self, client, db, mocker):
        from quadletman.models import ContainerCreate

        start_mock = mocker.patch("quadletman.services.service_manager.systemd_manager.start_unit")
        await service_manager.create_service(db, ServiceCreate(id="lifesvc", display_name="L"))
        await service_manager.add_container(db, "lifesvc", ContainerCreate(name="web", image="ng"))
        resp = await client.post("/api/services/lifesvc/start")
        assert resp.status_code == 200
        start_mock.assert_called()

    async def test_stop_returns_200(self, client, db, mocker):
        from quadletman.models import ContainerCreate

        mocker.patch("quadletman.services.service_manager.systemd_manager.stop_unit")
        await service_manager.create_service(db, ServiceCreate(id="stopsvc", display_name="L"))
        await service_manager.add_container(db, "stopsvc", ContainerCreate(name="web", image="ng"))
        resp = await client.post("/api/services/stopsvc/stop")
        assert resp.status_code == 200

    async def test_delete_service_404_for_unknown(self, client):
        resp = await client.delete("/api/services/ghost")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


class TestLogout:
    async def test_logout_clears_cookie(self, client):
        resp = await client.post("/api/logout")
        assert resp.status_code == 204
        assert "qm_session" in resp.headers.get("set-cookie", "")

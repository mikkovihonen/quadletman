"""Tests for quadletman/routers/api.py — REST + HTMX routes."""

import os

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from quadletman.models import CompartmentCreate
from quadletman.services import compartment_manager


@pytest.fixture(autouse=True)
def mock_system_calls(mocker):
    """Suppress all real OS/subprocess calls for every test in this module."""
    mocker.patch("quadletman.services.compartment_manager._setup_service_user")
    mocker.patch("quadletman.services.compartment_manager._teardown_service")
    mocker.patch("quadletman.services.compartment_manager._write_and_reload")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.start_unit")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.stop_unit")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.restart_unit")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.daemon_reload")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_container_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_image_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_network_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_pod_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_volume_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_container_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_network_unit")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.delete_volume_dir")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.create_volume_dir")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.chown_volume_dir")
    mocker.patch("quadletman.services.compartment_manager.user_manager.sync_helper_users")
    mocker.patch("quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.compartment_manager.user_manager._setup_subuid_subgid")
    mocker.patch("quadletman.services.user_manager.get_uid", return_value=1001)
    mocker.patch(
        "quadletman.services.compartment_manager.get_status",
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


class TestListCompartments:
    async def test_empty_list(self, client):
        resp = await client.get("/api/compartments")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    async def test_returns_created_service(self, client, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="mysvc"))
        resp = await client.get("/api/compartments")
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()]
        assert "mysvc" in ids


class TestCreateCompartment:
    async def test_creates_service(self, client):
        resp = await client.post(
            "/api/compartments",
            json={"id": "newsvc"},
        )
        assert resp.status_code == 201

    async def test_rejects_invalid_id(self, client):
        resp = await client.post(
            "/api/compartments",
            json={"id": "Invalid_ID"},
        )
        assert resp.status_code == 422

    async def test_rejects_qm_prefix(self, client):
        resp = await client.post(
            "/api/compartments",
            json={"id": "qm-foo"},
        )
        assert resp.status_code == 422


class TestGetCompartment:
    async def test_returns_404_for_missing(self, client):
        resp = await client.get("/api/compartments/nonexistent")
        assert resp.status_code == 404

    async def test_returns_service(self, client, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="svc1"))
        resp = await client.get("/api/compartments/svc1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "svc1"


class TestDeleteCompartment:
    async def test_deletes_service(self, client, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="todel"))
        resp = await client.delete("/api/compartments/todel")
        assert resp.status_code in (200, 204)
        # Gone from DB
        assert await compartment_manager.get_compartment(db, "todel") is None

    async def test_returns_404_for_missing(self, client):
        resp = await client.delete("/api/compartments/ghost")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# HTMX vs JSON paths
# ---------------------------------------------------------------------------


class TestHtmxVsJson:
    async def test_services_list_returns_json_by_default(self, client):
        resp = await client.get("/api/compartments")
        assert resp.headers["content-type"].startswith("application/json")

    async def test_service_detail_returns_html_for_htmx(self, client, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="htmxsvc"))
        resp = await client.get(
            "/api/compartments/htmxsvc",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Container routes
# ---------------------------------------------------------------------------


class TestContainerRoutes:
    async def test_add_container(self, client, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="csvc"))
        resp = await client.post(
            "/api/compartments/csvc/containers",
            json={"name": "web", "image": "nginx:latest"},
        )
        assert resp.status_code == 201

    async def test_container_invalid_name_rejected(self, client, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="csvc2"))
        resp = await client.post(
            "/api/compartments/csvc2/containers",
            json={"name": "Web_Container!", "image": "nginx"},
        )
        assert resp.status_code == 422

    async def test_delete_container_idempotent(self, client, db):
        # The delete endpoint is idempotent — deleting a non-existent container is a no-op
        await compartment_manager.create_compartment(db, CompartmentCreate(id="csvc3"))
        resp = await client.delete("/api/compartments/csvc3/containers/nonexistent-id")
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Lifecycle routes
# ---------------------------------------------------------------------------


class TestLifecycleRoutes:
    async def _make_service_with_container(self, client, db, svc_id: str):
        await compartment_manager.create_compartment(db, CompartmentCreate(id=svc_id))
        await compartment_manager.add_container(
            db,
            svc_id,
            __import__("quadletman.models", fromlist=["ContainerCreate"]).ContainerCreate(
                name="web", image="nginx"
            ),
        )

    async def test_start_calls_start_unit(self, client, db, mocker):
        from quadletman.models import ContainerCreate

        start_mock = mocker.patch(
            "quadletman.services.compartment_manager.systemd_manager.start_unit"
        )
        await compartment_manager.create_compartment(db, CompartmentCreate(id="lifesvc"))
        await compartment_manager.add_container(
            db, "lifesvc", ContainerCreate(name="web", image="ng")
        )
        resp = await client.post("/api/compartments/lifesvc/start")
        assert resp.status_code == 200
        start_mock.assert_called()

    async def test_stop_returns_200(self, client, db, mocker):
        from quadletman.models import ContainerCreate

        mocker.patch("quadletman.services.compartment_manager.systemd_manager.stop_unit")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="stopsvc"))
        await compartment_manager.add_container(
            db, "stopsvc", ContainerCreate(name="web", image="ng")
        )
        resp = await client.post("/api/compartments/stopsvc/stop")
        assert resp.status_code == 200

    async def test_delete_service_404_for_unknown(self, client):
        resp = await client.delete("/api/compartments/ghost")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


class TestLogout:
    async def test_logout_clears_cookie(self, client):
        resp = await client.post("/api/logout")
        assert resp.status_code == 204
        assert "qm_session" in resp.headers.get("set-cookie", "")


# ---------------------------------------------------------------------------
# Terminal WebSocket
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_client(mocker):
    """Synchronous TestClient for WebSocket testing, with auth and DB overridden."""
    import aiosqlite

    from quadletman.auth import require_auth
    from quadletman.database import get_db
    from quadletman.main import app

    async def _get_db():
        async with aiosqlite.connect(":memory:") as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[require_auth] = lambda: "testuser"
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


_WS_HEADERS = {"origin": "http://testserver"}  # matches Starlette TestClient default host


class TestContainerTerminal:
    def test_cross_origin_connection_rejected(self, sync_client, mocker):
        """WebSocket from a foreign origin must be closed with code 4403."""
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            sync_client.websocket_connect(
                "/api/compartments/svc/containers/web/terminal",
                headers={"origin": "http://evil.example.com"},
            ),
        ):
            pass
        assert exc_info.value.code == 4403

    def test_unauthenticated_connection_rejected(self, sync_client, mocker):
        """WebSocket without a valid session cookie must be closed with code 4401."""
        mocker.patch("quadletman.routers.api.get_session", return_value=None)
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            sync_client.websocket_connect(
                "/api/compartments/svc/containers/web/terminal",
                headers=_WS_HEADERS,
                cookies={"qm_session": "bad-token"},
            ),
        ):
            pass
        assert exc_info.value.code == 4401

    def test_invalid_exec_user_rejected(self, sync_client, mocker):
        """WebSocket with an invalid exec_user query param must be closed with code 4400."""
        mocker.patch("quadletman.routers.api.get_session", return_value="testuser")
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            sync_client.websocket_connect(
                "/api/compartments/svc/containers/web/terminal?exec_user=;rm+-rf+/",
                headers=_WS_HEADERS,
                cookies={"qm_session": "valid-token"},
            ),
        ):
            pass
        assert exc_info.value.code == 4400

    def test_exec_pty_launched_on_valid_auth(self, sync_client, mocker):
        """Authenticated WebSocket should spawn exec_pty_cmd and stream output."""
        mocker.patch("quadletman.routers.api.get_session", return_value="testuser")
        mocker.patch(
            "quadletman.routers.api.systemd_manager.exec_pty_cmd",
            return_value=["echo", "hello"],
        )
        # Use a pipe pair to simulate the PTY master/slave
        r_fd, w_fd = os.pipe()
        os.write(w_fd, b"$ ")
        os.close(w_fd)

        mocker.patch("quadletman.routers.api.pty.openpty", return_value=(r_fd, r_fd + 1))
        mock_proc = mocker.MagicMock()
        mock_proc.kill = mocker.MagicMock()
        mock_proc.wait = mocker.MagicMock()
        mocker.patch("quadletman.routers.api.subprocess.Popen", return_value=mock_proc)
        mocker.patch("quadletman.routers.api.os.close")

        with sync_client.websocket_connect(
            "/api/compartments/svc/containers/web/terminal",
            headers=_WS_HEADERS,
            cookies={"qm_session": "valid-token"},
        ) as ws:
            data = ws.receive_bytes()
            assert data == b"$ "

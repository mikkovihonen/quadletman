"""Tests for quadletman/routers/api.py — REST + HTMX routes."""

import os

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from quadletman.models import CompartmentCreate
from quadletman.models.sanitized import SafeSlug
from quadletman.routers._helpers import _fmt_bytes, _status_dot_context
from quadletman.services import compartment_manager


def _sid(s: str) -> SafeSlug:
    return SafeSlug.trusted(s, "test")


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
        "quadletman.routers._helpers.user_manager.get_user_info",
        return_value={"uid": 1001, "home": "/home/qm-test"},
    )
    mocker.patch("quadletman.routers._helpers.user_manager.list_helper_users", return_value=[])


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
        await compartment_manager.create_compartment(db, CompartmentCreate(id="mycomp"))
        resp = await client.get("/api/compartments")
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()]
        assert "mycomp" in ids


class TestCreateCompartment:
    async def test_creates_service(self, client):
        resp = await client.post(
            "/api/compartments",
            json={"id": "newcomp"},
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
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp1"))
        resp = await client.get("/api/compartments/comp1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "comp1"


class TestDeleteCompartment:
    async def test_deletes_service(self, client, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="todel"))
        resp = await client.delete("/api/compartments/todel")
        assert resp.status_code in (200, 204)
        # Gone from DB
        assert await compartment_manager.get_compartment(db, _sid("todel")) is None

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
        await compartment_manager.create_compartment(db, CompartmentCreate(id="htmxcomp"))
        resp = await client.get(
            "/api/compartments/htmxcomp",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Container routes
# ---------------------------------------------------------------------------


class TestContainerRoutes:
    async def test_add_container(self, client, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="ccomp"))
        resp = await client.post(
            "/api/compartments/ccomp/containers",
            json={"name": "web", "image": "nginx:latest"},
        )
        assert resp.status_code == 201

    async def test_container_invalid_name_rejected(self, client, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="ccomp2"))
        resp = await client.post(
            "/api/compartments/ccomp2/containers",
            json={"name": "Web_Container!", "image": "nginx"},
        )
        assert resp.status_code == 422

    async def test_delete_container_idempotent(self, client, db):
        # The delete endpoint is idempotent — deleting a non-existent container is a no-op
        await compartment_manager.create_compartment(db, CompartmentCreate(id="ccomp3"))
        resp = await client.delete("/api/compartments/ccomp3/containers/nonexistent-id")
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Lifecycle routes
# ---------------------------------------------------------------------------


class TestLifecycleRoutes:
    async def _make_service_with_container(self, client, db, comp_id: str):
        await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
        await compartment_manager.add_container(
            db,
            _sid(comp_id),
            __import__("quadletman.models", fromlist=["ContainerCreate"]).ContainerCreate(
                name="web", image="nginx"
            ),
        )

    async def test_start_calls_start_unit(self, client, db, mocker):
        from quadletman.models import ContainerCreate

        start_mock = mocker.patch(
            "quadletman.services.compartment_manager.systemd_manager.start_unit"
        )
        await compartment_manager.create_compartment(db, CompartmentCreate(id="lifecomp"))
        await compartment_manager.add_container(
            db, _sid("lifecomp"), ContainerCreate(name="web", image="ng")
        )
        resp = await client.post("/api/compartments/lifecomp/start")
        assert resp.status_code == 200
        start_mock.assert_called()

    async def test_stop_returns_200(self, client, db, mocker):
        from quadletman.models import ContainerCreate

        mocker.patch("quadletman.services.compartment_manager.systemd_manager.stop_unit")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="stopcomp"))
        await compartment_manager.add_container(
            db, _sid("stopcomp"), ContainerCreate(name="web", image="ng")
        )
        resp = await client.post("/api/compartments/stopcomp/stop")
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


class TestHelp:
    async def test_help_returns_200(self, client):
        resp = await client.get("/api/help")
        assert resp.status_code == 200

    async def test_help_returns_html(self, client):
        resp = await client.get("/api/help")
        assert "text/html" in resp.headers["content-type"]


class TestDbBackup:
    async def test_backup_returns_file(self, client, mocker, tmp_path):
        import asyncio
        from unittest.mock import MagicMock

        ts = "20240101T000000Z"
        db_file = tmp_path / f"quadletman-backup-{ts}.db"
        db_file.write_bytes(b"SQLite format 3")

        # Patch mkdtemp to return tmp_path (secure temp directory)
        mocker.patch("quadletman.routers.api.tempfile.mkdtemp", return_value=str(tmp_path))

        # Fix the timestamp so the constructed filename is predictable
        mock_now = MagicMock()
        mock_now.strftime.return_value = ts
        mock_datetime = mocker.patch("quadletman.routers.api.datetime")
        mock_datetime.now.return_value = mock_now

        # Capture the real loop and wrap run_in_executor to be a no-op
        real_loop = asyncio.get_event_loop()

        async def fake_executor(exc, fn):
            pass  # skip actual sqlite3 VACUUM — db_file already written above

        mocker.patch.object(real_loop, "run_in_executor", side_effect=fake_executor)

        resp = await client.get("/api/backup/db")
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("content-disposition", "")


# ---------------------------------------------------------------------------
# Terminal WebSocket
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_client(mocker):
    """Synchronous TestClient for WebSocket testing, with auth and DB overridden."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from quadletman.auth import require_auth
    from quadletman.db.engine import get_db
    from quadletman.db.orm import Base
    from quadletman.main import app

    _engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    _factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    async def _setup():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_setup())

    async def _get_db():
        async with _factory() as session:
            yield session

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
                "/api/compartments/comp/containers/web/terminal",
                headers={"origin": "http://evil.example.com"},
            ),
        ):
            pass
        assert exc_info.value.code == 4403

    def test_unauthenticated_connection_rejected(self, sync_client, mocker):
        """WebSocket without a valid session cookie must be closed with code 4401."""
        mocker.patch("quadletman.routers.logs.get_session", return_value=None)
        sync_client.cookies.set("qm_session", "bad-token")
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            sync_client.websocket_connect(
                "/api/compartments/comp/containers/web/terminal",
                headers=_WS_HEADERS,
            ),
        ):
            pass
        assert exc_info.value.code == 4401

    def test_invalid_exec_user_rejected(self, sync_client, mocker):
        """WebSocket with an invalid exec_user query param must be rejected.

        FastAPI validates the Query pattern and closes with 1008 (policy violation)
        before our application code runs; both 1008 and 4400 represent rejection.
        """
        mocker.patch("quadletman.routers.logs.get_session", return_value="testuser")
        sync_client.cookies.set("qm_session", "valid-token")
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            sync_client.websocket_connect(
                "/api/compartments/comp/containers/web/terminal?exec_user=;rm+-rf+/",
                headers=_WS_HEADERS,
            ),
        ):
            pass
        assert exc_info.value.code in (1008, 4400)

    def test_exec_pty_launched_on_valid_auth(self, sync_client, mocker):
        """Authenticated WebSocket should spawn exec_pty_cmd and stream output."""
        mocker.patch("quadletman.routers.logs.get_session", return_value="testuser")
        mocker.patch(
            "quadletman.routers.logs.systemd_manager.exec_pty_cmd",
            return_value=["echo", "hello"],
        )
        # Use a pipe pair to simulate the PTY master/slave
        r_fd, w_fd = os.pipe()
        os.write(w_fd, b"$ ")
        os.close(w_fd)

        mocker.patch("quadletman.routers.logs.pty.openpty", return_value=(r_fd, r_fd + 1))
        mock_proc = mocker.MagicMock()
        mock_proc.kill = mocker.MagicMock()
        mock_proc.wait = mocker.MagicMock()
        mocker.patch("quadletman.routers.logs.subprocess.Popen", return_value=mock_proc)
        mocker.patch("quadletman.routers.logs.os.close")

        sync_client.cookies.set("qm_session", "valid-token")
        with sync_client.websocket_connect(
            "/api/compartments/comp/containers/web/terminal",
            headers=_WS_HEADERS,
        ) as ws:
            data = ws.receive_bytes()
            assert data == b"$ "


# ---------------------------------------------------------------------------
# _status_dot_context — color/title logic branches
# ---------------------------------------------------------------------------


class TestStatusDotContext:
    def test_no_units_is_gray(self):
        ctx = _status_dot_context(SafeSlug.trusted("c", "t"), [])
        assert ctx["color"] == "bg-gray-600"
        assert ctx["title"] == "no units"

    def test_failed_is_red(self):
        statuses = [{"active_state": "failed"}]
        ctx = _status_dot_context(SafeSlug.trusted("c", "t"), statuses)
        assert ctx["color"] == "bg-red-500"
        assert "failed" in ctx["title"]

    def test_transitioning_is_yellow_pulse(self):
        statuses = [{"active_state": "activating"}]
        ctx = _status_dot_context(SafeSlug.trusted("c", "t"), statuses)
        assert "animate-pulse" in ctx["color"]
        assert ctx["title"] == "transitioning"

    def test_all_active_is_green(self):
        statuses = [{"active_state": "active"}, {"active_state": "active"}]
        ctx = _status_dot_context(SafeSlug.trusted("c", "t"), statuses)
        assert ctx["color"] == "bg-green-500"
        assert ctx["title"] == "all running"

    def test_partial_active_is_yellow(self):
        statuses = [{"active_state": "active"}, {"active_state": "inactive"}]
        ctx = _status_dot_context(SafeSlug.trusted("c", "t"), statuses)
        assert ctx["color"] == "bg-yellow-500"
        assert "1/2" in ctx["title"]

    def test_all_stopped_is_gray_stopped(self):
        statuses = [{"active_state": "inactive"}, {"active_state": "inactive"}]
        ctx = _status_dot_context(SafeSlug.trusted("c", "t"), statuses)
        assert ctx["color"] == "bg-gray-500"
        assert ctx["title"] == "stopped"

    def test_oob_false_by_default(self):
        ctx = _status_dot_context(SafeSlug.trusted("c", "t"), [])
        assert ctx["oob"] is False

    def test_oob_true_passed_through(self):
        ctx = _status_dot_context(SafeSlug.trusted("c", "t"), [], oob=True)
        assert ctx["oob"] is True


# ---------------------------------------------------------------------------
# _fmt_bytes — formatting branches
# ---------------------------------------------------------------------------


class TestFmtBytesHelpers:
    def test_bytes(self):
        assert _fmt_bytes(500) == "500 B"

    def test_kilobytes(self):
        assert _fmt_bytes(1024) == "1.0 KB"

    def test_megabytes(self):
        assert _fmt_bytes(1024 * 1024) == "1.0 MB"

    def test_gigabytes(self):
        assert _fmt_bytes(1024**3) == "1.0 GB"

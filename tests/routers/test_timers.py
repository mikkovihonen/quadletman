"""Tests for /api/compartments/{id}/timers routes."""

import pytest

from quadletman.models import CompartmentCreate, ContainerCreate
from quadletman.services import compartment_manager


@pytest.fixture(autouse=True)
def mock_system_calls(mocker):
    mocker.patch("quadletman.services.compartment_manager._setup_service_user")
    mocker.patch("quadletman.services.compartment_manager._teardown_service")
    mocker.patch("quadletman.services.compartment_manager._write_and_reload")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.daemon_reload")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_container_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_timer_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_timer_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_network_unit")
    mocker.patch("quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.compartment_manager.user_manager.sync_helper_users")
    mocker.patch("quadletman.services.compartment_manager.user_manager._setup_subuid_subgid")
    mocker.patch(
        "quadletman.services.compartment_manager.user_manager.user_exists", return_value=False
    )
    mocker.patch("quadletman.services.compartment_manager.volume_manager.create_volume_dir")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.chown_volume_dir")
    mocker.patch(
        "quadletman.services.compartment_manager.get_status",
        return_value={"service_id": "x", "containers": []},
    )
    mocker.patch(
        "quadletman.routers._helpers.user_manager.get_user_info",
        return_value={"uid": 1001, "home": "/home/qm-test"},
    )
    mocker.patch("quadletman.routers._helpers.user_manager.list_helper_users", return_value=[])


async def _make_compartment_with_container(db, comp_id="tcomp"):
    await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
    container = await compartment_manager.add_container(
        db, comp_id, ContainerCreate(name="web", image="nginx:latest")
    )
    return comp_id, container.id


class TestListTimers:
    async def test_empty_list(self, client, db):
        await _make_compartment_with_container(db)
        resp = await client.get("/api/compartments/tcomp/timers")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_404_for_missing_compartment(self, client):
        resp = await client.get("/api/compartments/ghost/timers")
        assert resp.status_code == 404


class TestCreateTimer:
    async def test_creates_timer_with_on_calendar(self, client, db):
        _, cid = await _make_compartment_with_container(db)
        resp = await client.post(
            "/api/compartments/tcomp/timers",
            json={"name": "daily", "container_id": cid, "on_calendar": "daily"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "daily"

    async def test_creates_timer_with_on_boot_sec(self, client, db):
        _, cid = await _make_compartment_with_container(db)
        resp = await client.post(
            "/api/compartments/tcomp/timers",
            json={"name": "boot", "container_id": cid, "on_boot_sec": "5min"},
        )
        assert resp.status_code == 201

    async def test_rejects_missing_schedule(self, client, db):
        _, cid = await _make_compartment_with_container(db)
        resp = await client.post(
            "/api/compartments/tcomp/timers",
            json={"name": "bad", "container_id": cid},
        )
        assert resp.status_code == 400

    async def test_rejects_invalid_container(self, client, db):
        await _make_compartment_with_container(db)
        resp = await client.post(
            "/api/compartments/tcomp/timers",
            json={"name": "t", "container_id": "nonexistent", "on_calendar": "daily"},
        )
        assert resp.status_code in (400, 404)

    async def test_timer_appears_in_list(self, client, db):
        _, cid = await _make_compartment_with_container(db)
        await client.post(
            "/api/compartments/tcomp/timers",
            json={"name": "hourly", "container_id": cid, "on_calendar": "hourly"},
        )
        resp = await client.get("/api/compartments/tcomp/timers")
        names = [t["name"] for t in resp.json()]
        assert "hourly" in names

    async def test_invalid_name_rejected(self, client, db):
        _, cid = await _make_compartment_with_container(db)
        resp = await client.post(
            "/api/compartments/tcomp/timers",
            json={"name": "Bad Timer!", "container_id": cid, "on_calendar": "daily"},
        )
        assert resp.status_code == 422


class TestDeleteTimer:
    async def test_deletes_timer(self, client, db):
        _, cid = await _make_compartment_with_container(db)
        create_resp = await client.post(
            "/api/compartments/tcomp/timers",
            json={"name": "delme", "container_id": cid, "on_calendar": "weekly"},
        )
        timer_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/compartments/tcomp/timers/{timer_id}")
        assert resp.status_code == 204

    async def test_delete_removes_from_list(self, client, db):
        _, cid = await _make_compartment_with_container(db)
        create_resp = await client.post(
            "/api/compartments/tcomp/timers",
            json={"name": "gone", "container_id": cid, "on_calendar": "monthly"},
        )
        timer_id = create_resp.json()["id"]
        await client.delete(f"/api/compartments/tcomp/timers/{timer_id}")
        resp = await client.get("/api/compartments/tcomp/timers")
        names = [t["name"] for t in resp.json()]
        assert "gone" not in names

    async def test_delete_nonexistent_is_no_op(self, client, db):
        await _make_compartment_with_container(db)
        resp = await client.delete("/api/compartments/tcomp/timers/nonexistent-id")
        assert resp.status_code == 204

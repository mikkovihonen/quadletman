"""Tests for /api/compartments/{id}/volumes routes."""

import pytest

from quadletman.models import CompartmentCreate
from quadletman.services import compartment_manager


@pytest.fixture(autouse=True)
def mock_system_calls(mocker):
    mocker.patch("quadletman.services.compartment_manager._setup_service_user")
    mocker.patch("quadletman.services.compartment_manager._teardown_service")
    mocker.patch("quadletman.services.compartment_manager._write_and_reload")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.daemon_reload")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_container_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_network_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_volume_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_volume_unit")
    mocker.patch("quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.compartment_manager.user_manager.sync_helper_users")
    mocker.patch("quadletman.services.compartment_manager.user_manager._setup_subuid_subgid")
    mocker.patch(
        "quadletman.services.compartment_manager.volume_manager.create_volume_dir",
        return_value="/var/lib/quadletman/volumes/volcomp/data",
    )
    mocker.patch("quadletman.services.compartment_manager.volume_manager.chown_volume_dir")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.delete_volume_dir")
    mocker.patch(
        "quadletman.services.compartment_manager.get_status",
        return_value={"service_id": "x", "containers": []},
    )
    mocker.patch(
        "quadletman.routers._helpers.user_manager.get_user_info",
        return_value={"uid": 1001, "home": "/home/qm-test"},
    )
    mocker.patch("quadletman.routers._helpers.user_manager.list_helper_users", return_value=[])
    mocker.patch(
        "quadletman.routers._helpers.user_manager.get_compartment_drivers", return_value=([], [])
    )


async def _make_compartment(db, comp_id="volcomp"):
    await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
    return comp_id


class TestListVolumes:
    async def test_compartment_has_no_volumes_initially(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/volcomp")
        assert resp.status_code == 200
        assert resp.json()["volumes"] == []

    async def test_created_volume_appears_in_compartment(self, client, db):
        await _make_compartment(db)
        await client.post("/api/compartments/volcomp/volumes", json={"name": "check"})
        resp = await client.get("/api/compartments/volcomp")
        names = [v["name"] for v in resp.json()["volumes"]]
        assert "check" in names


class TestCreateVolume:
    async def test_creates_volume(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/volcomp/volumes",
            json={"name": "mydata"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "mydata"

    async def test_volume_appears_in_compartment(self, client, db):
        await _make_compartment(db)
        await client.post("/api/compartments/volcomp/volumes", json={"name": "storage"})
        resp = await client.get("/api/compartments/volcomp")
        names = [v["name"] for v in resp.json()["volumes"]]
        assert "storage" in names

    async def test_rejects_invalid_name(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/volcomp/volumes",
            json={"name": "bad name!"},
        )
        assert resp.status_code == 422

    async def test_create_returns_404_for_missing_compartment(self, client):
        resp = await client.post(
            "/api/compartments/ghost/volumes",
            json={"name": "data"},
        )
        assert resp.status_code == 404


class TestDeleteVolume:
    async def test_deletes_volume(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/compartments/volcomp/volumes", json={"name": "del-me"}
        )
        volume_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/compartments/volcomp/volumes/{volume_id}")
        assert resp.status_code == 204

    async def test_delete_removes_from_compartment(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post("/api/compartments/volcomp/volumes", json={"name": "gone"})
        volume_id = create_resp.json()["id"]
        await client.delete(f"/api/compartments/volcomp/volumes/{volume_id}")
        resp = await client.get("/api/compartments/volcomp")
        names = [v["name"] for v in resp.json()["volumes"]]
        assert "gone" not in names

    async def test_returns_409_when_volume_mounted(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/compartments/volcomp/volumes", json={"name": "mounted"}
        )
        volume_id = create_resp.json()["id"]
        mocker.patch(
            "quadletman.routers.volumes.compartment_manager.delete_volume",
            side_effect=ValueError("Volume is in use"),
        )
        resp = await client.delete(f"/api/compartments/volcomp/volumes/{volume_id}")
        assert resp.status_code == 409


class TestVolumeForm:
    async def test_returns_html_form(self, client, db):
        await _make_compartment(db)
        resp = await client.get(
            "/api/compartments/volcomp/volumes/form",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_returns_404_for_missing_compartment(self, client):
        resp = await client.get("/api/compartments/ghost/volumes/form")
        assert resp.status_code == 404


class TestVolumeSize:
    async def test_returns_bytes_json(self, client, mocker):
        mocker.patch(
            "quadletman.services.metrics._dir_size",
            return_value=1024,
        )
        resp = await client.get("/api/compartments/volcomp/volumes/data/size")
        assert resp.status_code == 200
        assert resp.json()["bytes"] == 1024

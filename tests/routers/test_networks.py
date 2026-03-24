"""Tests for /api/compartments/{id}/networks routes."""

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
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_network_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_network_unit")
    mocker.patch("quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.compartment_manager.user_manager.sync_helper_users")
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
        "quadletman.routers.helpers.common.user_manager.get_user_info",
        return_value={"uid": 1001, "home": "/home/qm-test"},
    )
    mocker.patch(
        "quadletman.routers.helpers.common.user_manager.list_helper_users", return_value=[]
    )
    mocker.patch(
        "quadletman.routers.networks.user_manager.get_compartment_drivers",
        return_value=([], []),
    )


async def _make_compartment(db, comp_id="ncomp"):
    await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
    return comp_id


class TestCreateNetwork:
    async def test_create_returns_201(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ncomp/networks",
            data={"qm_name": "mynet", "driver": "bridge"},
        )
        assert resp.status_code == 201

    async def test_duplicate_name_returns_409(self, client, db):
        await _make_compartment(db)
        await client.post(
            "/api/compartments/ncomp/networks",
            data={"qm_name": "mynet"},
        )
        resp = await client.post(
            "/api/compartments/ncomp/networks",
            data={"qm_name": "mynet"},
        )
        assert resp.status_code == 409

    async def test_create_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ncomp/networks",
            data={"qm_name": "mynet"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestNetworkForm:
    async def test_edit_form_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ncomp/networks",
            data={"qm_name": "mynet"},
        )
        network_id = resp.json()["id"]
        resp = await client.get(
            f"/api/compartments/ncomp/networks/{network_id}/form",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "mynet" in resp.text

    async def test_edit_form_missing_returns_404(self, client, db):
        await _make_compartment(db)
        resp = await client.get(
            "/api/compartments/ncomp/networks/00000000-0000-0000-0000-000000000099/form",
        )
        assert resp.status_code == 404


class TestUpdateNetwork:
    async def test_rename_to_duplicate_returns_409(self, client, db):
        await _make_compartment(db)
        await client.post("/api/compartments/ncomp/networks", data={"qm_name": "net-a"})
        resp = await client.post("/api/compartments/ncomp/networks", data={"qm_name": "net-b"})
        network_id = resp.json()["id"]
        resp = await client.put(
            f"/api/compartments/ncomp/networks/{network_id}",
            json={"qm_name": "net-a"},
        )
        assert resp.status_code == 409


class TestDeleteNetwork:
    async def test_delete_returns_204(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ncomp/networks",
            data={"qm_name": "mynet"},
        )
        network_id = resp.json()["id"]
        resp = await client.delete(f"/api/compartments/ncomp/networks/{network_id}")
        assert resp.status_code == 204

    async def test_delete_missing_returns_204(self, client, db):
        await _make_compartment(db)
        resp = await client.delete(
            "/api/compartments/ncomp/networks/00000000-0000-0000-0000-000000000099"
        )
        assert resp.status_code == 204

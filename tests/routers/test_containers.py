"""Tests for container, pod, and image-unit routes."""

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
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_container_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_network_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_network_unit")
    mocker.patch("quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.compartment_manager.user_manager.sync_helper_users")
    mocker.patch("quadletman.services.compartment_manager.user_manager._setup_subuid_subgid")
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
    mocker.patch(
        "quadletman.routers._helpers.user_manager.get_compartment_drivers", return_value=([], [])
    )
    mocker.patch(
        "quadletman.routers.containers.systemd_manager.list_images",
        return_value=[],
    )
    mocker.patch(
        "quadletman.routers.containers.user_manager.get_compartment_log_drivers",
        return_value=[],
    )


async def _make_compartment(db, comp_id="ctest"):
    await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
    return comp_id


async def _make_container(client, comp_id="ctest", name="web", image="nginx:latest"):
    resp = await client.post(
        f"/api/compartments/{comp_id}/containers",
        json={"name": name, "image": image},
    )
    return resp


class TestUpdateContainer:
    async def test_update_changes_image(self, client, db):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/compartments/ctest/containers/{container_id}",
            json={"name": "web", "image": "nginx:stable"},
        )
        assert resp.status_code == 200
        assert resp.json()["image"] == "nginx:stable"

    async def test_returns_404_for_missing_container(self, client, db):
        await _make_compartment(db)
        resp = await client.put(
            "/api/compartments/ctest/containers/nonexistent",
            json={"name": "web", "image": "nginx:latest"},
        )
        assert resp.status_code == 404

    async def test_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/compartments/ctest/containers/{container_id}",
            json={"name": "web", "image": "nginx:stable"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestDeleteContainer:
    async def test_delete_returns_204(self, client, db):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/compartments/ctest/containers/{container_id}")
        assert resp.status_code == 204

    async def test_delete_removes_from_compartment(self, client, db):
        await _make_compartment(db)
        create_resp = await _make_container(client, name="gone")
        container_id = create_resp.json()["id"]
        await client.delete(f"/api/compartments/ctest/containers/{container_id}")
        comp = await client.get("/api/compartments/ctest")
        names = [c["name"] for c in comp.json()["containers"]]
        assert "gone" not in names


class TestContainerForm:
    async def test_create_form_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.get(
            "/api/compartments/ctest/containers/form",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_edit_form_returns_html(self, client, db):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        resp = await client.get(
            f"/api/compartments/ctest/containers/{container_id}/form",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_edit_form_404_for_missing(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/ctest/containers/ghost/form")
        assert resp.status_code == 404


class TestListContainers:
    async def test_compartment_has_no_containers_initially(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/ctest")
        assert resp.status_code == 200
        assert resp.json()["containers"] == []

    async def test_added_container_appears_in_compartment(self, client, db):
        await _make_compartment(db)
        await _make_container(client, name="api")
        resp = await client.get("/api/compartments/ctest")
        names = [c["name"] for c in resp.json()["containers"]]
        assert "api" in names


class TestContainerStatusDetail:
    async def test_status_detail_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        await _make_container(client)
        mocker.patch(
            "quadletman.services.systemd_manager.get_service_status",
            return_value=[{"active_state": "active", "sub_state": "running", "name": "web"}],
        )
        resp = await client.get(
            "/api/compartments/ctest/containers/web/status-detail",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200

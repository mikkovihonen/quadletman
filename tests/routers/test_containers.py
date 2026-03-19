"""Tests for container, pod, and image-unit routes."""

import io

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


class TestCreateContainer:
    async def test_create_returns_201(self, client, db):
        await _make_compartment(db)
        resp = await _make_container(client)
        assert resp.status_code == 201
        assert resp.json()["name"] == "web"

    async def test_create_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ctest/containers",
            json={"name": "api", "image": "myapp:latest"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_create_404_for_missing_compartment(self, client):
        resp = await client.post(
            "/api/compartments/ghost/containers",
            json={"name": "web", "image": "nginx"},
        )
        assert resp.status_code == 404


class TestInspectContainer:
    async def test_inspect_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        resp = await _make_container(client)
        container_id = resp.json()["id"]
        mocker.patch(
            "quadletman.routers.containers.systemd_manager.inspect_container",
            return_value={"Id": "abc123", "Name": "ctest-web"},
        )
        resp = await client.get(f"/api/compartments/ctest/containers/{container_id}/inspect")
        assert resp.status_code == 200

    async def test_inspect_404_for_missing(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.containers.systemd_manager.inspect_container",
            return_value={},
        )
        resp = await client.get("/api/compartments/ctest/containers/nonexistent/inspect")
        assert resp.status_code == 404


class TestListImages:
    async def test_list_images_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.containers.systemd_manager.list_images_detail",
            return_value=[{"id": "abc123", "names": ["nginx:latest"], "size": 100, "created": ""}],
        )
        resp = await client.get("/api/compartments/ctest/images")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestPruneImages:
    async def test_prune_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.containers.systemd_manager.prune_images",
            return_value={"count": 2, "space": "50MB freed", "output": ""},
        )
        resp = await client.post("/api/compartments/ctest/images/prune")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    async def test_prune_500_on_error(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.containers.systemd_manager.prune_images",
            side_effect=RuntimeError("podman error"),
        )
        resp = await client.post("/api/compartments/ctest/images/prune")
        assert resp.status_code == 500


class TestPullImage:
    async def test_pull_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.containers.systemd_manager.pull_image",
            return_value="Pulling nginx:latest...\nDone",
        )
        resp = await client.post(
            "/api/compartments/ctest/images/pull",
            json={"image": "nginx:latest"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_pull_400_for_missing_image(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ctest/images/pull",
            json={},
        )
        assert resp.status_code == 400

    async def test_pull_400_for_invalid_image(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ctest/images/pull",
            json={"image": "invalid image name!!"},
        )
        assert resp.status_code == 400


class TestHTMXPaths:
    async def test_delete_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        create_resp = await _make_container(client, name="gone")
        container_id = create_resp.json()["id"]
        resp = await client.delete(
            f"/api/compartments/ctest/containers/{container_id}",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_update_htmx_returns_html(self, client, db):
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


class TestPodRoutes:
    @pytest.fixture(autouse=True)
    def enable_quadlet(self, mocker):
        from quadletman.podman_version import PodmanFeatures

        mocker.patch(
            "quadletman.routers.containers.get_features",
            return_value=PodmanFeatures(
                version=(5, 8, 0),
                version_str="5.8.0",
                quadlet=True,
                build_units=True,
                image_pull_policy=True,
                apparmor=True,
                bundle=True,
                pasta=True,
                vol_driver_image=True,
            ),
        )
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_pod_unit")
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_pod_unit")

    async def test_add_pod_returns_201(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ctest/pods",
            json={"name": "mypod"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "mypod"

    async def test_add_pod_404_for_missing_compartment(self, client):
        resp = await client.post(
            "/api/compartments/ghost/pods",
            json={"name": "mypod"},
        )
        assert resp.status_code == 404

    async def test_delete_pod_returns_204(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/compartments/ctest/pods",
            json={"name": "mypod"},
        )
        pod_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/compartments/ctest/pods/{pod_id}")
        assert resp.status_code == 204


class TestEnvFileUpload:
    """Env file must be written with 0o600 and chowned to the service user."""

    @pytest.fixture
    async def container_with_home(self, client, db, tmp_path, mocker):
        await _make_compartment(db)
        await _make_container(client)
        comp = (await client.get("/api/compartments/ctest")).json()
        container_id = comp["containers"][0]["id"]

        home_dir = tmp_path / "home"
        home_dir.mkdir()
        mocker.patch(
            "quadletman.routers.containers.user_manager.get_home",
            return_value=str(home_dir),
        )
        mocker.patch("quadletman.routers.containers.user_manager.chown_to_service_user")
        return container_id, home_dir

    async def test_envfile_created_with_0o600(self, client, container_with_home):
        container_id, home_dir = container_with_home
        resp = await client.post(
            f"/api/compartments/ctest/containers/{container_id}/envfile",
            files={"file": ("web.env", io.BytesIO(b"SECRET=abc\n"), "text/plain")},
        )
        assert resp.status_code == 200
        env_file = home_dir / "env" / "web.env"
        assert env_file.exists()
        assert oct(env_file.stat().st_mode & 0o777) == oct(0o600)

    async def test_envfile_chowns_to_service_user(self, client, container_with_home, mocker):
        container_id, _ = container_with_home
        chown = mocker.patch("quadletman.routers.containers.user_manager.chown_to_service_user")
        await client.post(
            f"/api/compartments/ctest/containers/{container_id}/envfile",
            files={"file": ("web.env", io.BytesIO(b"KEY=val\n"), "text/plain")},
        )
        assert chown.called


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

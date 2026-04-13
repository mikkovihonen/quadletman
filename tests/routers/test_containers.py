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
    mocker.patch(
        "quadletman.routers.helpers.common.user_manager.get_compartment_drivers",
        return_value=([], []),
    )
    mocker.patch(
        "quadletman.routers.containers.systemd_manager.list_images",
        return_value=[],
    )
    mocker.patch(
        "quadletman.routers.containers.user_manager.get_compartment_log_drivers",
        return_value=[],
    )
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_artifact_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_artifact_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_build")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_build_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_pod_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_image_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_image_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_pod_unit")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.delete_volume_dir")
    mocker.patch(
        "quadletman.services.compartment_manager.user_manager.write_managed_containerfile",
        return_value="/home/qm-test/builds/web",
    )
    import types

    features = types.SimpleNamespace(
        version=(5, 8, 0),
        version_str="5.8.0",
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
    mocker.patch("quadletman.routers.containers.get_features", return_value=features)


async def _make_compartment(db, comp_id="ctest"):
    await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
    return comp_id


async def _make_container(client, comp_id="ctest", name="web", image="nginx:latest"):
    resp = await client.post(
        f"/api/compartments/{comp_id}/containers",
        json={"qm_name": name, "image": image},
    )
    return resp


class TestUpdateContainer:
    async def test_update_changes_image(self, client, db):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/compartments/ctest/containers/{container_id}",
            json={"qm_name": "web", "image": "nginx:stable"},
        )
        assert resp.status_code == 200
        assert resp.json()["image"] == "nginx:stable"

    async def test_returns_404_for_missing_container(self, client, db):
        await _make_compartment(db)
        resp = await client.put(
            "/api/compartments/ctest/containers/00000000-0000-0000-0000-000000000000",
            json={"qm_name": "web", "image": "nginx:latest"},
        )
        assert resp.status_code == 404

    async def test_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/compartments/ctest/containers/{container_id}",
            json={"qm_name": "web", "image": "nginx:stable"},
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
        resp = await client.get(
            "/api/compartments/ctest/containers/00000000-0000-0000-0000-000000000000/form"
        )
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
        names = [c["qm_name"] for c in resp.json()["containers"]]
        assert "api" in names


class TestCreateContainer:
    async def test_create_returns_201(self, client, db):
        await _make_compartment(db)
        resp = await _make_container(client)
        assert resp.status_code == 201
        assert resp.json()["qm_name"] == "web"

    async def test_create_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ctest/containers",
            json={"qm_name": "api", "image": "myapp:latest"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_create_404_for_missing_compartment(self, client):
        resp = await client.post(
            "/api/compartments/ghost/containers",
            json={"qm_name": "web", "image": "nginx"},
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
        resp = await client.get(
            "/api/compartments/ctest/containers/00000000-0000-0000-0000-000000000000/inspect"
        )
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
            json={"qm_name": "web", "image": "nginx:stable"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestPodRoutes:
    @pytest.fixture(autouse=True)
    def enable_quadlet(self, mocker):
        from quadletman.podman import PodmanFeatures

        mocker.patch(
            "quadletman.routers.containers.get_features",
            return_value=PodmanFeatures(
                version=(5, 8, 0),
                version_str="5.8.0",
                slirp4netns=True,
                pasta=True,
                quadlet=True,
                image_units=True,
                pod_units=True,
                build_units=True,
                quadlet_cli=True,
                artifact_units=True,
                bundle=True,
                auto_update_dry_run=True,
            ),
        )
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_pod_unit")
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_pod_unit")

    async def test_add_pod_returns_201(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ctest/pods",
            json={"qm_name": "mypod"},
        )
        assert resp.status_code == 201
        assert resp.json()["qm_name"] == "mypod"

    async def test_add_pod_404_for_missing_compartment(self, client):
        resp = await client.post(
            "/api/compartments/ghost/pods",
            json={"qm_name": "mypod"},
        )
        assert resp.status_code == 404

    async def test_delete_pod_returns_204(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/compartments/ctest/pods",
            json={"qm_name": "mypod"},
        )
        pod_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/compartments/ctest/pods/{pod_id}")
        assert resp.status_code == 204


class TestEnvFileUpload:
    """Env file upload delegates to user_manager.write_envfile."""

    @pytest.fixture
    async def container_with_envfile_mock(self, client, db, mocker):
        await _make_compartment(db)
        await _make_container(client)
        comp = (await client.get("/api/compartments/ctest")).json()
        container_id = comp["containers"][0]["id"]

        write_envfile = mocker.patch(
            "quadletman.routers.containers.user_manager.write_envfile",
            return_value="/home/qm-ctest/env/web.env",
        )
        return container_id, write_envfile

    async def test_envfile_delegates_to_service(self, client, container_with_envfile_mock):
        container_id, write_envfile = container_with_envfile_mock
        resp = await client.post(
            f"/api/compartments/ctest/containers/{container_id}/envfile",
            files={"file": ("web.env", io.BytesIO(b"SECRET=abc\n"), "text/plain")},
        )
        assert resp.status_code == 200
        assert write_envfile.called
        args = write_envfile.call_args
        assert str(args[0][0]) == "ctest"  # compartment_id
        assert str(args[0][1]) == "web"  # container name
        assert "SECRET=abc" in str(args[0][2])  # content


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


class TestDeleteEnvFile:
    async def test_delete_envfile_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        mocker.patch(
            "quadletman.routers.containers.user_manager.get_home",
            return_value="/tmp/fake-home",
        )
        resp = await client.delete(f"/api/compartments/ctest/containers/{container_id}/envfile")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_delete_envfile_404_for_missing_container(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.containers.user_manager.get_home",
            return_value="/tmp/fake-home",
        )
        resp = await client.delete(
            "/api/compartments/ctest/containers/00000000-0000-0000-0000-000000000000/envfile"
        )
        assert resp.status_code == 404


class TestImageUnits:
    @pytest.fixture(autouse=True)
    def enable_quadlet(self, mocker):
        from quadletman.podman import PodmanFeatures

        mocker.patch(
            "quadletman.routers.containers.get_features",
            return_value=PodmanFeatures(
                version=(5, 8, 0),
                version_str="5.8.0",
                slirp4netns=True,
                pasta=True,
                quadlet=True,
                image_units=True,
                pod_units=True,
                build_units=True,
                quadlet_cli=True,
                artifact_units=True,
                bundle=True,
                auto_update_dry_run=True,
            ),
        )
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_image_unit")
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_image_unit")

    async def test_add_image_unit_returns_201(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ctest/image-units",
            json={"qm_name": "myimage", "image": "nginx:latest"},
        )
        assert resp.status_code == 201
        assert resp.json()["qm_name"] == "myimage"

    async def test_add_image_unit_404_for_missing_compartment(self, client):
        resp = await client.post(
            "/api/compartments/ghost/image-units",
            json={"qm_name": "myimage", "image": "nginx:latest"},
        )
        assert resp.status_code == 404

    async def test_delete_image_unit_returns_204(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/compartments/ctest/image-units",
            json={"qm_name": "myimage", "image": "nginx:latest"},
        )
        image_unit_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/compartments/ctest/image-units/{image_unit_id}")
        assert resp.status_code == 204

    async def test_add_image_unit_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ctest/image-units",
            json={"qm_name": "webimage", "image": "nginx:latest"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestPreviewEnvFile:
    async def test_preview_returns_lines(self, client, db, tmp_path, mocker):
        await _make_compartment(db)
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        env_dir = home_dir / "env"
        env_dir.mkdir()
        env_file = env_dir / "myfile.env"
        env_file.write_text("KEY=value\n# comment\n\nSECRET=abc\n")
        mocker.patch(
            "quadletman.routers.containers.user_manager.get_home",
            return_value=str(home_dir),
        )
        resp = await client.get(
            "/api/compartments/ctest/envfile",
            params={"path": str(env_file)},
        )
        assert resp.status_code == 200
        lines = resp.json()["lines"]
        keys = [item["key"] for item in lines]
        assert "KEY" in keys
        assert "SECRET" in keys

    async def test_preview_returns_404_for_missing_file(self, client, db, tmp_path, mocker):
        await _make_compartment(db)
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        mocker.patch(
            "quadletman.routers.containers.user_manager.get_home",
            return_value=str(home_dir),
        )
        resp = await client.get(
            "/api/compartments/ctest/envfile",
            params={"path": str(home_dir / "env" / "missing.env")},
        )
        assert resp.status_code == 404

    async def test_preview_returns_403_for_path_outside_home(self, client, db, tmp_path, mocker):
        await _make_compartment(db)
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        mocker.patch(
            "quadletman.routers.containers.user_manager.get_home",
            return_value=str(home_dir),
        )
        resp = await client.get(
            "/api/compartments/ctest/envfile",
            params={"path": "/etc/passwd"},
        )
        assert resp.status_code == 403

    async def test_preview_returns_404_for_missing_user(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.containers.user_manager.get_home",
            side_effect=KeyError("user not found"),
        )
        resp = await client.get(
            "/api/compartments/ctest/envfile",
            params={"path": "/home/qm-test/env/web.env"},
        )
        assert resp.status_code == 404


class TestInspectContainerHTMX:
    async def test_inspect_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        resp = await _make_container(client)
        container_id = resp.json()["id"]
        mocker.patch(
            "quadletman.routers.containers.systemd_manager.inspect_container",
            return_value={"Id": "abc123", "Name": "ctest-web"},
        )
        resp = await client.get(
            f"/api/compartments/ctest/containers/{container_id}/inspect",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestDeletePodHTMX:
    @pytest.fixture(autouse=True)
    def enable_quadlet(self, mocker):
        from quadletman.podman import PodmanFeatures

        mocker.patch(
            "quadletman.routers.containers.get_features",
            return_value=PodmanFeatures(
                version=(5, 8, 0),
                version_str="5.8.0",
                slirp4netns=True,
                pasta=True,
                quadlet=True,
                image_units=True,
                pod_units=True,
                build_units=True,
                quadlet_cli=True,
                artifact_units=True,
                bundle=True,
                auto_update_dry_run=True,
            ),
        )
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_pod_unit")
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_pod_unit")

    async def test_delete_pod_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/compartments/ctest/pods",
            json={"qm_name": "mypod"},
        )
        pod_id = create_resp.json()["id"]
        resp = await client.delete(
            f"/api/compartments/ctest/pods/{pod_id}",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestStartContainer:
    async def test_start_returns_202(self, client, db):
        await _make_compartment(db)
        cid = (await _make_container(client)).json()["id"]
        resp = await client.post(f"/api/compartments/ctest/containers/{cid}/start")
        assert resp.status_code == 202
        assert "operation_id" in resp.json()

    async def test_start_htmx_returns_202_with_toast(self, client, db):
        await _make_compartment(db)
        cid = (await _make_container(client)).json()["id"]
        resp = await client.post(
            f"/api/compartments/ctest/containers/{cid}/start",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 202
        assert "HX-Trigger" in resp.headers


class TestStopContainer:
    async def test_stop_returns_202(self, client, db):
        await _make_compartment(db)
        cid = (await _make_container(client)).json()["id"]
        resp = await client.post(f"/api/compartments/ctest/containers/{cid}/stop")
        assert resp.status_code == 202
        assert "operation_id" in resp.json()

    async def test_stop_htmx_returns_202_with_toast(self, client, db):
        await _make_compartment(db)
        cid = (await _make_container(client)).json()["id"]
        resp = await client.post(
            f"/api/compartments/ctest/containers/{cid}/stop",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 202
        assert "HX-Trigger" in resp.headers

    async def test_stop_404_for_missing_container(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ctest/containers/00000000-0000-0000-0000-000000000000/stop"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Artifact CRUD
# ---------------------------------------------------------------------------

_ARTIFACT_DATA = {"qm_name": "my-artifact", "artifact": "docker.io/library/nginx:latest"}


class TestArtifactCRUD:
    async def test_add_artifact(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/ctest/artifacts",
            json=_ARTIFACT_DATA,
        )
        assert resp.status_code == 201
        assert resp.json()["qm_name"] == "my-artifact"

    async def test_add_artifact_404(self, client):
        resp = await client.post(
            "/api/compartments/ghost/artifacts",
            json=_ARTIFACT_DATA,
        )
        assert resp.status_code == 404

    async def test_add_artifact_duplicate(self, client, db):
        await _make_compartment(db)
        await client.post("/api/compartments/ctest/artifacts", json=_ARTIFACT_DATA)
        resp = await client.post("/api/compartments/ctest/artifacts", json=_ARTIFACT_DATA)
        assert resp.status_code == 409

    async def test_delete_artifact(self, client, db):
        await _make_compartment(db)
        create = await client.post("/api/compartments/ctest/artifacts", json=_ARTIFACT_DATA)
        aid = create.json()["id"]
        resp = await client.delete(f"/api/compartments/ctest/artifacts/{aid}")
        assert resp.status_code == 204

    async def test_update_artifact(self, client, db):
        await _make_compartment(db)
        create = await client.post("/api/compartments/ctest/artifacts", json=_ARTIFACT_DATA)
        aid = create.json()["id"]
        resp = await client.put(
            f"/api/compartments/ctest/artifacts/{aid}",
            json={**_ARTIFACT_DATA, "artifact": "docker.io/library/alpine:latest"},
        )
        assert resp.status_code == 200
        assert resp.json()["artifact"] == "docker.io/library/alpine:latest"

    async def test_update_artifact_404(self, client, db):
        await _make_compartment(db)
        resp = await client.put(
            "/api/compartments/ctest/artifacts/00000000-0000-0000-0000-000000000000",
            json=_ARTIFACT_DATA,
        )
        assert resp.status_code == 404


class TestArtifactForms:
    async def test_create_form(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/ctest/artifacts/form")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_create_form_404(self, client):
        resp = await client.get("/api/compartments/ghost/artifacts/form")
        assert resp.status_code == 404

    async def test_edit_form(self, client, db):
        await _make_compartment(db)
        create = await client.post("/api/compartments/ctest/artifacts", json=_ARTIFACT_DATA)
        aid = create.json()["id"]
        resp = await client.get(f"/api/compartments/ctest/artifacts/{aid}/form")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_edit_form_404(self, client, db):
        await _make_compartment(db)
        resp = await client.get(
            "/api/compartments/ctest/artifacts/00000000-0000-0000-0000-000000000000/form"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Update pod
# ---------------------------------------------------------------------------


class TestUpdatePod:
    async def test_update_pod(self, client, db):
        await _make_compartment(db)
        create = await client.post(
            "/api/compartments/ctest/pods",
            json={"qm_name": "mypod"},
        )
        pod_id = create.json()["id"]
        resp = await client.put(
            f"/api/compartments/ctest/pods/{pod_id}",
            json={"qm_name": "mypod"},
        )
        assert resp.status_code == 200

    async def test_update_pod_404(self, client, db):
        await _make_compartment(db)
        resp = await client.put(
            "/api/compartments/ctest/pods/00000000-0000-0000-0000-000000000000",
            json={"qm_name": "mypod"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Update image unit
# ---------------------------------------------------------------------------


class TestUpdateImageUnit:
    async def test_update_image_unit(self, client, db):
        await _make_compartment(db)
        create = await client.post(
            "/api/compartments/ctest/image-units",
            json={"qm_name": "myimg", "image": "docker.io/library/nginx:latest"},
        )
        iid = create.json()["id"]
        resp = await client.put(
            f"/api/compartments/ctest/image-units/{iid}",
            json={"qm_name": "myimg", "image": "docker.io/library/alpine:latest"},
        )
        assert resp.status_code == 200

    async def test_update_image_unit_404(self, client, db):
        await _make_compartment(db)
        resp = await client.put(
            "/api/compartments/ctest/image-units/00000000-0000-0000-0000-000000000000",
            json={"qm_name": "myimg", "image": "docker.io/library/nginx:latest"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Image unit / pod forms
# ---------------------------------------------------------------------------


class TestImageUnitForms:
    async def test_create_form(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/ctest/image-units/form")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_edit_form(self, client, db):
        await _make_compartment(db)
        create = await client.post(
            "/api/compartments/ctest/image-units",
            json={"qm_name": "myimg", "image": "docker.io/library/nginx:latest"},
        )
        iid = create.json()["id"]
        resp = await client.get(f"/api/compartments/ctest/image-units/{iid}/form")
        assert resp.status_code == 200

    async def test_edit_form_404(self, client, db):
        await _make_compartment(db)
        resp = await client.get(
            "/api/compartments/ctest/image-units/00000000-0000-0000-0000-000000000000/form"
        )
        assert resp.status_code == 404


class TestPodForms:
    async def test_edit_form_404_compartment(self, client):
        resp = await client.get(
            "/api/compartments/ghost/pods/00000000-0000-0000-0000-000000000000/form"
        )
        assert resp.status_code == 404

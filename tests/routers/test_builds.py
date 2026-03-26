"""Tests for /api/compartments/{id}/build-units routes."""

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
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_build")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_build_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_container_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_network_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_pod_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_volume_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_image_unit")
    mocker.patch("quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.compartment_manager.user_manager.sync_helper_users")
    mocker.patch("quadletman.services.compartment_manager.user_manager._setup_subuid_subgid")
    mocker.patch(
        "quadletman.services.compartment_manager.user_manager.write_managed_containerfile",
        return_value="/home/qm-test/builds/web",
    )
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
        "quadletman.routers.helpers.common.user_manager.list_helper_users", return_value=[]
    )
    # Build routes check get_features
    features = types.SimpleNamespace(
        build_units=True,
        version=(5, 3, 0),
        version_str="5.3.0",
        pasta=True,
        quadlet=True,
        image_units=True,
        pod_units=True,
        quadlet_cli=True,
        artifact_units=True,
        bundle=True,
        slirp4netns=False,
    )
    mocker.patch("quadletman.routers.builds.get_features", return_value=features)


async def _make_compartment(db, comp_id="btest"):
    await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
    return comp_id


_BUILD_DATA = {"qm_name": "my-build", "image_tag": "localhost/my-app:latest"}


class TestAddBuildUnit:
    async def test_creates_build(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/btest/build-units",
            json=_BUILD_DATA,
        )
        assert resp.status_code == 201
        assert resp.json()["qm_name"] == "my-build"

    async def test_returns_404_for_missing_compartment(self, client):
        resp = await client.post(
            "/api/compartments/ghost/build-units",
            json=_BUILD_DATA,
        )
        assert resp.status_code == 404

    async def test_version_gate_rejects(self, client, db, mocker):
        await _make_compartment(db)
        features_no_build = types.SimpleNamespace(
            build_units=False,
            version=(4, 0, 0),
            version_str="4.0.0",
            pasta=True,
            quadlet=True,
            image_units=False,
            pod_units=False,
            quadlet_cli=False,
            artifact_units=False,
            bundle=False,
            slirp4netns=False,
        )
        mocker.patch("quadletman.routers.builds.get_features", return_value=features_no_build)
        resp = await client.post(
            "/api/compartments/btest/build-units",
            json=_BUILD_DATA,
        )
        assert resp.status_code == 400

    async def test_duplicate_returns_409(self, client, db):
        await _make_compartment(db)
        await client.post("/api/compartments/btest/build-units", json=_BUILD_DATA)
        resp = await client.post("/api/compartments/btest/build-units", json=_BUILD_DATA)
        assert resp.status_code == 409


class TestUpdateBuildUnit:
    async def test_updates_build(self, client, db):
        await _make_compartment(db)
        create = await client.post("/api/compartments/btest/build-units", json=_BUILD_DATA)
        bid = create.json()["id"]
        resp = await client.put(
            f"/api/compartments/btest/build-units/{bid}",
            json={**_BUILD_DATA, "image_tag": "localhost/updated:v2"},
        )
        assert resp.status_code == 200
        assert resp.json()["image_tag"] == "localhost/updated:v2"

    async def test_returns_404_for_missing_build(self, client, db):
        await _make_compartment(db)
        resp = await client.put(
            "/api/compartments/btest/build-units/00000000-0000-0000-0000-000000000000",
            json=_BUILD_DATA,
        )
        assert resp.status_code == 404


class TestDeleteBuildUnit:
    async def test_deletes_build(self, client, db):
        await _make_compartment(db)
        create = await client.post("/api/compartments/btest/build-units", json=_BUILD_DATA)
        bid = create.json()["id"]
        resp = await client.delete(f"/api/compartments/btest/build-units/{bid}")
        assert resp.status_code == 204


class TestBuildUnitForms:
    async def test_create_form(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/btest/build-units/form")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_create_form_404(self, client):
        resp = await client.get("/api/compartments/ghost/build-units/form")
        assert resp.status_code == 404

    async def test_edit_form(self, client, db):
        await _make_compartment(db)
        create = await client.post("/api/compartments/btest/build-units", json=_BUILD_DATA)
        bid = create.json()["id"]
        resp = await client.get(f"/api/compartments/btest/build-units/{bid}/form")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_edit_form_404_build(self, client, db):
        await _make_compartment(db)
        resp = await client.get(
            "/api/compartments/btest/build-units/00000000-0000-0000-0000-000000000000/form"
        )
        assert resp.status_code == 404


class TestHTMXPaths:
    async def test_add_htmx(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/btest/build-units",
            json=_BUILD_DATA,
            headers={"HX-Request": "true"},
        )
        assert resp.status_code in (200, 201)
        assert "text/html" in resp.headers["content-type"]

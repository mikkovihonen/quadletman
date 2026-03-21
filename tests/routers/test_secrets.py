"""Tests for /api/compartments/{id}/secrets routes."""

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
        "quadletman.routers.helpers.common.user_manager.list_helper_users", return_value=[]
    )
    # Mock podman secret calls
    mocker.patch("quadletman.services.secrets_manager.create_podman_secret")
    mocker.patch("quadletman.services.secrets_manager.delete_podman_secret")


async def _make_compartment(db, comp_id="seccomp"):
    await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
    return comp_id


class TestListSecrets:
    async def test_empty_list(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/seccomp/secrets")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_404_for_missing_compartment(self, client):
        resp = await client.get("/api/compartments/ghost/secrets")
        assert resp.status_code == 404


class TestCreateSecret:
    async def test_creates_secret(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "my-secret", "value": "s3cret"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "my-secret"

    async def test_secret_appears_in_list(self, client, db):
        await _make_compartment(db)
        await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "tok", "value": "abc"},
        )
        resp = await client.get("/api/compartments/seccomp/secrets")
        names = [s["name"] for s in resp.json()]
        assert "tok" in names

    async def test_rejects_missing_name(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "", "value": "abc"},
        )
        assert resp.status_code == 400

    async def test_rejects_missing_value(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "tok", "value": ""},
        )
        assert resp.status_code == 400

    async def test_returns_404_for_missing_compartment(self, client):
        resp = await client.post(
            "/api/compartments/ghost/secrets/create",
            json={"name": "tok", "value": "abc"},
        )
        assert resp.status_code == 404

    async def test_podman_error_returns_500(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.secrets.secrets_manager.create_podman_secret",
            side_effect=RuntimeError("podman unavailable"),
        )
        resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "tok", "value": "abc"},
        )
        assert resp.status_code == 500


class TestOverwriteSecret:
    async def test_overwrites_secret(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch("quadletman.routers.secrets.secrets_manager.overwrite_podman_secret")
        create_resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "overwrite-me", "value": "old"},
        )
        secret_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/compartments/seccomp/secrets/{secret_id}",
            json={"value": "new-value"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == secret_id

    async def test_returns_404_for_missing_secret(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch("quadletman.routers.secrets.secrets_manager.overwrite_podman_secret")
        resp = await client.put(
            "/api/compartments/seccomp/secrets/nonexistent",
            json={"value": "x"},
        )
        assert resp.status_code == 404

    async def test_rejects_missing_value(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "no-val", "value": "old"},
        )
        secret_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/compartments/seccomp/secrets/{secret_id}",
            json={"value": ""},
        )
        assert resp.status_code == 400

    async def test_podman_error_returns_500(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.secrets.secrets_manager.overwrite_podman_secret",
            side_effect=RuntimeError("podman failure"),
        )
        create_resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "fail-secret", "value": "old"},
        )
        secret_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/compartments/seccomp/secrets/{secret_id}",
            json={"value": "new"},
        )
        assert resp.status_code == 500


class TestCreateSecretValidation:
    async def test_invalid_secret_name_returns_422(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": ".starts-with-dot", "value": "val"},
        )
        assert resp.status_code == 422


class TestDeleteSecret:
    async def test_deletes_secret(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "del-me", "value": "x"},
        )
        secret_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/compartments/seccomp/secrets/{secret_id}")
        assert resp.status_code == 204

    async def test_delete_removes_from_list(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "gone", "value": "x"},
        )
        secret_id = create_resp.json()["id"]
        await client.delete(f"/api/compartments/seccomp/secrets/{secret_id}")
        resp = await client.get("/api/compartments/seccomp/secrets")
        names = [s["name"] for s in resp.json()]
        assert "gone" not in names


class TestHTMXPaths:
    async def test_list_secrets_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.get(
            "/api/compartments/seccomp/secrets",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_create_secret_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "htmx-sec", "value": "val"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code in (200, 201)
        assert "text/html" in resp.headers["content-type"]

    async def test_overwrite_secret_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch("quadletman.routers.secrets.secrets_manager.overwrite_podman_secret")
        create_resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "htmx-upd", "value": "old"},
        )
        secret_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/compartments/seccomp/secrets/{secret_id}",
            json={"value": "new"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_delete_secret_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/compartments/seccomp/secrets/create",
            json={"name": "htmx-del", "value": "x"},
        )
        secret_id = create_resp.json()["id"]
        resp = await client.delete(
            f"/api/compartments/seccomp/secrets/{secret_id}",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

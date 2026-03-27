"""Tests for generic config file upload/preview/delete routes."""

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
    mocker.patch("quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.compartment_manager.user_manager.sync_helper_users")
    mocker.patch("quadletman.services.compartment_manager.user_manager._setup_subuid_subgid")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.create_volume_dir")
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


async def _make_compartment(db, comp_id="cftest"):
    await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
    return comp_id


async def _make_container(client, comp_id="cftest", name="web", image="nginx:latest"):
    resp = await client.post(
        f"/api/compartments/{comp_id}/containers",
        json={"qm_name": name, "image": image},
    )
    return resp


class TestUploadConfigFile:
    async def test_upload_delegates_to_service(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]

        write_cf = mocker.patch(
            "quadletman.routers.configfiles.user_manager.write_config_file",
            return_value="/home/qm-cftest/conf/container/web/environment_file.env",
        )
        resp = await client.post(
            f"/api/compartments/cftest/container/{container_id}/configfile/environment_file",
            files={"file": ("test.env", io.BytesIO(b"KEY=val\n"), "text/plain")},
        )
        assert resp.status_code == 200
        assert write_cf.called
        assert resp.json()["path"].endswith("environment_file.env")

    async def test_invalid_resource_type_returns_400(self, client, db, mocker):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/cftest/bogus/00000000-0000-0000-0000-000000000000/configfile/environment_file",
            files={"file": ("test.env", io.BytesIO(b"x\n"), "text/plain")},
        )
        assert resp.status_code == 400

    async def test_invalid_field_name_returns_400(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        resp = await client.post(
            f"/api/compartments/cftest/container/{container_id}/configfile/bogus_field",
            files={"file": ("test.env", io.BytesIO(b"x\n"), "text/plain")},
        )
        assert resp.status_code == 400

    async def test_missing_resource_returns_404(self, client, db, mocker):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/cftest/container/00000000-0000-0000-0000-000000000000/configfile/environment_file",
            files={"file": ("test.env", io.BytesIO(b"x\n"), "text/plain")},
        )
        assert resp.status_code == 404

    async def test_oversized_file_returns_413(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        # MAX_ENVFILE_BYTES defaults to 262144 (256 KiB)
        big = b"A" * (262144 + 1)
        resp = await client.post(
            f"/api/compartments/cftest/container/{container_id}/configfile/environment_file",
            files={"file": ("big.env", io.BytesIO(big), "text/plain")},
        )
        assert resp.status_code == 413

    async def test_invalid_json_seccomp_returns_400(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        resp = await client.post(
            f"/api/compartments/cftest/container/{container_id}/configfile/seccomp_profile",
            files={"file": ("bad.json", io.BytesIO(b"not json"), "text/plain")},
        )
        assert resp.status_code == 400
        assert "Invalid JSON" in resp.json()["detail"]

    async def test_seccomp_missing_defaultaction_returns_400(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        resp = await client.post(
            f"/api/compartments/cftest/container/{container_id}/configfile/seccomp_profile",
            files={"file": ("profile.json", io.BytesIO(b'{"foo": 1}'), "text/plain")},
        )
        assert resp.status_code == 400
        assert "defaultAction" in resp.json()["detail"]

    async def test_valid_seccomp_accepted(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        mocker.patch(
            "quadletman.routers.configfiles.user_manager.write_config_file",
            return_value="/home/qm-cftest/conf/container/web/seccomp_profile.json",
        )
        body = b'{"defaultAction": "SCMP_ACT_ERRNO", "architectures": ["SCMP_ARCH_X86_64"]}'
        resp = await client.post(
            f"/api/compartments/cftest/container/{container_id}/configfile/seccomp_profile",
            files={"file": ("profile.json", io.BytesIO(body), "text/plain")},
        )
        assert resp.status_code == 200

    async def test_invalid_toml_containers_conf_returns_400(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        resp = await client.post(
            f"/api/compartments/cftest/container/{container_id}/configfile/containers_conf_module",
            files={"file": ("bad.conf", io.BytesIO(b"[invalid\nno closing"), "text/plain")},
        )
        assert resp.status_code == 400
        assert "Invalid TOML" in resp.json()["detail"]

    async def test_auth_file_missing_auths_returns_400(self, client, db, mocker):
        """Image unit auth_file must contain an 'auths' key."""
        await _make_compartment(db)
        # Create an image unit
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_image_unit")
        iu_resp = await client.post(
            "/api/compartments/cftest/image-units",
            json={"qm_name": "myimg", "image": "docker.io/library/nginx:latest"},
        )
        iu_id = iu_resp.json()["id"]
        resp = await client.post(
            f"/api/compartments/cftest/image/{iu_id}/configfile/auth_file",
            files={"file": ("auth.json", io.BytesIO(b'{"foo": 1}'), "text/plain")},
        )
        assert resp.status_code == 400
        assert "auths" in resp.json()["detail"]


class TestPreviewConfigFile:
    async def test_keyvalue_preview(self, client, db, mocker, tmp_path):
        await _make_compartment(db)
        envfile = tmp_path / "test.env"
        envfile.write_text("DB_HOST=localhost\nDB_PORT=5432\n")
        mocker.patch(
            "quadletman.routers.configfiles.user_manager.get_home",
            return_value=str(tmp_path),
        )
        resp = await client.get(
            "/api/compartments/cftest/configfile",
            params={"path": str(envfile), "preview": "keyvalue"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "lines" in data
        assert data["lines"][0]["key"] == "DB_HOST"

    async def test_raw_preview(self, client, db, mocker, tmp_path):
        await _make_compartment(db)
        conffile = tmp_path / "seccomp.json"
        conffile.write_text('{"defaultAction": "SCMP_ACT_ERRNO"}')
        mocker.patch(
            "quadletman.routers.configfiles.user_manager.get_home",
            return_value=str(tmp_path),
        )
        resp = await client.get(
            "/api/compartments/cftest/configfile",
            params={"path": str(conffile), "preview": "raw"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "raw" in data
        assert "SCMP_ACT_ERRNO" in data["raw"]

    async def test_path_traversal_returns_403(self, client, db, mocker, tmp_path):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.configfiles.user_manager.get_home",
            return_value=str(tmp_path),
        )
        resp = await client.get(
            "/api/compartments/cftest/configfile",
            params={"path": "/etc/passwd", "preview": "raw"},
        )
        assert resp.status_code == 403


class TestDeleteConfigFile:
    async def test_delete_delegates_to_service(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]

        mocker.patch(
            "quadletman.routers.configfiles.user_manager.get_home",
            return_value="/home/qm-cftest",
        )
        delete_cf = mocker.patch(
            "quadletman.routers.configfiles.user_manager.delete_config_file",
        )
        resp = await client.delete(
            f"/api/compartments/cftest/container/{container_id}/configfile/environment_file",
        )
        assert resp.status_code == 200
        assert delete_cf.called
        assert resp.json()["ok"] is True

    async def test_invalid_field_returns_400(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await _make_container(client)
        container_id = create_resp.json()["id"]
        resp = await client.delete(
            f"/api/compartments/cftest/container/{container_id}/configfile/bogus",
        )
        assert resp.status_code == 400

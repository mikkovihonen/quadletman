"""Tests for /api/compartments/{id}/volumes routes."""

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
        "quadletman.routers.helpers.common.user_manager.get_user_info",
        return_value={"uid": 1001, "home": "/home/qm-test"},
    )
    mocker.patch(
        "quadletman.routers.helpers.common.user_manager.list_helper_users", return_value=[]
    )
    mocker.patch(
        "quadletman.routers.helpers.common.user_manager.get_compartment_drivers",
        return_value=([], []),
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


class TestVolumeSaveFile:
    """File-write routes must create files with 0o640 and chown to the service user."""

    @pytest.fixture
    async def vol(self, client, db, tmp_path, mocker):
        """Create a compartment + volume in the DB; patch list_volumes so the router
        resolves host_path to a real tmp directory we can inspect."""
        await _make_compartment(db)
        resp = await client.post("/api/compartments/volcomp/volumes", json={"name": "data"})
        vol_id = resp.json()["id"]

        vol_dir = tmp_path / "voldata"
        vol_dir.mkdir()

        from quadletman.models import Volume
        from quadletman.models.sanitized import (
            SafeResourceName,
            SafeSELinuxContext,
            SafeSlug,
            SafeStr,
            SafeTimestamp,
            SafeUUID,
        )

        fake_vol = Volume(
            id=SafeUUID.of(vol_id, "test"),
            compartment_id=SafeSlug.of("volcomp", "test"),
            name=SafeResourceName.of("data", "test"),
            host_path=SafeStr.of(str(vol_dir), "test"),
            created_at=SafeTimestamp.trusted("2024-01-01T00:00:00", "test"),
            selinux_context=SafeSELinuxContext.trusted("container_file_t", "test"),
        )
        mocker.patch(
            "quadletman.routers.volumes.compartment_manager.list_volumes",
            return_value=[fake_vol],
        )
        mocker.patch("quadletman.routers.volumes.user_manager.chown_to_service_user")
        mocker.patch("quadletman.routers.volumes.relabel")
        return vol_id, vol_dir

    async def test_save_file_creates_with_0o640(self, client, vol):
        vol_id, vol_dir = vol
        resp = await client.put(
            f"/api/compartments/volcomp/volumes/{vol_id}/file",
            params={"path": "/hello.txt"},
            data={"content": "hello world"},
        )
        assert resp.status_code == 200
        written = vol_dir / "hello.txt"
        assert written.exists()
        assert oct(written.stat().st_mode & 0o777) == oct(0o640)

    async def test_save_file_chowns_to_service_user(self, client, vol, mocker):
        vol_id, _ = vol
        chown = mocker.patch("quadletman.routers.volumes.user_manager.chown_to_service_user")
        await client.put(
            f"/api/compartments/volcomp/volumes/{vol_id}/file",
            params={"path": "/cfg.txt"},
            data={"content": "key=val"},
        )
        assert chown.called

    async def test_upload_creates_with_0o640(self, client, vol):
        vol_id, vol_dir = vol
        resp = await client.post(
            f"/api/compartments/volcomp/volumes/{vol_id}/upload",
            params={"path": "/"},
            files={"file": ("data.txt", io.BytesIO(b"payload"), "text/plain")},
        )
        assert resp.status_code == 200
        written = vol_dir / "data.txt"
        assert written.exists()
        assert oct(written.stat().st_mode & 0o777) == oct(0o640)

    async def test_upload_chowns_to_service_user(self, client, vol, mocker):
        vol_id, _ = vol
        chown = mocker.patch("quadletman.routers.volumes.user_manager.chown_to_service_user")
        await client.post(
            f"/api/compartments/volcomp/volumes/{vol_id}/upload",
            params={"path": "/"},
            files={"file": ("f.bin", io.BytesIO(b"\x00\x01"), "application/octet-stream")},
        )
        assert chown.called


class TestVolumeSize:
    async def test_returns_bytes_json(self, client, mocker):
        mocker.patch(
            "quadletman.utils.dir_size",
            return_value=1024,
        )
        resp = await client.get("/api/compartments/volcomp/volumes/data/size")
        assert resp.status_code == 200
        assert resp.json()["bytes"] == 1024

    async def test_returns_htmx_html(self, client, mocker):
        mocker.patch(
            "quadletman.utils.dir_size",
            return_value=2048,
        )
        resp = await client.get(
            "/api/compartments/volcomp/volumes/data/size",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestVolumeHTMX:
    async def test_create_json_returns_201(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/volcomp/volumes",
            json={"name": "jsonvol"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "jsonvol"

    async def test_delete_returns_204(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post("/api/compartments/volcomp/volumes", json={"name": "gone2"})
        volume_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/compartments/volcomp/volumes/{volume_id}")
        assert resp.status_code == 204


class TestVolumeUpdate:
    async def test_update_volume_owner_calls_manager(self, client, db, mocker):
        await _make_compartment(db)
        create_resp = await client.post("/api/compartments/volcomp/volumes", json={"name": "upd"})
        volume_id = create_resp.json()["id"]
        mock = mocker.patch(
            "quadletman.routers.volumes.compartment_manager.update_volume_owner",
            return_value=None,
        )
        # update_volume always renders HTML - mock the entire response path
        mocker.patch(
            "quadletman.routers.volumes._TEMPLATES.TemplateResponse",
            return_value=__import__("fastapi.responses", fromlist=["HTMLResponse"]).HTMLResponse(
                "<html/>"
            ),
        )
        resp = await client.patch(
            f"/api/compartments/volcomp/volumes/{volume_id}",
            json={"owner_uid": 0},
        )
        assert resp.status_code == 200
        mock.assert_called_once()


class TestVolumeBrowse:
    @pytest.fixture
    async def vol_with_dir(self, client, db, tmp_path, mocker):
        await _make_compartment(db)
        resp = await client.post("/api/compartments/volcomp/volumes", json={"name": "browse"})
        vol_id = resp.json()["id"]

        vol_dir = tmp_path / "browsedata"
        vol_dir.mkdir()
        (vol_dir / "subdir").mkdir()
        (vol_dir / "file.txt").write_text("hello")

        from quadletman.models import Volume
        from quadletman.models.sanitized import (
            SafeResourceName,
            SafeSELinuxContext,
            SafeSlug,
            SafeStr,
            SafeTimestamp,
            SafeUUID,
        )

        fake_vol = Volume(
            id=SafeUUID.of(vol_id, "test"),
            compartment_id=SafeSlug.of("volcomp", "test"),
            name=SafeResourceName.of("browse", "test"),
            host_path=SafeStr.of(str(vol_dir), "test"),
            created_at=SafeTimestamp.trusted("2024-01-01T00:00:00", "test"),
            selinux_context=SafeSELinuxContext.trusted("container_file_t", "test"),
        )
        mocker.patch(
            "quadletman.routers.volumes.compartment_manager.list_volumes",
            return_value=[fake_vol],
        )
        mocker.patch(
            "quadletman.services.selinux.get_file_context_type",
            return_value="container_file_t",
        )
        return vol_id, vol_dir

    async def test_browse_returns_200(self, client, vol_with_dir):
        vol_id, _ = vol_with_dir
        resp = await client.get(
            f"/api/compartments/volcomp/volumes/{vol_id}/browse",
            params={"path": "/"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_browse_missing_dir_returns_404(self, client, vol_with_dir):
        vol_id, _ = vol_with_dir
        resp = await client.get(
            f"/api/compartments/volcomp/volumes/{vol_id}/browse",
            params={"path": "/nonexistent"},
        )
        assert resp.status_code == 404

    async def test_get_file_returns_200(self, client, vol_with_dir):
        vol_id, vol_dir = vol_with_dir
        resp = await client.get(
            f"/api/compartments/volcomp/volumes/{vol_id}/file",
            params={"path": "/file.txt"},
        )
        assert resp.status_code == 200

    async def test_get_new_file_returns_200(self, client, vol_with_dir):
        vol_id, _ = vol_with_dir
        resp = await client.get(
            f"/api/compartments/volcomp/volumes/{vol_id}/file",
            params={"path": "/newfile.txt"},
        )
        assert resp.status_code == 200

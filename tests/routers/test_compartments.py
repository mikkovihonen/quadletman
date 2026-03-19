"""Tests for compartment-level routes (lifecycle, update, export, metrics)."""

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
        "quadletman.routers._helpers.user_manager.get_user_info",
        return_value={"uid": 1001, "home": "/home/qm-test"},
    )
    mocker.patch("quadletman.routers._helpers.user_manager.list_helper_users", return_value=[])
    mocker.patch(
        "quadletman.routers._helpers.user_manager.get_compartment_drivers", return_value=([], [])
    )
    mocker.patch(
        "quadletman.routers.compartments.compartment_manager.get_status",
        return_value=[],
    )


async def _make_compartment(db, comp_id="comp1", description=""):
    await compartment_manager.create_compartment(
        db, CompartmentCreate(id=comp_id, description=description)
    )
    return comp_id


class TestCreateCompartmentRollback:
    async def test_os_user_cleaned_up_on_setup_failure(self, mocker, db):
        """If _setup_service_user raises, delete_service_user must be called to avoid orphaned users."""
        mocker.patch(
            "quadletman.services.compartment_manager._setup_service_user",
            side_effect=RuntimeError("loginctl failed"),
        )
        delete_mock = mocker.patch(
            "quadletman.services.compartment_manager.user_manager.delete_service_user"
        )
        with pytest.raises(RuntimeError):
            await compartment_manager.create_compartment(db, CompartmentCreate(id="failcomp"))
        delete_mock.assert_called_once_with("failcomp")

    async def test_db_record_rolled_back_on_setup_failure(self, mocker, db):
        """If _setup_service_user raises, the DB record must be removed."""
        mocker.patch(
            "quadletman.services.compartment_manager._setup_service_user",
            side_effect=RuntimeError("loginctl failed"),
        )
        mocker.patch("quadletman.services.compartment_manager.user_manager.delete_service_user")
        with pytest.raises(RuntimeError):
            await compartment_manager.create_compartment(db, CompartmentCreate(id="failcomp2"))
        result = await compartment_manager.get_compartment(db, "failcomp2")
        assert result is None


class TestCreateCompartmentDuplicate:
    async def test_duplicate_returns_409(self, client, db):
        await _make_compartment(db, "dup")
        resp = await client.post("/api/compartments", json={"id": "dup"})
        assert resp.status_code == 409

    async def test_htmx_returns_html(self, client, db):
        resp = await client.post(
            "/api/compartments",
            json={"id": "newone"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code in (200, 201)
        assert "text/html" in resp.headers["content-type"]


class TestUpdateCompartment:
    async def test_update_description(self, client, db):
        await _make_compartment(db)
        resp = await client.put(
            "/api/compartments/comp1",
            json={"description": "updated"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "updated"

    async def test_returns_404_for_missing(self, client):
        resp = await client.put(
            "/api/compartments/ghost",
            json={"description": "x"},
        )
        assert resp.status_code == 404


class TestExportCompartment:
    async def test_export_returns_text_attachment(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.export_compartment_bundle",
            return_value="# bundle content\n",
        )
        resp = await client.get("/api/compartments/comp1/export")
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("content-disposition", "")

    async def test_export_returns_404_for_missing(self, client, mocker):
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.export_compartment_bundle",
            return_value=None,
        )
        resp = await client.get("/api/compartments/ghost/export")
        assert resp.status_code == 404


class TestLifecycle:
    async def test_start_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.start_compartment",
            return_value=[],
        )
        resp = await client.post("/api/compartments/comp1/start")
        assert resp.status_code == 200

    async def test_stop_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.stop_compartment",
            return_value=None,
        )
        resp = await client.post("/api/compartments/comp1/stop")
        assert resp.status_code == 200

    async def test_restart_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.stop_compartment",
            return_value=None,
        )
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.start_compartment",
            return_value=[],
        )
        resp = await client.post("/api/compartments/comp1/restart")
        assert resp.status_code == 200


class TestStatusEndpoints:
    async def test_status_dot_returns_200(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/comp1/status-dot")
        assert resp.status_code == 200

    async def test_processes_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.metrics.get_processes",
            return_value=[],
        )
        mocker.patch(
            "quadletman.routers.compartments.user_manager.get_uid",
            return_value=1001,
        )
        resp = await client.get(
            "/api/compartments/comp1/processes",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200

    async def test_disk_usage_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.metrics.get_disk_breakdown",
            return_value={
                "images": [],
                "overlays": [],
                "volumes": [],
                "volumes_total": 0,
                "config_bytes": 0,
            },
        )
        mocker.patch("quadletman.routers.compartments.user_manager.get_uid", return_value=1001)
        resp = await client.get(
            "/api/compartments/comp1/disk-usage",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200


class TestDeleteCompartment:
    async def test_delete_returns_204(self, client, db):
        await _make_compartment(db)
        resp = await client.delete("/api/compartments/comp1")
        assert resp.status_code == 204

    async def test_delete_missing_returns_404(self, client):
        resp = await client.delete("/api/compartments/ghost")
        assert resp.status_code == 404

    async def test_htmx_delete_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.delete(
            "/api/compartments/comp1",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestNetworkUpdate:
    async def test_update_network_returns_200(self, client, db):
        await _make_compartment(db)
        resp = await client.put(
            "/api/compartments/comp1/network",
            data={
                "net_driver": "bridge",
                "net_subnet": "",
                "net_gateway": "",
                "net_ipv6": "",
                "net_internal": "",
                "net_dns_enabled": "",
            },
        )
        assert resp.status_code == 200

    async def test_update_network_missing_returns_404(self, client):
        resp = await client.put(
            "/api/compartments/ghost/network",
            data={
                "net_driver": "bridge",
                "net_subnet": "",
                "net_gateway": "",
                "net_ipv6": "",
                "net_internal": "",
                "net_dns_enabled": "",
            },
        )
        assert resp.status_code == 404


class TestEnableDisableCompartment:
    async def test_enable_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.enable_compartment",
            return_value=None,
        )
        resp = await client.post("/api/compartments/comp1/enable")
        assert resp.status_code == 200

    async def test_disable_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.disable_compartment",
            return_value=None,
        )
        resp = await client.post("/api/compartments/comp1/disable")
        assert resp.status_code == 200


class TestQuadletsViewer:
    async def test_quadlets_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.get_quadlet_files",
            return_value=[],
        )
        resp = await client.get("/api/compartments/comp1/quadlets")
        assert resp.status_code == 200

    async def test_quadlets_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.get_quadlet_files",
            return_value=[],
        )
        resp = await client.get(
            "/api/compartments/comp1/quadlets",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestCompartmentStatus:
    async def test_status_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.get_status",
            return_value=[],
        )
        resp = await client.get("/api/compartments/comp1/status")
        assert resp.status_code == 200

    async def test_status_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.get_status",
            return_value=[],
        )
        resp = await client.get(
            "/api/compartments/comp1/status",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestBundleImport:
    @pytest.fixture(autouse=True)
    def enable_bundle_feature(self, mocker):
        from quadletman.podman_version import PodmanFeatures

        mocker.patch(
            "quadletman.routers.compartments.get_features",
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

    async def test_import_creates_compartment(self, client, db):
        bundle = "[Container]\nImage=nginx:latest\nContainerName=web\n"
        resp = await client.post(
            "/api/compartments/import",
            data={"compartment_id": "importcomp", "description": ""},
            files={"file": ("test.quadlets", bundle.encode(), "text/plain")},
        )
        assert resp.status_code == 201
        assert resp.json()["id"] == "importcomp"

    async def test_import_409_for_existing(self, client, db):
        await _make_compartment(db, "existing")
        bundle = "[Container]\nImage=nginx:latest\nContainerName=web\n"
        resp = await client.post(
            "/api/compartments/import",
            data={"compartment_id": "existing", "description": ""},
            files={"file": ("test.quadlets", bundle.encode(), "text/plain")},
        )
        assert resp.status_code == 409

    async def test_import_422_for_empty_bundle(self, client, db):
        resp = await client.post(
            "/api/compartments/import",
            data={"compartment_id": "emptycomp", "description": ""},
            files={"file": ("test.quadlets", b"# no containers\n", "text/plain")},
        )
        assert resp.status_code == 422


class TestLifecycleHTMX:
    async def test_start_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.start_compartment",
            return_value=[],
        )
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.get_status",
            return_value=[],
        )
        resp = await client.post(
            "/api/compartments/comp1/start",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_stop_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.stop_compartment",
            return_value=[],
        )
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.get_status",
            return_value=[],
        )
        resp = await client.post(
            "/api/compartments/comp1/stop",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_restart_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.restart_compartment",
            return_value=[],
        )
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.get_status",
            return_value=[],
        )
        resp = await client.post(
            "/api/compartments/comp1/restart",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_enable_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.enable_compartment",
            return_value=None,
        )
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.get_status",
            return_value=[],
        )
        resp = await client.post(
            "/api/compartments/comp1/enable",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_disable_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.disable_compartment",
            return_value=None,
        )
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.get_status",
            return_value=[],
        )
        resp = await client.post(
            "/api/compartments/comp1/disable",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestUpdateHTMX:
    async def test_update_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.put(
            "/api/compartments/comp1",
            json={"description": "updated"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_update_network_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.put(
            "/api/compartments/comp1/network",
            data={
                "net_driver": "bridge",
                "net_subnet": "",
                "net_gateway": "",
                "net_ipv6": "",
                "net_internal": "",
                "net_dns_enabled": "",
            },
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestSyncHTMX:
    async def test_sync_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.check_sync",
            return_value=[],
        )
        resp = await client.get(
            "/api/compartments/comp1/sync",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_resync_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.resync_compartment",
            return_value=None,
        )
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.check_sync",
            return_value=[],
        )
        resp = await client.post(
            "/api/compartments/comp1/sync",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestSyncStatus:
    async def test_sync_returns_list(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.check_sync",
            return_value=[],
        )
        resp = await client.get("/api/compartments/comp1/sync")
        assert resp.status_code == 200

    async def test_resync_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager._write_and_reload",
            return_value=None,
        )
        resp = await client.post("/api/compartments/comp1/sync")
        assert resp.status_code == 200

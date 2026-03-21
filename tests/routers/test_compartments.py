"""Tests for compartment-level routes (lifecycle, update, export, metrics)."""

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
    mocker.patch(
        "quadletman.routers.helpers.common.user_manager.get_compartment_drivers",
        return_value=([], []),
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
        result = await compartment_manager.get_compartment(db, _sid("failcomp2"))
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
                pasta=True,
                quadlet=True,
                image_units=True,
                pod_units=True,
                build_units=True,
                quadlet_cli=True,
                artifact_units=True,
                bundle=True,
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


class TestListCompartments:
    async def test_list_returns_200(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert any(c["id"] == "comp1" for c in resp.json())

    async def test_list_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestGetCompartment:
    async def test_get_returns_200(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/comp1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "comp1"

    async def test_get_returns_404_for_missing(self, client):
        resp = await client.get("/api/compartments/ghost")
        assert resp.status_code == 404

    async def test_get_htmx_returns_html(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.compartment_manager.get_status",
            return_value=[],
        )
        resp = await client.get("/api/compartments/comp1", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestMetricsEndpoints:
    async def test_metrics_returns_200(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.user_manager.get_user_info",
            return_value={"uid": 1001, "home": "/home/qm-comp1"},
        )
        mocker.patch(
            "quadletman.routers.compartments.metrics.get_metrics",
            return_value={
                "cpu_percent": 0.0,
                "mem_bytes": 0,
                "proc_count": 0,
                "disk_bytes": 0,
            },
        )
        resp = await client.get("/api/compartments/comp1/metrics")
        assert resp.status_code == 200

    async def test_metrics_no_user_returns_zeros(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.user_manager.get_user_info",
            return_value=None,
        )
        resp = await client.get("/api/compartments/comp1/metrics")
        assert resp.status_code == 200
        assert resp.json()["cpu_percent"] == 0

    async def test_global_metrics_returns_list(self, client, db, mocker):
        await _make_compartment(db)
        mocker.patch(
            "quadletman.routers.compartments.user_manager.get_user_info",
            return_value=None,
        )
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_metrics_disk_returns_list(self, client, db, mocker):
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
        resp = await client.get("/api/metrics/disk")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestMetricsHistory:
    async def test_metrics_history_returns_list(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/comp1/metrics-history")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_metrics_history_404_for_missing(self, client):
        resp = await client.get("/api/compartments/ghost/metrics-history")
        assert resp.status_code == 404


class TestRestartStats:
    async def test_restart_stats_returns_list(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/comp1/restart-stats")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_restart_stats_404_for_missing(self, client):
        resp = await client.get("/api/compartments/ghost/restart-stats")
        assert resp.status_code == 404


class TestNotificationHooks:
    async def test_list_hooks_returns_200(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/comp1/notification-hooks")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_list_hooks_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.get(
            "/api/compartments/comp1/notification-hooks",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_add_hook_returns_201(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/notification-hooks",
            data={
                "event_type": "on_failure",
                "container_name": "web",
                "webhook_url": "https://hooks.example.com/test",
                "webhook_secret": "",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["webhook_url"] == "https://hooks.example.com/test"

    async def test_add_hook_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/notification-hooks",
            data={
                "event_type": "on_failure",
                "container_name": "",
                "webhook_url": "https://hooks.example.com/test2",
                "webhook_secret": "",
            },
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_delete_hook_returns_204(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/compartments/comp1/notification-hooks",
            data={
                "event_type": "on_failure",
                "container_name": "",
                "webhook_url": "https://hooks.example.com/del",
                "webhook_secret": "",
            },
        )
        hook_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/compartments/comp1/notification-hooks/{hook_id}")
        assert resp.status_code == 204

    async def test_on_unexpected_process_clears_container_name(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/notification-hooks",
            data={
                "event_type": "on_unexpected_process",
                "container_name": "should-be-cleared",
                "webhook_url": "https://hooks.example.com/proc",
                "webhook_secret": "",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["container_name"] == ""


class TestProcessMonitor:
    async def test_get_process_monitor_returns_200(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/comp1/process-monitor")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_process_monitor_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.get(
            "/api/compartments/comp1/process-monitor",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_set_process_monitor_enabled(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/process-monitor/enabled",
            data={"enabled": "true"},
        )
        assert resp.status_code == 200
        assert resp.json()["process_monitor_enabled"] is True

    async def test_set_process_monitor_disabled(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/process-monitor/enabled",
            data={"enabled": "false"},
        )
        assert resp.status_code == 200
        assert resp.json()["process_monitor_enabled"] is False

    async def test_set_process_monitor_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/process-monitor/enabled",
            data={"enabled": "true"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestConnectionMonitor:
    async def test_get_connection_monitor_returns_200(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/comp1/connection-monitor")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_connection_monitor_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.get(
            "/api/compartments/comp1/connection-monitor",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_set_connection_monitor_enabled(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/connection-monitor/enabled",
            data={"enabled": "true"},
        )
        assert resp.status_code == 200
        assert resp.json()["connection_monitor_enabled"] is True

    async def test_set_connection_monitor_disabled(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/connection-monitor/enabled",
            data={"enabled": "false"},
        )
        assert resp.status_code == 200
        assert resp.json()["connection_monitor_enabled"] is False

    async def test_set_retention_days(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/connection-monitor/retention",
            data={"days": "30"},
        )
        assert resp.status_code == 200
        assert resp.json()["connection_history_retention_days"] == 30

    async def test_set_retention_invalid(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/connection-monitor/retention",
            data={"days": "abc"},
        )
        assert resp.status_code == 422

    async def test_set_retention_zero_invalid(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/connection-monitor/retention",
            data={"days": "0"},
        )
        assert resp.status_code == 422

    async def test_add_whitelist_rule(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/connection-whitelist",
            data={
                "description": "allow dns",
                "container_name": "web",
                "proto": "udp",
                "dst_ip": "",
                "dst_port": "53",
                "direction": "outbound",
            },
        )
        assert resp.status_code == 200

    async def test_add_whitelist_rule_invalid_port(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/connection-whitelist",
            data={
                "description": "bad port",
                "container_name": "",
                "proto": "",
                "dst_ip": "",
                "dst_port": "99999",
                "direction": "",
            },
        )
        assert resp.status_code == 422

    async def test_add_whitelist_rule_invalid_ip(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/compartments/comp1/connection-whitelist",
            data={
                "description": "bad ip",
                "container_name": "",
                "proto": "",
                "dst_ip": "not-an-ip",
                "dst_port": "",
                "direction": "",
            },
        )
        assert resp.status_code == 422

    async def test_clear_connections_history(self, client, db):
        await _make_compartment(db)
        resp = await client.delete("/api/compartments/comp1/connections")
        assert resp.status_code in (200, 204)

    async def test_download_connections_csv(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/compartments/comp1/connections.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    async def test_delete_whitelist_rule(self, client, db):
        await _make_compartment(db)
        # Just verify delete endpoint exists - use a fake ID (graceful no-op)
        resp = await client.delete(
            "/api/compartments/comp1/connection-whitelist/00000000-0000-0000-0000-000000000000"
        )
        assert resp.status_code in (200, 204)


class TestAllStatusDots:
    async def test_all_status_dots_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.get("/api/status-dots")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

"""Tests for Podman version-gated API routes."""

import io

import pytest

from quadletman.podman_version import PodmanFeatures

_OLD_FEATURES = PodmanFeatures(
    version=(4, 3, 0),
    version_str="4.3.0",
    quadlet=False,
    build_units=False,
    image_pull_policy=False,
    apparmor=False,
    bundle=False,
    pasta=True,
    vol_driver_image=False,
)

_NO_PODMAN = PodmanFeatures(
    version=None,
    version_str="not found",
    quadlet=False,
    build_units=False,
    image_pull_policy=False,
    apparmor=False,
    bundle=False,
    pasta=False,
    vol_driver_image=False,
)


@pytest.fixture(autouse=True)
def mock_system_calls(mocker):
    mocker.patch("quadletman.services.compartment_manager._setup_service_user")
    mocker.patch("quadletman.services.compartment_manager._teardown_service")
    mocker.patch("quadletman.services.compartment_manager._write_and_reload")
    mocker.patch("quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.user_manager.get_uid", return_value=1001)
    mocker.patch(
        "quadletman.routers.helpers.common.user_manager.get_user_info",
        return_value={"uid": 1001, "home": "/home/qm-test"},
    )
    mocker.patch(
        "quadletman.routers.helpers.common.user_manager.list_helper_users", return_value=[]
    )


# ---------------------------------------------------------------------------
# add_pod — requires quadlet (Podman >= 4.4)
# ---------------------------------------------------------------------------


class TestAddPodVersionGate:
    async def test_add_pod_blocked_on_old_podman(self, client, mocker):
        mocker.patch("quadletman.routers.containers.get_features", return_value=_OLD_FEATURES)
        resp = await client.post(
            "/api/compartments/comp1/pods",
            json={"name": "mypod"},
        )
        assert resp.status_code == 400
        assert "4.4+" in resp.json()["detail"]
        assert "4.3.0" in resp.json()["detail"]

    async def test_add_pod_blocked_when_podman_absent(self, client, mocker):
        mocker.patch("quadletman.routers.containers.get_features", return_value=_NO_PODMAN)
        resp = await client.post(
            "/api/compartments/comp1/pods",
            json={"name": "mypod"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# add_image_unit — requires quadlet (Podman >= 4.4)
# ---------------------------------------------------------------------------


class TestAddImageUnitVersionGate:
    async def test_add_image_unit_blocked_on_old_podman(self, client, mocker):
        mocker.patch("quadletman.routers.containers.get_features", return_value=_OLD_FEATURES)
        resp = await client.post(
            "/api/compartments/comp1/image-units",
            json={"name": "myimage", "image": "docker.io/library/alpine:latest"},
        )
        assert resp.status_code == 400
        assert "4.4+" in resp.json()["detail"]

    async def test_add_image_unit_blocked_when_podman_absent(self, client, mocker):
        mocker.patch("quadletman.routers.containers.get_features", return_value=_NO_PODMAN)
        resp = await client.post(
            "/api/compartments/comp1/image-units",
            json={"name": "myimage", "image": "docker.io/library/alpine:latest"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# import_service_bundle — requires bundle (Podman >= 5.8)
# ---------------------------------------------------------------------------


class TestImportBundleVersionGate:
    async def test_import_blocked_on_old_podman(self, client, mocker):
        mocker.patch("quadletman.routers.compartments.get_features", return_value=_OLD_FEATURES)
        resp = await client.post(
            "/api/compartments/import",
            data={"compartment_id": "newcomp"},
            files={
                "file": ("test.quadlets", io.BytesIO(b"[Container]\nImage=alpine\n"), "text/plain")
            },
        )
        assert resp.status_code == 400
        assert "5.8+" in resp.json()["detail"]

    async def test_import_blocked_when_podman_absent(self, client, mocker):
        mocker.patch("quadletman.routers.compartments.get_features", return_value=_NO_PODMAN)
        resp = await client.post(
            "/api/compartments/import",
            data={"compartment_id": "newcomp"},
            files={"file": ("test.quadlets", io.BytesIO(b""), "text/plain")},
        )
        assert resp.status_code == 400
        assert "5.8+" in resp.json()["detail"]

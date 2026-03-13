"""Tests for quadletman/services/service_manager.py — orchestration with in-memory DB."""

import pytest

from quadletman.models import ContainerCreate, ServiceCreate, VolumeCreate
from quadletman.services import service_manager


@pytest.fixture(autouse=True)
def mock_system_calls(mocker):
    """Suppress all real system calls made by service_manager for every test."""
    mocker.patch("quadletman.services.service_manager._setup_service_user")
    mocker.patch("quadletman.services.service_manager._teardown_service")
    mocker.patch("quadletman.services.service_manager._write_and_reload")
    mocker.patch("quadletman.services.service_manager.systemd_manager.daemon_reload")
    mocker.patch("quadletman.services.service_manager.systemd_manager.start_unit")
    mocker.patch("quadletman.services.service_manager.systemd_manager.stop_unit")
    mocker.patch("quadletman.services.service_manager.systemd_manager.restart_unit")
    mocker.patch("quadletman.services.service_manager.quadlet_writer.remove_container_unit")
    mocker.patch("quadletman.services.service_manager.quadlet_writer.remove_network_unit")
    mocker.patch("quadletman.services.service_manager.volume_manager.delete_volume_dir")
    mocker.patch(
        "quadletman.services.service_manager.volume_manager.create_volume_dir",
        return_value="/var/lib/quadletman/volumes/svc/data",
    )
    mocker.patch("quadletman.services.service_manager.volume_manager.chown_volume_dir")
    mocker.patch("quadletman.services.service_manager.user_manager.sync_helper_users")
    mocker.patch("quadletman.services.service_manager.user_manager.get_uid", return_value=1001)


# ---------------------------------------------------------------------------
# Service CRUD
# ---------------------------------------------------------------------------


class TestCreateService:
    async def test_creates_service_in_db(self, db):
        data = ServiceCreate(id="mysvc")
        svc = await service_manager.create_service(db, data)
        assert svc.id == "mysvc"

    async def test_setup_service_user_called(self, db, mocker):
        setup_mock = mocker.patch("quadletman.services.service_manager._setup_service_user")
        await service_manager.create_service(db, ServiceCreate(id="svc2"))
        setup_mock.assert_called_once_with("svc2")

    async def test_db_rolled_back_on_setup_failure(self, db, mocker):
        mocker.patch(
            "quadletman.services.service_manager._setup_service_user",
            side_effect=RuntimeError("useradd failed"),
        )
        with pytest.raises(RuntimeError):
            await service_manager.create_service(db, ServiceCreate(id="bad"))
        # Service should not exist in DB after rollback
        assert await service_manager.get_service(db, "bad") is None

    async def test_rejects_duplicate_id(self, db):
        import sqlite3

        data = ServiceCreate(id="dup")
        await service_manager.create_service(db, data)
        with pytest.raises(sqlite3.IntegrityError):
            await service_manager.create_service(db, data)


class TestGetService:
    async def test_returns_none_for_missing(self, db):
        assert await service_manager.get_service(db, "nonexistent") is None

    async def test_returns_service(self, db):
        await service_manager.create_service(db, ServiceCreate(id="s1"))
        svc = await service_manager.get_service(db, "s1")
        assert svc is not None
        assert svc.id == "s1"


class TestListServices:
    async def test_empty_list_initially(self, db):
        services = await service_manager.list_services(db)
        assert services == []

    async def test_returns_created_services(self, db):
        await service_manager.create_service(db, ServiceCreate(id="a"))
        await service_manager.create_service(db, ServiceCreate(id="b"))
        services = await service_manager.list_services(db)
        ids = {s.id for s in services}
        assert ids == {"a", "b"}


# ---------------------------------------------------------------------------
# Container CRUD
# ---------------------------------------------------------------------------


class TestAddContainer:
    async def test_adds_container_to_db(self, db):
        await service_manager.create_service(db, ServiceCreate(id="svc"))
        c = await service_manager.add_container(
            db, "svc", ContainerCreate(name="web", image="nginx")
        )
        assert c.name == "web"
        assert c.service_id == "svc"

    async def test_write_and_reload_called(self, db, mocker):
        wr_mock = mocker.patch("quadletman.services.service_manager._write_and_reload")
        await service_manager.create_service(db, ServiceCreate(id="svc2"))
        await service_manager.add_container(db, "svc2", ContainerCreate(name="app", image="myapp"))
        wr_mock.assert_called()

    async def test_raises_for_unknown_service(self, db):
        import sqlite3

        with pytest.raises((sqlite3.IntegrityError, Exception)):
            await service_manager.add_container(
                db, "ghost", ContainerCreate(name="web", image="nginx")
            )


class TestUpdateContainer:
    async def test_updates_image(self, db, mocker):
        mocker.patch("quadletman.services.service_manager._write_and_reload")
        await service_manager.create_service(db, ServiceCreate(id="svc"))
        original = await service_manager.add_container(
            db, "svc", ContainerCreate(name="web", image="nginx:1.0")
        )
        updated = await service_manager.update_container(
            db,
            "svc",
            original.id,
            ContainerCreate(name="web", image="nginx:2.0"),
        )
        assert updated.image == "nginx:2.0"


class TestDeleteContainer:
    async def test_deletes_container(self, db, mocker):
        mocker.patch("quadletman.services.service_manager._write_and_reload")
        await service_manager.create_service(db, ServiceCreate(id="svc"))
        c = await service_manager.add_container(
            db, "svc", ContainerCreate(name="web", image="nginx")
        )
        await service_manager.delete_container(db, "svc", c.id)
        containers = await service_manager.list_containers(db, "svc")
        assert not any(x.id == c.id for x in containers)


# ---------------------------------------------------------------------------
# Volume CRUD
# ---------------------------------------------------------------------------


class TestAddVolume:
    async def test_adds_volume(self, db):
        await service_manager.create_service(db, ServiceCreate(id="svc"))
        vol = await service_manager.add_volume(db, "svc", VolumeCreate(name="data"))
        assert vol.name == "data"
        assert vol.service_id == "svc"

    async def test_host_path_set(self, db):
        await service_manager.create_service(db, ServiceCreate(id="svc2"))
        vol = await service_manager.add_volume(db, "svc2", VolumeCreate(name="uploads"))
        assert vol.host_path != ""

"""Tests for quadletman/services/compartment_manager.py — orchestration with in-memory DB."""

import pytest

from quadletman.models import CompartmentCreate, ContainerCreate, VolumeCreate
from quadletman.services import compartment_manager


@pytest.fixture(autouse=True)
def mock_system_calls(mocker):
    """Suppress all real system calls made by compartment_manager for every test."""
    mocker.patch("quadletman.services.compartment_manager._setup_service_user")
    mocker.patch("quadletman.services.compartment_manager._teardown_service")
    mocker.patch("quadletman.services.compartment_manager._write_and_reload")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.daemon_reload")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.start_unit")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.stop_unit")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.restart_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_container_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_network_unit")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.delete_volume_dir")
    mocker.patch(
        "quadletman.services.compartment_manager.volume_manager.create_volume_dir",
        return_value="/var/lib/quadletman/volumes/comp/data",
    )
    mocker.patch("quadletman.services.compartment_manager.volume_manager.chown_volume_dir")
    mocker.patch("quadletman.services.compartment_manager.user_manager.sync_helper_users")
    mocker.patch("quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001)


# ---------------------------------------------------------------------------
# Service CRUD
# ---------------------------------------------------------------------------


class TestCreateCompartment:
    async def test_creates_service_in_db(self, db):
        data = CompartmentCreate(id="mycomp")
        comp = await compartment_manager.create_compartment(db, data)
        assert comp.id == "mycomp"

    async def test_setup_service_user_called(self, db, mocker):
        setup_mock = mocker.patch("quadletman.services.compartment_manager._setup_service_user")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp2"))
        setup_mock.assert_called_once_with("comp2")

    async def test_db_rolled_back_on_setup_failure(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager._setup_service_user",
            side_effect=RuntimeError("useradd failed"),
        )
        with pytest.raises(RuntimeError):
            await compartment_manager.create_compartment(db, CompartmentCreate(id="bad"))
        # Service should not exist in DB after rollback
        assert await compartment_manager.get_compartment(db, "bad") is None

    async def test_rejects_duplicate_id(self, db):
        import sqlite3

        data = CompartmentCreate(id="dup")
        await compartment_manager.create_compartment(db, data)
        with pytest.raises(sqlite3.IntegrityError):
            await compartment_manager.create_compartment(db, data)


class TestGetCompartment:
    async def test_returns_none_for_missing(self, db):
        assert await compartment_manager.get_compartment(db, "nonexistent") is None

    async def test_returns_service(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="s1"))
        comp = await compartment_manager.get_compartment(db, "s1")
        assert comp is not None
        assert comp.id == "s1"


class TestListCompartments:
    async def test_empty_list_initially(self, db):
        services = await compartment_manager.list_compartments(db)
        assert services == []

    async def test_returns_created_services(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="a"))
        await compartment_manager.create_compartment(db, CompartmentCreate(id="b"))
        services = await compartment_manager.list_compartments(db)
        ids = {s.id for s in services}
        assert ids == {"a", "b"}


# ---------------------------------------------------------------------------
# Container CRUD
# ---------------------------------------------------------------------------


class TestAddContainer:
    async def test_adds_container_to_db(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        c = await compartment_manager.add_container(
            db, "comp", ContainerCreate(name="web", image="nginx")
        )
        assert c.name == "web"
        assert c.compartment_id == "comp"

    async def test_write_and_reload_called(self, db, mocker):
        wr_mock = mocker.patch("quadletman.services.compartment_manager._write_and_reload")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp2"))
        await compartment_manager.add_container(
            db, "comp2", ContainerCreate(name="app", image="myapp")
        )
        wr_mock.assert_called()

    async def test_raises_for_unknown_service(self, db):
        import sqlite3

        with pytest.raises((sqlite3.IntegrityError, Exception)):
            await compartment_manager.add_container(
                db, "ghost", ContainerCreate(name="web", image="nginx")
            )


class TestUpdateContainer:
    async def test_updates_image(self, db, mocker):
        mocker.patch("quadletman.services.compartment_manager._write_and_reload")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        original = await compartment_manager.add_container(
            db, "comp", ContainerCreate(name="web", image="nginx:1.0")
        )
        updated = await compartment_manager.update_container(
            db,
            "comp",
            original.id,
            ContainerCreate(name="web", image="nginx:2.0"),
        )
        assert updated.image == "nginx:2.0"


class TestDeleteContainer:
    async def test_deletes_container(self, db, mocker):
        mocker.patch("quadletman.services.compartment_manager._write_and_reload")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        c = await compartment_manager.add_container(
            db, "comp", ContainerCreate(name="web", image="nginx")
        )
        await compartment_manager.delete_container(db, "comp", c.id)
        containers = await compartment_manager.list_containers(db, "comp")
        assert not any(x.id == c.id for x in containers)


# ---------------------------------------------------------------------------
# Volume CRUD
# ---------------------------------------------------------------------------


class TestAddVolume:
    async def test_adds_volume(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        vol = await compartment_manager.add_volume(db, "comp", VolumeCreate(name="data"))
        assert vol.name == "data"
        assert vol.compartment_id == "comp"

    async def test_host_path_set(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp2"))
        vol = await compartment_manager.add_volume(db, "comp2", VolumeCreate(name="uploads"))
        assert vol.host_path != ""

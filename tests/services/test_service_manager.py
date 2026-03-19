"""Tests for quadletman/services/compartment_manager.py — orchestration with in-memory DB."""

import pytest

from quadletman.models import CompartmentCreate, ContainerCreate, VolumeCreate
from quadletman.models.sanitized import SafeSlug
from quadletman.services import compartment_manager


def _sid(s: str) -> SafeSlug:
    return SafeSlug.trusted(s, "test")


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
        assert await compartment_manager.get_compartment(db, _sid("bad")) is None

    async def test_rejects_duplicate_id(self, db):
        import sqlite3

        data = CompartmentCreate(id="dup")
        await compartment_manager.create_compartment(db, data)
        with pytest.raises(sqlite3.IntegrityError):
            await compartment_manager.create_compartment(db, data)


class TestGetCompartment:
    async def test_returns_none_for_missing(self, db):
        assert await compartment_manager.get_compartment(db, _sid("nonexistent")) is None

    async def test_returns_service(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="s1"))
        comp = await compartment_manager.get_compartment(db, _sid("s1"))
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
            db, _sid("comp"), ContainerCreate(name="web", image="nginx")
        )
        assert c.name == "web"
        assert c.compartment_id == "comp"

    async def test_write_and_reload_called(self, db, mocker):
        wr_mock = mocker.patch("quadletman.services.compartment_manager._write_and_reload")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp2"))
        await compartment_manager.add_container(
            db, _sid("comp2"), ContainerCreate(name="app", image="myapp")
        )
        wr_mock.assert_called()

    async def test_raises_for_unknown_service(self, db):
        import sqlite3

        with pytest.raises((sqlite3.IntegrityError, Exception)):
            await compartment_manager.add_container(
                db, _sid("ghost"), ContainerCreate(name="web", image="nginx")
            )


class TestUpdateContainer:
    async def test_updates_image(self, db, mocker):
        mocker.patch("quadletman.services.compartment_manager._write_and_reload")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        original = await compartment_manager.add_container(
            db, _sid("comp"), ContainerCreate(name="web", image="nginx:1.0")
        )
        updated = await compartment_manager.update_container(
            db,
            _sid("comp"),
            original.id,
            ContainerCreate(name="web", image="nginx:2.0"),
        )
        assert updated.image == "nginx:2.0"


class TestDeleteContainer:
    async def test_deletes_container(self, db, mocker):
        mocker.patch("quadletman.services.compartment_manager._write_and_reload")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        c = await compartment_manager.add_container(
            db, _sid("comp"), ContainerCreate(name="web", image="nginx")
        )
        await compartment_manager.delete_container(db, _sid("comp"), c.id)
        containers = await compartment_manager.list_containers(db, _sid("comp"))
        assert not any(x.id == c.id for x in containers)


# ---------------------------------------------------------------------------
# Volume CRUD
# ---------------------------------------------------------------------------


class TestCreateCompartmentRollback:
    async def test_delete_service_user_called_on_setup_failure(self, db, mocker):
        """delete_service_user must be called with a SafeSlug on _setup_service_user failure."""
        mocker.patch(
            "quadletman.services.compartment_manager._setup_service_user",
            side_effect=RuntimeError("loginctl failed"),
        )
        delete_mock = mocker.patch(
            "quadletman.services.compartment_manager.user_manager.delete_service_user"
        )
        with pytest.raises(RuntimeError):
            await compartment_manager.create_compartment(db, CompartmentCreate(id="failcomp"))
        delete_mock.assert_called_once()
        # The argument must be a SafeSlug, not a raw string
        arg = delete_mock.call_args.args[0]
        assert isinstance(arg, SafeSlug)

    async def test_delete_service_user_error_suppressed(self, db, mocker):
        """If delete_service_user itself raises, the DB rollback must still run."""
        mocker.patch(
            "quadletman.services.compartment_manager._setup_service_user",
            side_effect=RuntimeError("useradd failed"),
        )
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.delete_service_user",
            side_effect=RuntimeError("userdel also failed"),
        )
        with pytest.raises(RuntimeError, match="useradd failed"):
            await compartment_manager.create_compartment(db, CompartmentCreate(id="failcomp2"))
        # DB record should still have been rolled back
        assert await compartment_manager.get_compartment(db, _sid("failcomp2")) is None


class TestPodCRUD:
    async def test_add_pod_creates_db_record(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=False
        )
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        from quadletman.models import PodCreate

        pod = await compartment_manager.add_pod(db, _sid("comp"), PodCreate(name="mypod"))
        assert pod.name == "mypod"
        assert pod.compartment_id == "comp"

    async def test_list_pods_returns_added(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=False
        )
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        from quadletman.models import PodCreate

        await compartment_manager.add_pod(db, _sid("comp"), PodCreate(name="p1"))
        await compartment_manager.add_pod(db, _sid("comp"), PodCreate(name="p2"))
        pods = await compartment_manager.list_pods(db, _sid("comp"))
        assert {p.name for p in pods} == {"p1", "p2"}

    async def test_delete_pod_removes_record(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=False
        )
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_pod_unit")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        from quadletman.models import PodCreate

        pod = await compartment_manager.add_pod(db, _sid("comp"), PodCreate(name="gone"))
        await compartment_manager.delete_pod(db, _sid("comp"), pod.id)
        pods = await compartment_manager.list_pods(db, _sid("comp"))
        assert not any(p.id == pod.id for p in pods)


class TestImageUnitCRUD:
    async def test_add_image_unit_creates_db_record(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=False
        )
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        from quadletman.models import ImageUnitCreate

        iu = await compartment_manager.add_image_unit(
            db, _sid("comp"), ImageUnitCreate(name="myimage", image="nginx:latest")
        )
        assert iu.name == "myimage"

    async def test_delete_image_unit_removes_record(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=False
        )
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_image_unit")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        from quadletman.models import ImageUnitCreate

        iu = await compartment_manager.add_image_unit(
            db, _sid("comp"), ImageUnitCreate(name="img", image="alpine:latest")
        )
        await compartment_manager.delete_image_unit(db, _sid("comp"), iu.id)
        comp = await compartment_manager.get_compartment(db, _sid("comp"))
        assert not any(i.id == iu.id for i in comp.image_units)


class TestDeleteCompartmentService:
    async def test_delete_compartment_removes_from_db(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=False
        )
        await compartment_manager.create_compartment(db, CompartmentCreate(id="todel"))
        await compartment_manager.delete_compartment(db, _sid("todel"))
        assert await compartment_manager.get_compartment(db, _sid("todel")) is None


class TestUpdateCompartmentService:
    async def test_update_description(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        updated = await compartment_manager.update_compartment(db, _sid("comp"), "new desc")
        assert updated.description == "new desc"

    async def test_update_missing_returns_none(self, db):
        result = await compartment_manager.update_compartment(db, _sid("ghost"), "x")
        assert result is None


class TestAddVolume:
    async def test_adds_volume(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        vol = await compartment_manager.add_volume(db, _sid("comp"), VolumeCreate(name="data"))
        assert vol.name == "data"
        assert vol.compartment_id == "comp"

    async def test_host_path_set(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp2"))
        vol = await compartment_manager.add_volume(db, _sid("comp2"), VolumeCreate(name="uploads"))
        assert vol.host_path != ""

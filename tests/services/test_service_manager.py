"""Tests for quadletman/services/compartment_manager.py — orchestration with in-memory DB."""

import pytest

from quadletman.models import (
    CompartmentCreate,
    ContainerCreate,
    Network,
    NetworkCreate,
    NotificationHookCreate,
    PodCreate,
    VolumeCreate,
)
from quadletman.models.sanitized import (
    SafeIpAddress,
    SafeMultilineStr,
    SafeRegex,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeUUID,
)
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
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_network_unit")
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
        from sqlalchemy.exc import IntegrityError

        data = CompartmentCreate(id="dup")
        await compartment_manager.create_compartment(db, data)
        with pytest.raises(IntegrityError):
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
            db, _sid("comp"), ContainerCreate(qm_name="web", image="nginx")
        )
        assert c.qm_name == "web"
        assert c.compartment_id == "comp"

    async def test_write_and_reload_called(self, db, mocker):
        wr_mock = mocker.patch("quadletman.services.compartment_manager._write_and_reload")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp2"))
        await compartment_manager.add_container(
            db, _sid("comp2"), ContainerCreate(qm_name="app", image="myapp")
        )
        wr_mock.assert_called()

    async def test_raises_for_unknown_service(self, db):
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await compartment_manager.add_container(
                db, _sid("ghost"), ContainerCreate(qm_name="web", image="nginx")
            )


class TestUpdateContainer:
    async def test_updates_image(self, db, mocker):
        mocker.patch("quadletman.services.compartment_manager._write_and_reload")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        original = await compartment_manager.add_container(
            db, _sid("comp"), ContainerCreate(qm_name="web", image="nginx:1.0")
        )
        updated = await compartment_manager.update_container(
            db,
            _sid("comp"),
            original.id,
            ContainerCreate(qm_name="web", image="nginx:2.0"),
        )
        assert updated.image == "nginx:2.0"


class TestDeleteContainer:
    async def test_deletes_container(self, db, mocker):
        mocker.patch("quadletman.services.compartment_manager._write_and_reload")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        c = await compartment_manager.add_container(
            db, _sid("comp"), ContainerCreate(qm_name="web", image="nginx")
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

        pod = await compartment_manager.add_pod(db, _sid("comp"), PodCreate(qm_name="mypod"))
        assert pod.qm_name == "mypod"
        assert pod.compartment_id == "comp"

    async def test_list_pods_returns_added(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=False
        )
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        from quadletman.models import PodCreate

        await compartment_manager.add_pod(db, _sid("comp"), PodCreate(qm_name="p1"))
        await compartment_manager.add_pod(db, _sid("comp"), PodCreate(qm_name="p2"))
        pods = await compartment_manager.list_pods(db, _sid("comp"))
        assert {p.qm_name for p in pods} == {"p1", "p2"}

    async def test_delete_pod_removes_record(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=False
        )
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_pod_unit")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        from quadletman.models import PodCreate

        pod = await compartment_manager.add_pod(db, _sid("comp"), PodCreate(qm_name="gone"))
        await compartment_manager.delete_pod(db, _sid("comp"), pod.id)
        pods = await compartment_manager.list_pods(db, _sid("comp"))
        assert not any(p.id == pod.id for p in pods)


class TestImageCRUD:
    async def test_add_image_creates_db_record(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=False
        )
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        from quadletman.models import ImageCreate

        iu = await compartment_manager.add_image(
            db, _sid("comp"), ImageCreate(qm_name="myimage", image="nginx:latest")
        )
        assert iu.qm_name == "myimage"

    async def test_delete_image_removes_record(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=False
        )
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_image_unit")
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        from quadletman.models import ImageCreate

        iu = await compartment_manager.add_image(
            db, _sid("comp"), ImageCreate(qm_name="img", image="alpine:latest")
        )
        await compartment_manager.delete_image(db, _sid("comp"), iu.id)
        comp = await compartment_manager.get_compartment(db, _sid("comp"))
        assert not any(i.id == iu.id for i in comp.images)


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
        vol = await compartment_manager.add_volume(db, _sid("comp"), VolumeCreate(qm_name="data"))
        assert vol.qm_name == "data"
        assert vol.compartment_id == "comp"

    async def test_host_path_set(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp2"))
        vol = await compartment_manager.add_volume(
            db, _sid("comp2"), VolumeCreate(qm_name="uploads")
        )
        assert vol.qm_host_path != ""


# ---------------------------------------------------------------------------
# Notification hooks
# ---------------------------------------------------------------------------


def _str(v: str) -> SafeStr:
    return SafeStr.trusted(v, "test")


def _uuid(v: str) -> SafeUUID:
    return SafeUUID.trusted(v, "test")


def _ml(v: str) -> SafeMultilineStr:
    return SafeMultilineStr.trusted(v, "test")


class TestNotificationHooks:
    async def test_add_hook_creates_record(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        hook = await compartment_manager.add_notification_hook(
            db,
            _sid("comp"),
            NotificationHookCreate(
                event_type="on_failure",
                webhook_url="https://example.com/hook",
                webhook_secret="",
                enabled=True,
            ),
        )
        assert hook.compartment_id == "comp"
        assert hook.event_type == "on_failure"

    async def test_list_notification_hooks_returns_added(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.add_notification_hook(
            db,
            _sid("comp"),
            NotificationHookCreate(
                event_type="on_start",
                webhook_url="https://example.com/hook",
                webhook_secret="",
                enabled=True,
            ),
        )
        hooks = await compartment_manager.list_notification_hooks(db, _sid("comp"))
        assert len(hooks) == 1
        assert hooks[0].event_type == "on_start"

    async def test_delete_hook_removes_record(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        hook = await compartment_manager.add_notification_hook(
            db,
            _sid("comp"),
            NotificationHookCreate(
                event_type="on_stop",
                webhook_url="https://example.com/hook",
                webhook_secret="",
                enabled=True,
            ),
        )
        await compartment_manager.delete_notification_hook(db, _sid("comp"), _uuid(hook.id))
        hooks = await compartment_manager.list_notification_hooks(db, _sid("comp"))
        assert hooks == []

    async def test_list_all_notification_hooks(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.add_notification_hook(
            db,
            _sid("comp"),
            NotificationHookCreate(
                event_type="on_failure",
                webhook_url="https://example.com/hook",
                webhook_secret="",
                enabled=True,
            ),
        )
        all_hooks = await compartment_manager.list_all_notification_hooks(db)
        assert len(all_hooks) >= 1


# ---------------------------------------------------------------------------
# Process monitor CRUD
# ---------------------------------------------------------------------------


class TestProcessCRUD:
    async def test_upsert_process_creates_new(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        proc, is_new = await compartment_manager.upsert_process(
            db, _sid("comp"), _str("myproc"), _ml("/usr/bin/myproc --flag")
        )
        assert is_new is True
        assert proc.process_name == "myproc"

    async def test_upsert_process_increments_existing(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.upsert_process(
            db, _sid("comp"), _str("myproc"), _ml("/usr/bin/myproc")
        )
        proc2, is_new = await compartment_manager.upsert_process(
            db, _sid("comp"), _str("myproc"), _ml("/usr/bin/myproc")
        )
        assert is_new is False
        assert proc2.times_seen >= 2

    async def test_list_processes_returns_added(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.upsert_process(db, _sid("comp"), _str("bash"), _ml("/bin/bash"))
        procs = await compartment_manager.list_processes(db, _sid("comp"))
        assert any(p.process_name == "bash" for p in procs)

    async def test_set_process_known_creates_pattern(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        proc, _ = await compartment_manager.upsert_process(
            db, _sid("comp"), _str("bash"), _ml("/bin/bash")
        )
        pattern = await compartment_manager.set_process_known(
            db, _sid("comp"), _uuid(proc.id), True
        )
        assert pattern is not None
        assert pattern.process_name == "bash"
        procs = await compartment_manager.list_processes(db, _sid("comp"))
        found = next(p for p in procs if p.id == proc.id)
        assert found.known is True
        assert found.pattern_id == pattern.id

    async def test_delete_process_removes_record(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        proc, _ = await compartment_manager.upsert_process(
            db, _sid("comp"), _str("sh"), _ml("/bin/sh")
        )
        await compartment_manager.delete_process(db, _sid("comp"), _uuid(proc.id))
        procs = await compartment_manager.list_processes(db, _sid("comp"))
        assert not any(p.id == proc.id for p in procs)

    async def test_list_all_processes(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.upsert_process(
            db, _sid("comp"), _str("nginx"), _ml("/usr/sbin/nginx")
        )
        all_procs = await compartment_manager.list_all_processes(db)
        assert any(p.process_name == "nginx" for p in all_procs)


# ---------------------------------------------------------------------------
# Process pattern CRUD
# ---------------------------------------------------------------------------


def _rx(v: str) -> SafeRegex:
    return SafeRegex.trusted(v, "test")


class TestProcessPatterns:
    async def test_create_pattern(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        pat = await compartment_manager.create_process_pattern(
            db, _sid("comp"), _str("nginx"), _rx(r"nginx: worker process.*"), _str("[]")
        )
        assert pat.process_name == "nginx"
        assert pat.cmdline_pattern == r"nginx: worker process.*"

    async def test_list_patterns(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.create_process_pattern(
            db, _sid("comp"), _str("nginx"), _rx(r"nginx.*"), _str("[]")
        )
        patterns = await compartment_manager.list_process_patterns(db, _sid("comp"))
        n_defaults = len(compartment_manager._DEFAULT_PROCESS_PATTERNS)
        assert len(patterns) == n_defaults + 1

    async def test_pattern_auto_links_existing_processes(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        proc, _ = await compartment_manager.upsert_process(
            db, _sid("comp"), _str("nginx"), _ml("nginx: worker process 1")
        )
        assert proc.known is False
        pat = await compartment_manager.create_process_pattern(
            db, _sid("comp"), _str("nginx"), _rx(r"nginx: worker process \d+"), _str("[]")
        )
        procs = await compartment_manager.list_processes(db, _sid("comp"))
        found = next(p for p in procs if p.id == proc.id)
        assert found.known is True
        assert found.pattern_id == pat.id

    async def test_upsert_auto_known_with_pattern(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.create_process_pattern(
            db, _sid("comp"), _str("worker"), _rx(r"/usr/bin/worker --id=[a-z0-9]+"), _str("[]")
        )
        proc, is_new = await compartment_manager.upsert_process(
            db, _sid("comp"), _str("worker"), _ml("/usr/bin/worker --id=abc123")
        )
        assert is_new is True
        assert proc.known is True
        assert proc.pattern_id != ""

    async def test_overlap_detection_rejects(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.upsert_process(db, _sid("comp"), _str("bash"), _ml("/bin/bash"))
        await compartment_manager.create_process_pattern(
            db, _sid("comp"), _str("bash"), _rx(r"/bin/bash"), _str("[]")
        )
        with pytest.raises(ValueError, match="overlaps"):
            await compartment_manager.create_process_pattern(
                db, _sid("comp"), _str("bash"), _rx(r"/bin/.*"), _str("[]")
            )

    async def test_delete_pattern_unlinks_processes(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        proc, _ = await compartment_manager.upsert_process(
            db, _sid("comp"), _str("sh"), _ml("/bin/sh")
        )
        pat = await compartment_manager.create_process_pattern(
            db, _sid("comp"), _str("sh"), _rx(r"/bin/sh"), _str("[]")
        )
        # Process should now be linked
        procs = await compartment_manager.list_processes(db, _sid("comp"))
        assert next(p for p in procs if p.id == proc.id).known is True
        # Delete the pattern
        await compartment_manager.delete_process_pattern(db, _sid("comp"), _uuid(pat.id))
        procs = await compartment_manager.list_processes(db, _sid("comp"))
        found = next(p for p in procs if p.id == proc.id)
        assert found.known is False
        assert found.pattern_id == ""

    async def test_update_pattern_relinks(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.upsert_process(
            db, _sid("comp"), _str("app"), _ml("/app/run --port=8080")
        )
        await compartment_manager.upsert_process(
            db, _sid("comp"), _str("app"), _ml("/app/run --port=9090")
        )
        pat = await compartment_manager.create_process_pattern(
            db, _sid("comp"), _str("app"), _rx(r"/app/run --port=8080"), _str("[]")
        )
        procs = await compartment_manager.list_processes(db, _sid("comp"))
        linked = [p for p in procs if p.pattern_id == pat.id]
        assert len(linked) == 1
        # Update pattern to match both
        updated = await compartment_manager.update_process_pattern(
            db, _sid("comp"), _uuid(pat.id), _rx(r"/app/run --port=\d+"), _str("[]")
        )
        procs = await compartment_manager.list_processes(db, _sid("comp"))
        linked = [p for p in procs if p.pattern_id == updated.id]
        assert len(linked) == 2

    async def test_mark_unknown_unlinks_from_pattern(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        proc, _ = await compartment_manager.upsert_process(
            db, _sid("comp"), _str("bash"), _ml("/bin/bash")
        )
        await compartment_manager.set_process_known(db, _sid("comp"), _uuid(proc.id), True)
        # Now mark as unknown
        await compartment_manager.set_process_known(db, _sid("comp"), _uuid(proc.id), False)
        procs = await compartment_manager.list_processes(db, _sid("comp"))
        found = next(p for p in procs if p.id == proc.id)
        assert found.known is False
        assert found.pattern_id == ""

    async def test_get_pattern_match_count(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.upsert_process(db, _sid("comp"), _str("app"), _ml("/app/run 1"))
        await compartment_manager.upsert_process(db, _sid("comp"), _str("app"), _ml("/app/run 2"))
        pat = await compartment_manager.create_process_pattern(
            db, _sid("comp"), _str("app"), _rx(r"/app/run \d+"), _str("[]")
        )
        count = await compartment_manager.get_pattern_match_count(db, _uuid(pat.id))
        assert count == 2


# ---------------------------------------------------------------------------
# Connection monitor CRUD
# ---------------------------------------------------------------------------


def _ip(v: str) -> SafeIpAddress:
    return SafeIpAddress.trusted(v, "test")


def _rn(v: str) -> SafeResourceName:
    return SafeResourceName.trusted(v, "test")


class TestConnectionCRUD:
    async def test_upsert_connection_creates_new(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        conn, is_new = await compartment_manager.upsert_connection(
            db,
            _sid("comp"),
            _rn("web"),
            _str("tcp"),
            _ip("1.2.3.4"),
            443,
            _str("outbound"),
        )
        assert is_new is True
        assert conn.dst_port == 443

    async def test_upsert_connection_increments_existing(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.upsert_connection(
            db, _sid("comp"), _rn("web"), _str("tcp"), _ip("1.2.3.4"), 80, _str("outbound")
        )
        conn2, is_new = await compartment_manager.upsert_connection(
            db, _sid("comp"), _rn("web"), _str("tcp"), _ip("1.2.3.4"), 80, _str("outbound")
        )
        assert is_new is False
        assert conn2.times_seen >= 2

    async def test_list_connections_returns_added(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.upsert_connection(
            db, _sid("comp"), _rn("api"), _str("tcp"), _ip("8.8.8.8"), 53, _str("outbound")
        )
        conns = await compartment_manager.list_connections(db, _sid("comp"))
        assert any(c.dst_ip == "8.8.8.8" for c in conns)

    async def test_delete_connection_removes_record(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        conn, _ = await compartment_manager.upsert_connection(
            db, _sid("comp"), _rn("web"), _str("tcp"), _ip("9.9.9.9"), 443, _str("outbound")
        )
        await compartment_manager.delete_connection(db, _sid("comp"), _uuid(conn.id))
        conns = await compartment_manager.list_connections(db, _sid("comp"))
        assert not any(c.id == conn.id for c in conns)

    async def test_clear_connections_history(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.upsert_connection(
            db, _sid("comp"), _rn("web"), _str("tcp"), _ip("5.5.5.5"), 80, _str("outbound")
        )
        await compartment_manager.clear_connections_history(db, _sid("comp"))
        conns = await compartment_manager.list_connections(db, _sid("comp"))
        assert conns == []

    async def test_set_connection_monitor_enabled(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.set_connection_monitor_enabled(db, _sid("comp"), True)
        comp = await compartment_manager.get_compartment(db, _sid("comp"))
        assert comp.connection_monitor_enabled is True

    async def test_set_connection_history_retention(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.set_connection_history_retention(db, _sid("comp"), 30)
        comp = await compartment_manager.get_compartment(db, _sid("comp"))
        assert comp.connection_history_retention_days == 30

    async def test_set_process_monitor_enabled(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.set_process_monitor_enabled(db, _sid("comp"), True)
        comp = await compartment_manager.get_compartment(db, _sid("comp"))
        assert comp.process_monitor_enabled is True


# ---------------------------------------------------------------------------
# Allowlist rules CRUD
# ---------------------------------------------------------------------------


class TestAllowlistRules:
    async def test_add_allowlist_rule_creates_record(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        rule = await compartment_manager.add_allowlist_rule(
            db,
            _sid("comp"),
            _str("allow DNS"),
            None,
            _str("udp"),
            _ip("8.8.8.8"),
            53,
            _str("outbound"),
        )
        assert rule.compartment_id == "comp"
        assert rule.dst_port == 53

    async def test_list_allowlist_rules_returns_added(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.add_allowlist_rule(
            db, _sid("comp"), _str("allow http"), None, _str("tcp"), None, 80, _str("outbound")
        )
        rules = await compartment_manager.list_allowlist_rules(db, _sid("comp"))
        assert len(rules) == 1

    async def test_delete_allowlist_rule_removes_record(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        rule = await compartment_manager.add_allowlist_rule(
            db, _sid("comp"), _str("allow https"), None, _str("tcp"), None, 443, _str("outbound")
        )
        await compartment_manager.delete_allowlist_rule(db, _sid("comp"), _uuid(rule.id))
        rules = await compartment_manager.list_allowlist_rules(db, _sid("comp"))
        assert rules == []

    async def test_connection_is_allowlisted(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        rule = await compartment_manager.add_allowlist_rule(
            db, _sid("comp"), _str("allow dns"), None, _str("tcp"), None, 443, _str("outbound")
        )
        rules = [rule]
        result = compartment_manager.connection_is_allowlisted(
            rules, "tcp", _ip("1.2.3.4"), 443, _rn("web"), "outbound"
        )
        assert result is True

    async def test_connection_not_allowlisted(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        rules = await compartment_manager.list_allowlist_rules(db, _sid("comp"))
        result = compartment_manager.connection_is_allowlisted(
            rules, "tcp", _ip("1.2.3.4"), 443, _rn("web"), "outbound"
        )
        assert result is False

    async def test_rule_direction_mismatch_not_matched(self, db):
        """Rule specifying inbound should NOT match outbound connection."""
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        rule = await compartment_manager.add_allowlist_rule(
            db, _sid("comp"), _str("inbound only"), None, _str("tcp"), None, 443, _str("inbound")
        )
        result = compartment_manager.connection_is_allowlisted(
            [rule], "tcp", _ip("1.2.3.4"), 443, _rn("web"), "outbound"
        )
        assert result is False

    async def test_rule_container_name_mismatch_not_matched(self, db):
        """Rule specifying container 'api' should NOT match container 'web'."""
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))

        rule = await compartment_manager.add_allowlist_rule(
            db,
            _sid("comp"),
            _str("api only"),
            SafeStr.trusted("api", "test"),
            _str("tcp"),
            None,
            443,
            None,
        )
        result = compartment_manager.connection_is_allowlisted(
            [rule], "tcp", _ip("1.2.3.4"), 443, _rn("web"), "outbound"
        )
        assert result is False

    async def test_rule_cidr_ip_match(self, db):
        """Rule specifying CIDR should match an IP in the range."""
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        rule = await compartment_manager.add_allowlist_rule(
            db,
            _sid("comp"),
            _str("allow 10.x.x.x"),
            None,
            _str("tcp"),
            SafeIpAddress.trusted("10.0.0.0/8", "test"),
            443,
            None,
        )
        result = compartment_manager.connection_is_allowlisted(
            [rule], "tcp", _ip("10.1.2.3"), 443, _rn("web"), "outbound"
        )
        assert result is True

    async def test_rule_cidr_ip_not_in_range(self, db):
        """CIDR rule should NOT match an IP outside the range."""
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        rule = await compartment_manager.add_allowlist_rule(
            db,
            _sid("comp"),
            _str("10.x.x.x"),
            None,
            _str("tcp"),
            SafeIpAddress.trusted("10.0.0.0/8", "test"),
            443,
            None,
        )
        result = compartment_manager.connection_is_allowlisted(
            [rule], "tcp", _ip("8.8.8.8"), 443, _rn("web"), "outbound"
        )
        assert result is False


class TestCleanup:
    async def test_cleanup_stale_connections_with_retention(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        await compartment_manager.set_connection_history_retention(db, _sid("comp"), 30)
        await compartment_manager.upsert_connection(
            db, _sid("comp"), _rn("web"), _str("tcp"), _ip("1.1.1.1"), 80, _str("outbound")
        )
        # Should not raise even with connections present
        await compartment_manager.cleanup_stale_connections(db)

    async def test_cleanup_stale_connections_no_retention(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="comp"))
        # No retention set — should be a no-op
        await compartment_manager.cleanup_stale_connections(db)


# ---------------------------------------------------------------------------
# write_network_unit must always receive a Network, never a Compartment
# ---------------------------------------------------------------------------


class TestWriteNetworkUnitReceivesNetwork:
    """Guard against passing a Compartment where write_network_unit expects a Network.

    These tests exercise the real code paths (not mocking _write_and_reload) and
    assert that every call to write_network_unit receives a Network instance.
    """

    @pytest.fixture(autouse=True)
    def mock_system_calls(self, mocker):
        """Override the module-level fixture: mock leaf calls but NOT _write_and_reload."""
        mocker.patch("quadletman.services.compartment_manager._setup_service_user")
        mocker.patch("quadletman.services.compartment_manager._teardown_service")
        mocker.patch("quadletman.services.compartment_manager.systemd_manager.daemon_reload")
        mocker.patch("quadletman.services.compartment_manager.systemd_manager.start_unit")
        mocker.patch("quadletman.services.compartment_manager.systemd_manager.stop_unit")
        mocker.patch("quadletman.services.compartment_manager.systemd_manager.restart_unit")
        mocker.patch(
            "quadletman.services.compartment_manager.systemd_manager.get_unit_status",
            return_value={},
        )
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_container_unit")
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_pod_unit")
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_volume_unit")
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_image_unit")
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_timer_unit")
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_container_unit")
        mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_network_unit")
        mocker.patch(
            "quadletman.services.compartment_manager.volume_manager.create_volume_dir",
            return_value="/var/lib/quadletman/volumes/comp/data",
        )
        mocker.patch("quadletman.services.compartment_manager.volume_manager.chown_volume_dir")
        mocker.patch("quadletman.services.compartment_manager.volume_manager.delete_volume_dir")
        mocker.patch("quadletman.services.compartment_manager.user_manager.sync_helper_users")
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001
        )
        self.write_net = mocker.patch(
            "quadletman.services.compartment_manager.quadlet_writer.write_network_unit"
        )

    async def _make_compartment_with_network(self, db, comp_id="comp", net_name="mynet"):
        await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
        await compartment_manager.add_network(db, _sid(comp_id), NetworkCreate(qm_name=net_name))

    def _assert_all_calls_pass_network(self):
        for call in self.write_net.call_args_list:
            net_arg = call.args[1] if len(call.args) > 1 else call.kwargs.get("network")
            assert isinstance(net_arg, Network), (
                f"write_network_unit received {type(net_arg).__name__}, expected Network"
            )

    async def test_add_container_passes_network(self, db):
        await self._make_compartment_with_network(db)
        await compartment_manager.add_container(
            db, _sid("comp"), ContainerCreate(qm_name="web", image="nginx", network="mynet")
        )
        self.write_net.assert_called()
        self._assert_all_calls_pass_network()

    async def test_update_container_passes_network(self, db):
        await self._make_compartment_with_network(db)
        c = await compartment_manager.add_container(
            db, _sid("comp"), ContainerCreate(qm_name="web", image="nginx", network="mynet")
        )
        self.write_net.reset_mock()
        await compartment_manager.update_container(
            db, _sid("comp"), c.id, ContainerCreate(qm_name="web", image="nginx:2", network="mynet")
        )
        self.write_net.assert_called()
        self._assert_all_calls_pass_network()

    async def test_add_pod_passes_network(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=True
        )
        await self._make_compartment_with_network(db)
        # A container using the network (not in a pod) triggers the write
        await compartment_manager.add_container(
            db, _sid("comp"), ContainerCreate(qm_name="web", image="nginx", network="mynet")
        )
        self.write_net.reset_mock()
        await compartment_manager.add_pod(db, _sid("comp"), PodCreate(qm_name="mypod"))
        self.write_net.assert_called()
        self._assert_all_calls_pass_network()

    async def test_resync_compartment_passes_network(self, db, mocker):
        mocker.patch(
            "quadletman.services.compartment_manager.user_manager.user_exists", return_value=True
        )
        await self._make_compartment_with_network(db)
        await compartment_manager.add_container(
            db, _sid("comp"), ContainerCreate(qm_name="web", image="nginx", network="mynet")
        )
        self.write_net.reset_mock()
        await compartment_manager.resync_compartment(db, _sid("comp"))
        self.write_net.assert_called()
        self._assert_all_calls_pass_network()


# ---------------------------------------------------------------------------
# Per-compartment locking
# ---------------------------------------------------------------------------


class TestCompartmentLocking:
    async def test_lock_cleaned_up_on_delete(self, db):
        await compartment_manager.create_compartment(db, CompartmentCreate(id="locktest"))
        assert "locktest" in compartment_manager._compartment_locks
        await compartment_manager.delete_compartment(db, _sid("locktest"))
        assert "locktest" not in compartment_manager._compartment_locks

    async def test_busy_raises_compartment_busy(self, db, monkeypatch):
        monkeypatch.setattr(compartment_manager, "_LOCK_TIMEOUT", 0.1)
        await compartment_manager.create_compartment(db, CompartmentCreate(id="busytest"))
        lock = compartment_manager._get_lock(_sid("busytest"))
        await lock.acquire()
        try:
            with pytest.raises(compartment_manager.CompartmentBusy):
                await compartment_manager.add_volume(
                    db, _sid("busytest"), VolumeCreate(qm_name="vol1")
                )
        finally:
            lock.release()


# ---------------------------------------------------------------------------
# DB-filesystem atomicity rollback tests (Fix #1)
# ---------------------------------------------------------------------------


class TestAddContainerRollback:
    async def test_db_rolled_back_on_filesystem_failure(self, db, mocker):
        """If _write_and_reload fails, the container row must be deleted from DB."""
        await compartment_manager.create_compartment(db, CompartmentCreate(id="rollback"))
        mocker.patch(
            "quadletman.services.compartment_manager._write_and_reload",
            side_effect=RuntimeError("disk full"),
        )
        with pytest.raises(compartment_manager.FileWriteFailed) as exc_info:
            await compartment_manager.add_container(
                db,
                _sid("rollback"),
                ContainerCreate(qm_name="web", image="nginx"),
            )
        assert exc_info.value.rolled_back is True
        # Container must not exist in DB after rollback
        containers = await compartment_manager.list_containers(db, _sid("rollback"))
        assert len(containers) == 0


class TestAddVolumeRollback:
    async def test_db_rolled_back_on_filesystem_failure(self, db, mocker):
        """If volume_manager.create_volume_dir fails, the volume row must be deleted from DB."""
        await compartment_manager.create_compartment(db, CompartmentCreate(id="vrollback"))
        mocker.patch(
            "quadletman.services.compartment_manager.volume_manager.create_volume_dir",
            side_effect=OSError("permission denied"),
        )
        with pytest.raises(compartment_manager.FileWriteFailed) as exc_info:
            await compartment_manager.add_volume(
                db,
                _sid("vrollback"),
                VolumeCreate(qm_name="data"),
            )
        assert exc_info.value.rolled_back is True
        # Volume must not exist in DB after rollback
        volumes = await compartment_manager.list_volumes(db, _sid("vrollback"))
        assert len(volumes) == 0

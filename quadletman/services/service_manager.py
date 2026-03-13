"""High-level service lifecycle orchestration."""

import asyncio
import contextlib
import json
import logging

import aiosqlite

from ..config import settings
from ..models import (
    Container,
    ContainerCreate,
    Service,
    ServiceCreate,
    Volume,
    VolumeCreate,
    new_id,
)
from . import quadlet_writer, systemd_manager, user_manager, volume_manager

logger = logging.getLogger(__name__)

# Per-service lock to prevent concurrent modifications
_service_locks: dict[str, asyncio.Lock] = {}


def _get_lock(service_id: str) -> asyncio.Lock:
    if service_id not in _service_locks:
        _service_locks[service_id] = asyncio.Lock()
    return _service_locks[service_id]


async def _log_event(
    db: aiosqlite.Connection,
    event_type: str,
    message: str,
    service_id: str | None = None,
    container_id: str | None = None,
) -> None:
    await db.execute(
        "INSERT INTO system_events (service_id, container_id, event_type, message) "
        "VALUES (?, ?, ?, ?)",
        (service_id, container_id, event_type, message),
    )


# ---------------------------------------------------------------------------
# Service CRUD
# ---------------------------------------------------------------------------


async def create_service(db: aiosqlite.Connection, data: ServiceCreate) -> Service:
    linux_user = f"{settings.service_user_prefix}{data.id}"

    async with _get_lock(data.id):
        # Insert DB record first (fast fail before system ops)
        await db.execute(
            "INSERT INTO services (id, display_name, description, linux_user) VALUES (?, ?, ?, ?)",
            (data.id, data.display_name, data.description, linux_user),
        )
        await db.commit()

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _setup_service_user, data.id)
        except Exception as exc:
            logger.error("Failed to set up service user for %s: %s", data.id, exc)
            try:
                await db.execute("DELETE FROM services WHERE id = ?", (data.id,))
                await db.commit()
            except Exception as rollback_exc:
                logger.error("Rollback of service record %s also failed: %s", data.id, rollback_exc)
            raise

        await _log_event(db, "create", f"Service {data.id} created", data.id)
        await db.commit()

    return await get_service(db, data.id)


def _setup_service_user(service_id: str) -> None:
    user_manager.create_service_user(service_id)
    user_manager.ensure_quadlet_dir(service_id)
    user_manager.write_storage_conf(service_id)
    user_manager.write_containers_conf(service_id)
    user_manager.enable_linger(service_id)
    # /run/user/{uid} now exists — reset stale storage then migrate with new config
    user_manager.podman_reset(service_id)
    user_manager.podman_migrate(service_id)
    volume_manager.ensure_volumes_base()


async def get_service(db: aiosqlite.Connection, service_id: str) -> Service | None:
    async with db.execute("SELECT * FROM services WHERE id = ?", (service_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    svc = Service.from_row(row)
    svc.containers = await list_containers(db, service_id)
    svc.volumes = await list_volumes(db, service_id)
    return svc


async def list_services(db: aiosqlite.Connection) -> list[Service]:
    async with db.execute("SELECT * FROM services ORDER BY created_at") as cur:
        rows = await cur.fetchall()
    services = []
    for row in rows:
        svc = Service.from_row(row)
        svc.containers = await list_containers(db, svc.id)
        svc.volumes = await list_volumes(db, svc.id)
        services.append(svc)
    return services


async def update_service(
    db: aiosqlite.Connection,
    service_id: str,
    display_name: str | None,
    description: str | None,
) -> Service | None:
    if display_name is not None:
        await db.execute(
            "UPDATE services SET display_name = ? WHERE id = ?",
            (display_name, service_id),
        )
    if description is not None:
        await db.execute(
            "UPDATE services SET description = ? WHERE id = ?",
            (description, service_id),
        )
    await db.commit()
    return await get_service(db, service_id)


async def delete_service(db: aiosqlite.Connection, service_id: str) -> None:
    async with _get_lock(service_id):
        svc = await get_service(db, service_id)
        if svc is None:
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _teardown_service, svc)

        await db.execute("DELETE FROM services WHERE id = ?", (service_id,))
        await db.commit()


def _teardown_service(svc: Service) -> None:
    service_id = svc.id
    # Stop all containers
    for container in svc.containers:
        try:
            systemd_manager.stop_unit(service_id, f"{container.name}.service")
        except Exception as e:
            logger.warning("Could not stop %s: %s", container.name, e)
        quadlet_writer.remove_container_unit(service_id, container.name)

    # Remove network unit if present
    with contextlib.suppress(Exception):
        quadlet_writer.remove_network_unit(service_id)

    if user_manager.user_exists(service_id):
        with contextlib.suppress(Exception):
            systemd_manager.daemon_reload(service_id)
        user_manager.disable_linger(service_id)
        user_manager.delete_service_user(service_id)

    volume_manager.delete_all_service_volumes(service_id)


# ---------------------------------------------------------------------------
# Volume CRUD
# ---------------------------------------------------------------------------


async def add_volume(db: aiosqlite.Connection, service_id: str, data: VolumeCreate) -> Volume:
    vid = new_id()
    await db.execute(
        "INSERT INTO volumes (id, service_id, name, selinux_context, owner_uid) VALUES (?, ?, ?, ?, ?)",
        (vid, service_id, data.name, data.selinux_context, data.owner_uid),
    )
    await db.commit()

    loop = asyncio.get_event_loop()
    host_path = await loop.run_in_executor(
        None,
        volume_manager.create_volume_dir,
        service_id,
        data.name,
        data.selinux_context,
        data.owner_uid,
    )

    await _log_event(db, "volume_create", f"Volume {data.name} created", service_id)
    await db.commit()

    return Volume(
        id=vid,
        service_id=service_id,
        name=data.name,
        selinux_context=data.selinux_context,
        owner_uid=data.owner_uid,
        host_path=host_path,
        created_at="",
    )


async def update_volume_owner(
    db: aiosqlite.Connection, service_id: str, volume_id: str, owner_uid: int
) -> None:
    """Change the owner_uid of a managed volume and re-chown the directory."""
    async with db.execute(
        "SELECT name, selinux_context FROM volumes WHERE id = ? AND service_id = ?",
        (volume_id, service_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError("Volume not found")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        volume_manager.chown_volume_dir,
        service_id,
        row["name"],
        owner_uid,
    )
    await db.execute(
        "UPDATE volumes SET owner_uid = ? WHERE id = ?",
        (owner_uid, volume_id),
    )
    await db.commit()
    await _log_event(
        db, "volume_update", f"Volume {row['name']} owner_uid → {owner_uid}", service_id
    )


async def list_volumes(db: aiosqlite.Connection, service_id: str) -> list[Volume]:
    async with db.execute(
        "SELECT * FROM volumes WHERE service_id = ? ORDER BY created_at",
        (service_id,),
    ) as cur:
        rows = await cur.fetchall()
    result = []
    for row in rows:
        v = Volume(**dict(row), host_path=volume_manager.volume_path(service_id, row["name"]))
        result.append(v)
    return result


async def delete_volume(db: aiosqlite.Connection, service_id: str, volume_id: str) -> None:
    async with db.execute(
        "SELECT name FROM volumes WHERE id = ? AND service_id = ?",
        (volume_id, service_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return

    # Refuse deletion if any container that mounts this volume is currently running.
    containers = await list_containers(db, service_id)
    blocking = []
    for c in containers:
        if any(vm.volume_id == volume_id for vm in c.volumes):
            loop = asyncio.get_event_loop()
            props = await loop.run_in_executor(
                None, systemd_manager.get_unit_status, service_id, f"{c.name}.service"
            )
            if props.get("ActiveState") == "active":
                blocking.append(c.name)
    if blocking:
        raise ValueError(
            f"Volume is mounted by running container(s): {', '.join(blocking)}. "
            "Stop the container(s) first."
        )

    volume_manager.delete_volume_dir(service_id, row["name"])
    await db.execute("DELETE FROM volumes WHERE id = ?", (volume_id,))
    await db.commit()


# ---------------------------------------------------------------------------
# Container CRUD
# ---------------------------------------------------------------------------


async def add_container(
    db: aiosqlite.Connection, service_id: str, data: ContainerCreate
) -> Container:
    cid = new_id()

    if data.containerfile_content:
        loop = asyncio.get_event_loop()
        data.build_context = await loop.run_in_executor(
            None,
            user_manager.write_managed_containerfile,
            service_id,
            data.name,
            data.containerfile_content,
        )
        data.build_file = ""

    volumes_json = json.dumps([vm.model_dump() for vm in data.volumes])
    bind_mounts_json = json.dumps([bm.model_dump() for bm in data.bind_mounts])
    await db.execute(
        """INSERT INTO containers
           (id, service_id, name, image, environment, ports, volumes, labels,
            network, restart_policy, exec_start_pre, memory_limit, cpu_quota,
            depends_on, sort_order, apparmor_profile, build_context, build_file,
            containerfile_content, bind_mounts, run_user, user_ns, uid_map, gid_map)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cid,
            service_id,
            data.name,
            data.image,
            json.dumps(data.environment),
            json.dumps(data.ports),
            volumes_json,
            json.dumps(data.labels),
            data.network,
            data.restart_policy,
            data.exec_start_pre,
            data.memory_limit,
            data.cpu_quota,
            json.dumps(data.depends_on),
            data.sort_order,
            data.apparmor_profile,
            data.build_context,
            data.build_file,
            data.containerfile_content,
            bind_mounts_json,
            data.run_user,
            data.user_ns,
            json.dumps(data.uid_map),
            json.dumps(data.gid_map),
        ),
    )
    await db.commit()

    container = await get_container(db, cid)
    svc_volumes = await list_volumes(db, service_id)
    all_containers = await list_containers(db, service_id)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _write_and_reload,
        service_id,
        container,
        svc_volumes,
        all_containers,
    )

    await _log_event(db, "container_add", f"Container {data.name} added", service_id, cid)
    await db.commit()
    return container


def _write_and_reload(
    service_id: str,
    container: Container,
    volumes: list[Volume],
    all_containers: list[Container],
) -> None:
    # Collect UIDs/GIDs across ALL containers in the service so that sync_helper_users
    # does not delete helpers that other containers still need.
    all_ids = list({int(u) for c in all_containers for u in c.uid_map + c.gid_map})
    user_manager.sync_helper_users(service_id, all_ids)

    if container.network != "host":
        quadlet_writer.write_network_unit(service_id)
    quadlet_writer.write_container_unit(service_id, container, volumes)
    systemd_manager.daemon_reload(service_id)
    unit = f"{container.name}.service"
    props = systemd_manager.get_unit_status(service_id, unit)
    if props.get("ActiveState") == "active":
        systemd_manager.restart_unit(service_id, unit)


async def get_container(db: aiosqlite.Connection, container_id: str) -> Container | None:
    async with db.execute("SELECT * FROM containers WHERE id = ?", (container_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return Container.from_row(row)


async def list_containers(db: aiosqlite.Connection, service_id: str) -> list[Container]:
    async with db.execute(
        "SELECT * FROM containers WHERE service_id = ? ORDER BY sort_order, created_at",
        (service_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [Container.from_row(r) for r in rows]


async def update_container(
    db: aiosqlite.Connection,
    service_id: str,
    container_id: str,
    data: ContainerCreate,
) -> Container | None:
    if data.containerfile_content:
        # Need the container name to derive the build path
        existing = await get_container(db, container_id)
        container_name = existing.name if existing else data.name
        loop = asyncio.get_event_loop()
        data.build_context = await loop.run_in_executor(
            None,
            user_manager.write_managed_containerfile,
            service_id,
            container_name,
            data.containerfile_content,
        )
        data.build_file = ""

    volumes_json = json.dumps([vm.model_dump() for vm in data.volumes])
    bind_mounts_json = json.dumps([bm.model_dump() for bm in data.bind_mounts])
    await db.execute(
        """UPDATE containers SET
            image = ?, environment = ?, ports = ?, volumes = ?, labels = ?,
            network = ?, restart_policy = ?, exec_start_pre = ?,
            memory_limit = ?, cpu_quota = ?, depends_on = ?, sort_order = ?,
            apparmor_profile = ?, build_context = ?, build_file = ?,
            containerfile_content = ?, bind_mounts = ?, run_user = ?, user_ns = ?,
            uid_map = ?, gid_map = ?
           WHERE id = ? AND service_id = ?""",
        (
            data.image,
            json.dumps(data.environment),
            json.dumps(data.ports),
            volumes_json,
            json.dumps(data.labels),
            data.network,
            data.restart_policy,
            data.exec_start_pre,
            data.memory_limit,
            data.cpu_quota,
            json.dumps(data.depends_on),
            data.sort_order,
            data.apparmor_profile,
            data.build_context,
            data.build_file,
            data.containerfile_content,
            bind_mounts_json,
            data.run_user,
            data.user_ns,
            json.dumps(data.uid_map),
            json.dumps(data.gid_map),
            container_id,
            service_id,
        ),
    )
    await db.commit()

    container = await get_container(db, container_id)
    if container is None:
        return None
    svc_volumes = await list_volumes(db, service_id)
    all_containers = await list_containers(db, service_id)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _write_and_reload, service_id, container, svc_volumes, all_containers
    )
    return container


async def delete_container(db: aiosqlite.Connection, service_id: str, container_id: str) -> None:
    async with db.execute(
        "SELECT name FROM containers WHERE id = ? AND service_id = ?",
        (container_id, service_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return
    name = row["name"]

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _stop_and_remove_container,
        service_id,
        name,
    )

    await db.execute("DELETE FROM containers WHERE id = ?", (container_id,))
    await db.commit()


def _stop_and_remove_container(service_id: str, container_name: str) -> None:
    try:
        systemd_manager.stop_unit(service_id, f"{container_name}.service")
    except Exception as e:
        logger.warning("Could not stop container %s: %s", container_name, e)
    quadlet_writer.remove_container_unit(service_id, container_name)
    try:
        systemd_manager.daemon_reload(service_id)
    except Exception as e:
        logger.warning("daemon-reload after container remove failed: %s", e)


# ---------------------------------------------------------------------------
# Service lifecycle actions
# ---------------------------------------------------------------------------


async def enable_service(db: aiosqlite.Connection, service_id: str) -> None:
    containers = await list_containers(db, service_id)
    loop = asyncio.get_event_loop()
    for container in containers:
        await loop.run_in_executor(None, systemd_manager.enable_unit, service_id, container.name)
    await loop.run_in_executor(None, systemd_manager.daemon_reload, service_id)


async def disable_service(db: aiosqlite.Connection, service_id: str) -> None:
    containers = await list_containers(db, service_id)
    loop = asyncio.get_event_loop()
    for container in containers:
        await loop.run_in_executor(None, systemd_manager.disable_unit, service_id, container.name)
    await loop.run_in_executor(None, systemd_manager.daemon_reload, service_id)


async def start_service(db: aiosqlite.Connection, service_id: str) -> list[dict]:
    async with _get_lock(service_id):
        # Ensure subuid/subgid are configured (idempotent — skipped if already set)
        loop = asyncio.get_event_loop()
        username = user_manager._username(service_id)
        await loop.run_in_executor(None, user_manager._setup_subuid_subgid, username)
        containers = await list_containers(db, service_id)
        # Ensure network unit exists for any container using the shared network
        if any(c.network != "host" for c in containers):
            await loop.run_in_executor(None, quadlet_writer.write_network_unit, service_id)
            await loop.run_in_executor(None, systemd_manager.daemon_reload, service_id)
        errors = []
        for container in sorted(containers, key=lambda c: c.sort_order):
            unit = f"{container.name}.service"
            try:
                await loop.run_in_executor(None, systemd_manager.start_unit, service_id, unit)
            except Exception as e:
                logger.error("Failed to start %s: %s", unit, e)
                errors.append({"unit": unit, "error": str(e)})
        await _log_event(db, "start", f"Service {service_id} started", service_id)
        await db.commit()
        return errors


async def stop_service(db: aiosqlite.Connection, service_id: str) -> list[dict]:
    async with _get_lock(service_id):
        containers = await list_containers(db, service_id)
        loop = asyncio.get_event_loop()
        errors = []
        for container in sorted(containers, key=lambda c: c.sort_order, reverse=True):
            unit = f"{container.name}.service"
            try:
                await loop.run_in_executor(None, systemd_manager.stop_unit, service_id, unit)
            except Exception as e:
                logger.warning("Failed to stop %s: %s", unit, e)
                errors.append({"unit": unit, "error": str(e)})
        await _log_event(db, "stop", f"Service {service_id} stopped", service_id)
        await db.commit()
        return errors


async def restart_service(db: aiosqlite.Connection, service_id: str) -> list[dict]:
    await stop_service(db, service_id)
    return await start_service(db, service_id)


async def check_sync(db: aiosqlite.Connection, service_id: str) -> list[dict]:
    """Return out-of-sync quadlet files for a service."""
    svc = await get_service(db, service_id)
    if svc is None:
        return []
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        quadlet_writer.check_service_sync,
        service_id,
        svc.containers,
        svc.volumes,
    )


async def resync_service(db: aiosqlite.Connection, service_id: str) -> None:
    """Re-write all quadlet unit files from DB and reload systemd."""
    svc = await get_service(db, service_id)
    if svc is None:
        return
    loop = asyncio.get_event_loop()

    def _do_resync():
        if any(c.network != "host" for c in svc.containers):
            quadlet_writer.write_network_unit(service_id)
        for container in svc.containers:
            quadlet_writer.write_container_unit(service_id, container, svc.volumes)
        systemd_manager.daemon_reload(service_id)
        # Restart any container that is currently active so new config takes effect
        for container in svc.containers:
            unit = f"{container.name}.service"
            props = systemd_manager.get_unit_status(service_id, unit)
            if props.get("ActiveState") == "active":
                systemd_manager.restart_unit(service_id, unit)

    await loop.run_in_executor(None, _do_resync)


async def export_service_bundle(db: aiosqlite.Connection, service_id: str) -> str | None:
    """Render all quadlet units for a service as a .quadlets bundle string."""
    svc = await get_service(db, service_id)
    if svc is None:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        quadlet_writer.export_service_bundle,
        service_id,
        svc.containers,
        svc.volumes,
    )


async def get_quadlet_files(db: aiosqlite.Connection, service_id: str) -> list[dict]:
    svc = await get_service(db, service_id)
    if svc is None:
        return []
    files = quadlet_writer.render_quadlet_files(service_id, svc.containers, svc.volumes)
    storage_conf = user_manager.read_storage_conf(service_id)
    if storage_conf is not None:
        files.append({"filename": "storage.conf", "content": storage_conf})
    containers_conf = user_manager.read_containers_conf(service_id)
    if containers_conf is not None:
        files.append({"filename": "containers.conf", "content": containers_conf})
    return files


async def get_status(db: aiosqlite.Connection, service_id: str) -> list[dict]:
    containers = await list_containers(db, service_id)
    if not containers:
        return []
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        systemd_manager.get_service_status,
        service_id,
        [c.name for c in containers],
    )

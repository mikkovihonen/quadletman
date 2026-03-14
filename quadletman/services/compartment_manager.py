"""High-level compartment lifecycle orchestration."""

import asyncio
import contextlib
import json
import logging

import aiosqlite

from ..config import settings
from ..models import (
    Compartment,
    CompartmentCreate,
    CompartmentNetworkUpdate,
    Container,
    ContainerCreate,
    ImageUnit,
    ImageUnitCreate,
    Pod,
    PodCreate,
    Volume,
    VolumeCreate,
    new_id,
)
from . import quadlet_writer, systemd_manager, user_manager, volume_manager

logger = logging.getLogger(__name__)

# Per-compartment lock to prevent concurrent modifications
_compartment_locks: dict[str, asyncio.Lock] = {}


def _get_lock(compartment_id: str) -> asyncio.Lock:
    if compartment_id not in _compartment_locks:
        _compartment_locks[compartment_id] = asyncio.Lock()
    return _compartment_locks[compartment_id]


async def _log_event(
    db: aiosqlite.Connection,
    event_type: str,
    message: str,
    compartment_id: str | None = None,
    container_id: str | None = None,
) -> None:
    await db.execute(
        "INSERT INTO system_events (compartment_id, container_id, event_type, message) "
        "VALUES (?, ?, ?, ?)",
        (compartment_id, container_id, event_type, message),
    )


# ---------------------------------------------------------------------------
# Compartment CRUD
# ---------------------------------------------------------------------------


async def create_compartment(db: aiosqlite.Connection, data: CompartmentCreate) -> Compartment:
    linux_user = f"{settings.service_user_prefix}{data.id}"

    async with _get_lock(data.id):
        # Insert DB record first (fast fail before system ops)
        await db.execute(
            "INSERT INTO compartments (id, description, linux_user) VALUES (?, ?, ?)",
            (data.id, data.description, linux_user),
        )
        await db.commit()

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _setup_service_user, data.id)
        except Exception as exc:
            logger.error("Failed to set up compartment user for %s: %s", data.id, exc)
            try:
                await db.execute("DELETE FROM compartments WHERE id = ?", (data.id,))
                await db.commit()
            except Exception as rollback_exc:
                logger.error(
                    "Rollback of compartment record %s also failed: %s", data.id, rollback_exc
                )
            raise

        await _log_event(db, "create", f"Compartment {data.id} created", data.id)
        await db.commit()

    return await get_compartment(db, data.id)


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


async def get_compartment(db: aiosqlite.Connection, compartment_id: str) -> Compartment | None:
    async with db.execute("SELECT * FROM compartments WHERE id = ?", (compartment_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    svc = Compartment.from_row(row)
    svc.containers = await list_containers(db, compartment_id)
    svc.volumes = await list_volumes(db, compartment_id)
    svc.pods = await list_pods(db, compartment_id)
    svc.image_units = await list_image_units(db, compartment_id)
    return svc


async def list_compartments(db: aiosqlite.Connection) -> list[Compartment]:
    async with db.execute("SELECT * FROM compartments ORDER BY created_at") as cur:
        rows = await cur.fetchall()
    compartments = []
    for row in rows:
        svc = Compartment.from_row(row)
        svc.containers = await list_containers(db, svc.id)
        svc.volumes = await list_volumes(db, svc.id)
        svc.pods = await list_pods(db, svc.id)
        svc.image_units = await list_image_units(db, svc.id)
        compartments.append(svc)
    return compartments


async def update_compartment(
    db: aiosqlite.Connection,
    compartment_id: str,
    description: str | None,
) -> Compartment | None:
    if description is not None:
        await db.execute(
            "UPDATE compartments SET description = ? WHERE id = ?",
            (description, compartment_id),
        )
    await db.commit()
    return await get_compartment(db, compartment_id)


async def update_compartment_network(
    db: aiosqlite.Connection,
    compartment_id: str,
    data: CompartmentNetworkUpdate,
) -> Compartment | None:
    """Update the shared network unit config for a compartment and re-write the unit file."""
    svc = await get_compartment(db, compartment_id)
    if svc is None:
        return None
    await db.execute(
        """UPDATE compartments SET
            net_driver = ?, net_subnet = ?, net_gateway = ?,
            net_ipv6 = ?, net_internal = ?, net_dns_enabled = ?
           WHERE id = ?""",
        (
            data.net_driver,
            data.net_subnet,
            data.net_gateway,
            int(data.net_ipv6),
            int(data.net_internal),
            int(data.net_dns_enabled),
            compartment_id,
        ),
    )
    await db.commit()

    svc = await get_compartment(db, compartment_id)
    # Re-write the network unit if at least one container uses the shared network
    if svc and any(c.network != "host" for c in svc.containers):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            _write_network_and_reload,
            compartment_id,
            svc,
        )
    return svc


def _write_network_and_reload(compartment_id: str, svc: Compartment) -> None:
    quadlet_writer.write_network_unit(compartment_id, svc)
    systemd_manager.daemon_reload(compartment_id)


async def delete_compartment(db: aiosqlite.Connection, compartment_id: str) -> None:
    async with _get_lock(compartment_id):
        svc = await get_compartment(db, compartment_id)
        if svc is None:
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _teardown_service, svc)

        await db.execute("DELETE FROM compartments WHERE id = ?", (compartment_id,))
        await db.commit()


def _teardown_service(svc: Compartment) -> None:
    service_id = svc.id
    # Stop all containers
    for container in svc.containers:
        try:
            systemd_manager.stop_unit(service_id, f"{container.name}.service")
        except Exception as e:
            logger.warning("Could not stop %s: %s", container.name, e)
        quadlet_writer.remove_container_unit(service_id, container.name)

    # Remove pod units
    for pod in svc.pods:
        with contextlib.suppress(Exception):
            quadlet_writer.remove_pod_unit(service_id, pod.name)

    # Remove quadlet-managed volume units
    for vol in svc.volumes:
        if vol.use_quadlet:
            with contextlib.suppress(Exception):
                quadlet_writer.remove_volume_unit(service_id, vol.name)

    # Remove image units
    for iu in svc.image_units:
        with contextlib.suppress(Exception):
            quadlet_writer.remove_image_unit(service_id, iu.name)

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


async def add_volume(db: aiosqlite.Connection, compartment_id: str, data: VolumeCreate) -> Volume:
    vid = new_id()
    await db.execute(
        """INSERT INTO volumes
           (id, compartment_id, name, selinux_context, owner_uid,
            use_quadlet, vol_driver, vol_device, vol_options, vol_copy, vol_group)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            vid,
            compartment_id,
            data.name,
            data.selinux_context,
            data.owner_uid,
            int(data.use_quadlet),
            data.vol_driver,
            data.vol_device,
            data.vol_options,
            int(data.vol_copy),
            data.vol_group,
        ),
    )
    await db.commit()

    loop = asyncio.get_event_loop()
    host_path = ""
    if not data.use_quadlet:
        host_path = await loop.run_in_executor(
            None,
            volume_manager.create_volume_dir,
            compartment_id,
            data.name,
            data.selinux_context,
            data.owner_uid,
        )
    else:
        # Write the .volume quadlet file so systemd can create the Podman volume
        vol = Volume(
            id=vid,
            compartment_id=compartment_id,
            name=data.name,
            selinux_context=data.selinux_context,
            owner_uid=data.owner_uid,
            host_path="",
            created_at="",
            use_quadlet=data.use_quadlet,
            vol_driver=data.vol_driver,
            vol_device=data.vol_device,
            vol_options=data.vol_options,
            vol_copy=data.vol_copy,
            vol_group=data.vol_group,
        )
        if user_manager.user_exists(compartment_id):
            await loop.run_in_executor(None, quadlet_writer.write_volume_unit, compartment_id, vol)
            await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)

    await _log_event(db, "volume_create", f"Volume {data.name} created", compartment_id)
    await db.commit()

    return Volume(
        id=vid,
        compartment_id=compartment_id,
        name=data.name,
        selinux_context=data.selinux_context,
        owner_uid=data.owner_uid,
        host_path=host_path,
        created_at="",
        use_quadlet=data.use_quadlet,
        vol_driver=data.vol_driver,
        vol_device=data.vol_device,
        vol_options=data.vol_options,
        vol_copy=data.vol_copy,
        vol_group=data.vol_group,
    )


async def update_volume_owner(
    db: aiosqlite.Connection, compartment_id: str, volume_id: str, owner_uid: int
) -> None:
    """Change the owner_uid of a managed volume and re-chown the directory."""
    async with db.execute(
        "SELECT name, selinux_context FROM volumes WHERE id = ? AND compartment_id = ?",
        (volume_id, compartment_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError("Volume not found")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        volume_manager.chown_volume_dir,
        compartment_id,
        row["name"],
        owner_uid,
    )
    await db.execute(
        "UPDATE volumes SET owner_uid = ? WHERE id = ?",
        (owner_uid, volume_id),
    )
    await db.commit()
    await _log_event(
        db, "volume_update", f"Volume {row['name']} owner_uid → {owner_uid}", compartment_id
    )


async def list_volumes(db: aiosqlite.Connection, compartment_id: str) -> list[Volume]:
    async with db.execute(
        "SELECT * FROM volumes WHERE compartment_id = ? ORDER BY created_at",
        (compartment_id,),
    ) as cur:
        rows = await cur.fetchall()
    result = []
    for row in rows:
        v = Volume.from_row(row)
        if not v.use_quadlet:
            v.host_path = volume_manager.volume_path(compartment_id, row["name"])
        result.append(v)
    return result


async def delete_volume(db: aiosqlite.Connection, compartment_id: str, volume_id: str) -> None:
    async with db.execute(
        "SELECT name FROM volumes WHERE id = ? AND compartment_id = ?",
        (volume_id, compartment_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return

    # Refuse deletion if any container that mounts this volume is currently running.
    containers = await list_containers(db, compartment_id)
    blocking = []
    for c in containers:
        if any(vm.volume_id == volume_id for vm in c.volumes):
            loop = asyncio.get_event_loop()
            props = await loop.run_in_executor(
                None, systemd_manager.get_unit_status, compartment_id, f"{c.name}.service"
            )
            if props.get("ActiveState") == "active":
                blocking.append(c.name)
    if blocking:
        raise ValueError(
            f"Volume is mounted by running container(s): {', '.join(blocking)}. "
            "Stop the container(s) first."
        )

    volume_manager.delete_volume_dir(compartment_id, row["name"])
    await db.execute("DELETE FROM volumes WHERE id = ?", (volume_id,))
    await db.commit()


# ---------------------------------------------------------------------------
# Pod CRUD (P2)
# ---------------------------------------------------------------------------


async def add_pod(db: aiosqlite.Connection, compartment_id: str, data: PodCreate) -> Pod:
    pid = new_id()
    await db.execute(
        "INSERT INTO pods (id, compartment_id, name, network, publish_ports) VALUES (?, ?, ?, ?, ?)",
        (pid, compartment_id, data.name, data.network, json.dumps(data.publish_ports)),
    )
    await db.commit()

    pod = Pod(
        id=pid,
        compartment_id=compartment_id,
        name=data.name,
        network=data.network,
        publish_ports=data.publish_ports,
        created_at="",
    )
    loop = asyncio.get_event_loop()
    if user_manager.user_exists(compartment_id):
        # Write the network unit first if needed, then the pod unit
        svc = await get_compartment(db, compartment_id)
        if any(c.network != "host" and not c.pod_name for c in svc.containers):
            await loop.run_in_executor(None, quadlet_writer.write_network_unit, compartment_id, svc)
        await loop.run_in_executor(None, quadlet_writer.write_pod_unit, compartment_id, pod)
        await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)

    await _log_event(db, "pod_add", f"Pod {data.name} added", compartment_id)
    await db.commit()
    return pod


async def list_pods(db: aiosqlite.Connection, compartment_id: str) -> list[Pod]:
    async with db.execute(
        "SELECT * FROM pods WHERE compartment_id = ? ORDER BY created_at", (compartment_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [Pod.from_row(r) for r in rows]


async def delete_pod(db: aiosqlite.Connection, compartment_id: str, pod_id: str) -> None:
    async with db.execute(
        "SELECT name FROM pods WHERE id = ? AND compartment_id = ?", (pod_id, compartment_id)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return
    pod_name = row["name"]

    # Refuse if any container still references this pod
    containers = await list_containers(db, compartment_id)
    using = [c.name for c in containers if c.pod_name == pod_name]
    if using:
        raise ValueError(
            f"Pod is used by container(s): {', '.join(using)}. "
            "Remove the pod assignment from containers first."
        )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, quadlet_writer.remove_pod_unit, compartment_id, pod_name)
    await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)

    await db.execute("DELETE FROM pods WHERE id = ?", (pod_id,))
    await db.commit()


# ---------------------------------------------------------------------------
# Image unit CRUD (P2)
# ---------------------------------------------------------------------------


async def add_image_unit(
    db: aiosqlite.Connection, compartment_id: str, data: ImageUnitCreate
) -> ImageUnit:
    iid = new_id()
    await db.execute(
        "INSERT INTO image_units (id, compartment_id, name, image, auth_file, pull_policy) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (iid, compartment_id, data.name, data.image, data.auth_file, data.pull_policy),
    )
    await db.commit()

    iu = ImageUnit(
        id=iid,
        compartment_id=compartment_id,
        name=data.name,
        image=data.image,
        auth_file=data.auth_file,
        pull_policy=data.pull_policy,
        created_at="",
    )
    loop = asyncio.get_event_loop()
    if user_manager.user_exists(compartment_id):
        await loop.run_in_executor(None, quadlet_writer.write_image_unit, compartment_id, iu)
        await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)

    await _log_event(db, "image_unit_add", f"Image unit {data.name} added", compartment_id)
    await db.commit()
    return iu


async def list_image_units(db: aiosqlite.Connection, compartment_id: str) -> list[ImageUnit]:
    async with db.execute(
        "SELECT * FROM image_units WHERE compartment_id = ? ORDER BY created_at", (compartment_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [ImageUnit.from_row(r) for r in rows]


async def delete_image_unit(
    db: aiosqlite.Connection, compartment_id: str, image_unit_id: str
) -> None:
    async with db.execute(
        "SELECT name FROM image_units WHERE id = ? AND compartment_id = ?",
        (image_unit_id, compartment_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return
    name = row["name"]

    # Refuse deletion if any container references this image unit
    containers = await list_containers(db, compartment_id)
    blocking = [c.name for c in containers if c.image == f"{name}.image"]
    if blocking:
        raise ValueError(
            f"Image unit is referenced by container(s): {', '.join(blocking)}. "
            "Update or remove the container(s) first."
        )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, quadlet_writer.remove_image_unit, compartment_id, name)
    await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)

    await db.execute("DELETE FROM image_units WHERE id = ?", (image_unit_id,))
    await db.commit()


# ---------------------------------------------------------------------------
# Container CRUD
# ---------------------------------------------------------------------------


async def add_container(
    db: aiosqlite.Connection, compartment_id: str, data: ContainerCreate
) -> Container:
    cid = new_id()

    if data.containerfile_content:
        loop = asyncio.get_event_loop()
        data.build_context = await loop.run_in_executor(
            None,
            user_manager.write_managed_containerfile,
            compartment_id,
            data.name,
            data.containerfile_content,
        )
        data.build_file = ""

    volumes_json = json.dumps([vm.model_dump() for vm in data.volumes])
    bind_mounts_json = json.dumps([bm.model_dump() for bm in data.bind_mounts])
    await db.execute(
        """INSERT INTO containers
           (id, compartment_id, name, image, environment, ports, volumes, labels,
            network, restart_policy, exec_start_pre, memory_limit, cpu_quota,
            depends_on, sort_order, apparmor_profile, build_context, build_file,
            containerfile_content, bind_mounts, run_user, user_ns, uid_map, gid_map,
            health_cmd, health_interval, health_timeout, health_retries,
            health_start_period, health_on_failure, notify_healthy,
            auto_update, environment_file, exec_cmd, entrypoint,
            no_new_privileges, read_only,
            working_dir, drop_caps, add_caps, sysctl, seccomp_profile,
            mask_paths, unmask_paths, privileged,
            hostname, dns, dns_search, dns_option,
            pod_name, log_driver, log_opt, exec_start_post, exec_stop)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?)""",
        (
            cid,
            compartment_id,
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
            data.health_cmd,
            data.health_interval,
            data.health_timeout,
            data.health_retries,
            data.health_start_period,
            data.health_on_failure,
            int(data.notify_healthy),
            data.auto_update,
            data.environment_file,
            data.exec_cmd,
            data.entrypoint,
            int(data.no_new_privileges),
            int(data.read_only),
            data.working_dir,
            json.dumps(data.drop_caps),
            json.dumps(data.add_caps),
            json.dumps(data.sysctl),
            data.seccomp_profile,
            json.dumps(data.mask_paths),
            json.dumps(data.unmask_paths),
            int(data.privileged),
            data.hostname,
            json.dumps(data.dns),
            json.dumps(data.dns_search),
            json.dumps(data.dns_option),
            data.pod_name,
            data.log_driver,
            json.dumps(data.log_opt),
            data.exec_start_post,
            data.exec_stop,
        ),
    )
    await db.commit()

    container = await get_container(db, cid)
    svc_volumes = await list_volumes(db, compartment_id)
    all_containers = await list_containers(db, compartment_id)
    svc = await get_compartment(db, compartment_id)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _write_and_reload,
        compartment_id,
        container,
        svc_volumes,
        all_containers,
        svc,
    )

    await _log_event(db, "container_add", f"Container {data.name} added", compartment_id, cid)
    await db.commit()
    return container


def _write_and_reload(
    compartment_id: str,
    container: Container,
    volumes: list[Volume],
    all_containers: list[Container],
    svc: "Compartment | None" = None,
) -> None:
    # Collect UIDs/GIDs across ALL containers in the compartment so that sync_helper_users
    # does not delete helpers that other containers still need.
    all_ids = list({int(u) for c in all_containers for u in c.uid_map + c.gid_map})
    user_manager.sync_helper_users(compartment_id, all_ids)

    if container.network != "host":
        quadlet_writer.write_network_unit(compartment_id, svc)
    # If the container references an image unit (Image=name.image), Quadlet's generator
    # requires the .image quadlet file to be present at daemon-reload time or it will
    # silently skip generating the container's .service, causing "unit not found" on start.
    if svc and container.image.endswith(".image"):
        image_unit_name = container.image[: -len(".image")]
        for iu in svc.image_units:
            if iu.name == image_unit_name:
                quadlet_writer.write_image_unit(compartment_id, iu)
                break
    quadlet_writer.write_container_unit(compartment_id, container, volumes)
    systemd_manager.daemon_reload(compartment_id)
    unit = f"{container.name}.service"
    props = systemd_manager.get_unit_status(compartment_id, unit)
    if props.get("ActiveState") == "active":
        systemd_manager.restart_unit(compartment_id, unit)


async def get_container(db: aiosqlite.Connection, container_id: str) -> Container | None:
    async with db.execute("SELECT * FROM containers WHERE id = ?", (container_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return Container.from_row(row)


async def list_containers(db: aiosqlite.Connection, compartment_id: str) -> list[Container]:
    async with db.execute(
        "SELECT * FROM containers WHERE compartment_id = ? ORDER BY sort_order, created_at",
        (compartment_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [Container.from_row(r) for r in rows]


async def update_container(
    db: aiosqlite.Connection,
    compartment_id: str,
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
            compartment_id,
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
            uid_map = ?, gid_map = ?,
            health_cmd = ?, health_interval = ?, health_timeout = ?,
            health_retries = ?, health_start_period = ?, health_on_failure = ?,
            notify_healthy = ?, auto_update = ?, environment_file = ?,
            exec_cmd = ?, entrypoint = ?, no_new_privileges = ?, read_only = ?,
            working_dir = ?, drop_caps = ?, add_caps = ?, sysctl = ?,
            seccomp_profile = ?, mask_paths = ?, unmask_paths = ?, privileged = ?,
            hostname = ?, dns = ?, dns_search = ?, dns_option = ?,
            pod_name = ?, log_driver = ?, log_opt = ?,
            exec_start_post = ?, exec_stop = ?
           WHERE id = ? AND compartment_id = ?""",
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
            data.health_cmd,
            data.health_interval,
            data.health_timeout,
            data.health_retries,
            data.health_start_period,
            data.health_on_failure,
            int(data.notify_healthy),
            data.auto_update,
            data.environment_file,
            data.exec_cmd,
            data.entrypoint,
            int(data.no_new_privileges),
            int(data.read_only),
            data.working_dir,
            json.dumps(data.drop_caps),
            json.dumps(data.add_caps),
            json.dumps(data.sysctl),
            data.seccomp_profile,
            json.dumps(data.mask_paths),
            json.dumps(data.unmask_paths),
            int(data.privileged),
            data.hostname,
            json.dumps(data.dns),
            json.dumps(data.dns_search),
            json.dumps(data.dns_option),
            data.pod_name,
            data.log_driver,
            json.dumps(data.log_opt),
            data.exec_start_post,
            data.exec_stop,
            container_id,
            compartment_id,
        ),
    )
    await db.commit()

    container = await get_container(db, container_id)
    if container is None:
        return None
    svc_volumes = await list_volumes(db, compartment_id)
    all_containers = await list_containers(db, compartment_id)
    svc = await get_compartment(db, compartment_id)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _write_and_reload, compartment_id, container, svc_volumes, all_containers, svc
    )
    return container


async def delete_container(
    db: aiosqlite.Connection, compartment_id: str, container_id: str
) -> None:
    async with db.execute(
        "SELECT name FROM containers WHERE id = ? AND compartment_id = ?",
        (container_id, compartment_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return
    name = row["name"]

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _stop_and_remove_container,
        compartment_id,
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
# Compartment lifecycle actions
# ---------------------------------------------------------------------------


async def enable_compartment(db: aiosqlite.Connection, compartment_id: str) -> None:
    containers = await list_containers(db, compartment_id)
    loop = asyncio.get_event_loop()
    for container in containers:
        await loop.run_in_executor(
            None, systemd_manager.enable_unit, compartment_id, container.name
        )
    await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)


async def disable_compartment(db: aiosqlite.Connection, compartment_id: str) -> None:
    containers = await list_containers(db, compartment_id)
    loop = asyncio.get_event_loop()
    for container in containers:
        await loop.run_in_executor(
            None, systemd_manager.disable_unit, compartment_id, container.name
        )
    await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)


async def start_compartment(db: aiosqlite.Connection, compartment_id: str) -> list[dict]:
    async with _get_lock(compartment_id):
        # Ensure subuid/subgid are configured (idempotent — skipped if already set)
        loop = asyncio.get_event_loop()
        username = user_manager._username(compartment_id)
        await loop.run_in_executor(None, user_manager._setup_subuid_subgid, username)
        containers = await list_containers(db, compartment_id)
        svc = await get_compartment(db, compartment_id)
        # Ensure pod units exist
        for pod in svc.pods:
            await loop.run_in_executor(None, quadlet_writer.write_pod_unit, compartment_id, pod)
        # Ensure quadlet-managed volume units exist
        for vol in svc.volumes:
            if vol.use_quadlet:
                await loop.run_in_executor(
                    None, quadlet_writer.write_volume_unit, compartment_id, vol
                )
        # Ensure image units exist
        for iu in svc.image_units:
            await loop.run_in_executor(None, quadlet_writer.write_image_unit, compartment_id, iu)
        # Ensure network unit exists for any container using the shared network (not in a pod)
        if any(c.network != "host" and not c.pod_name for c in containers):
            await loop.run_in_executor(None, quadlet_writer.write_network_unit, compartment_id, svc)
        # Ensure all container unit files are on disk. This is normally done when containers
        # are saved, but files can be missing after a DB reset or manual cleanup.
        for container in containers:
            await loop.run_in_executor(
                None, quadlet_writer.write_container_unit, compartment_id, container, svc.volumes
            )
        # Always reload so Quadlet generates .service files from the unit files written above.
        await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)
        errors = []
        for container in sorted(containers, key=lambda c: c.sort_order):
            unit = f"{container.name}.service"
            try:
                await loop.run_in_executor(None, systemd_manager.start_unit, compartment_id, unit)
            except Exception as e:
                logger.error("Failed to start %s: %s", unit, e)
                errors.append({"unit": unit, "error": str(e)})
        await _log_event(db, "start", f"Compartment {compartment_id} started", compartment_id)
        await db.commit()
        return errors


async def stop_compartment(db: aiosqlite.Connection, compartment_id: str) -> list[dict]:
    async with _get_lock(compartment_id):
        containers = await list_containers(db, compartment_id)
        loop = asyncio.get_event_loop()
        errors = []
        for container in sorted(containers, key=lambda c: c.sort_order, reverse=True):
            unit = f"{container.name}.service"
            try:
                await loop.run_in_executor(None, systemd_manager.stop_unit, compartment_id, unit)
            except Exception as e:
                logger.warning("Failed to stop %s: %s", unit, e)
                errors.append({"unit": unit, "error": str(e)})
        await _log_event(db, "stop", f"Compartment {compartment_id} stopped", compartment_id)
        await db.commit()
        return errors


async def restart_compartment(db: aiosqlite.Connection, compartment_id: str) -> list[dict]:
    await stop_compartment(db, compartment_id)
    return await start_compartment(db, compartment_id)


async def check_sync(db: aiosqlite.Connection, compartment_id: str) -> list[dict]:
    """Return out-of-sync quadlet files for a compartment."""
    svc = await get_compartment(db, compartment_id)
    if svc is None:
        return []
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        quadlet_writer.check_service_sync,
        compartment_id,
        svc.containers,
        svc.volumes,
        svc,
    )


async def resync_compartment(db: aiosqlite.Connection, compartment_id: str) -> None:
    """Re-write all quadlet unit files from DB and reload systemd."""
    svc = await get_compartment(db, compartment_id)
    if svc is None:
        return
    loop = asyncio.get_event_loop()

    def _do_resync():
        for pod in svc.pods:
            quadlet_writer.write_pod_unit(compartment_id, pod)
        for vol in svc.volumes:
            if vol.use_quadlet:
                quadlet_writer.write_volume_unit(compartment_id, vol)
        for iu in svc.image_units:
            quadlet_writer.write_image_unit(compartment_id, iu)
        if any(c.network != "host" and not c.pod_name for c in svc.containers):
            quadlet_writer.write_network_unit(compartment_id, svc)
        for container in svc.containers:
            quadlet_writer.write_container_unit(compartment_id, container, svc.volumes)
        systemd_manager.daemon_reload(compartment_id)
        # Restart any container that is currently active so new config takes effect
        for container in svc.containers:
            unit = f"{container.name}.service"
            props = systemd_manager.get_unit_status(compartment_id, unit)
            if props.get("ActiveState") == "active":
                systemd_manager.restart_unit(compartment_id, unit)

    await loop.run_in_executor(None, _do_resync)


async def export_compartment_bundle(db: aiosqlite.Connection, compartment_id: str) -> str | None:
    """Render all quadlet units for a compartment as a .quadlets bundle string."""
    svc = await get_compartment(db, compartment_id)
    if svc is None:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        quadlet_writer.export_service_bundle,
        compartment_id,
        svc.containers,
        svc.volumes,
        svc,
    )


async def get_quadlet_files(db: aiosqlite.Connection, compartment_id: str) -> list[dict]:
    svc = await get_compartment(db, compartment_id)
    if svc is None:
        return []
    files = quadlet_writer.render_quadlet_files(compartment_id, svc.containers, svc.volumes, svc)
    storage_conf = user_manager.read_storage_conf(compartment_id)
    if storage_conf is not None:
        files.append({"filename": "storage.conf", "content": storage_conf})
    containers_conf = user_manager.read_containers_conf(compartment_id)
    if containers_conf is not None:
        files.append({"filename": "containers.conf", "content": containers_conf})
    return files


async def get_status(db: aiosqlite.Connection, compartment_id: str) -> list[dict]:
    containers = await list_containers(db, compartment_id)
    if not containers:
        return []
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        systemd_manager.get_service_status,
        compartment_id,
        [c.name for c in containers],
    )

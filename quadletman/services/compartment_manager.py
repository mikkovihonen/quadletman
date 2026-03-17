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
    Connection,
    Container,
    ContainerCreate,
    ImageUnit,
    ImageUnitCreate,
    NotificationHook,
    NotificationHookCreate,
    Pod,
    PodCreate,
    Process,
    Secret,
    SecretCreate,
    Template,
    TemplateCreate,
    Timer,
    TimerCreate,
    Volume,
    VolumeCreate,
    new_id,
)
from . import quadlet_writer, secrets_manager, systemd_manager, user_manager, volume_manager

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
    comp = Compartment.from_row(row)
    comp.containers = await list_containers(db, compartment_id)
    comp.volumes = await list_volumes(db, compartment_id)
    comp.pods = await list_pods(db, compartment_id)
    comp.image_units = await list_image_units(db, compartment_id)
    return comp


async def list_compartments(db: aiosqlite.Connection) -> list[Compartment]:
    async with db.execute("SELECT * FROM compartments ORDER BY created_at") as cur:
        rows = await cur.fetchall()
    compartments = []
    for row in rows:
        comp = Compartment.from_row(row)
        comp.containers = await list_containers(db, comp.id)
        comp.volumes = await list_volumes(db, comp.id)
        comp.pods = await list_pods(db, comp.id)
        comp.image_units = await list_image_units(db, comp.id)
        compartments.append(comp)
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
    comp = await get_compartment(db, compartment_id)
    if comp is None:
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

    comp = await get_compartment(db, compartment_id)
    # Re-write the network unit if at least one container uses the shared network
    if comp and any(c.network != "host" for c in comp.containers):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            _write_network_and_reload,
            compartment_id,
            comp,
        )
    return comp


def _write_network_and_reload(compartment_id: str, comp: Compartment) -> None:
    quadlet_writer.write_network_unit(compartment_id, comp)
    systemd_manager.daemon_reload(compartment_id)


async def delete_compartment(db: aiosqlite.Connection, compartment_id: str) -> None:
    async with _get_lock(compartment_id):
        comp = await get_compartment(db, compartment_id)
        if comp is None:
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _teardown_service, comp)

        await db.execute("DELETE FROM compartments WHERE id = ?", (compartment_id,))
        await db.commit()


def _teardown_service(comp: Compartment) -> None:
    service_id = comp.id
    # Stop all containers
    for container in comp.containers:
        try:
            systemd_manager.stop_unit(service_id, f"{container.name}.service")
        except Exception as e:
            logger.warning("Could not stop %s: %s", container.name, e)
        quadlet_writer.remove_container_unit(service_id, container.name)

    # Remove pod units
    for pod in comp.pods:
        with contextlib.suppress(Exception):
            quadlet_writer.remove_pod_unit(service_id, pod.name)

    # Remove quadlet-managed volume units
    for vol in comp.volumes:
        if vol.use_quadlet:
            with contextlib.suppress(Exception):
                quadlet_writer.remove_volume_unit(service_id, vol.name)

    # Remove image units
    for iu in comp.image_units:
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
        comp = await get_compartment(db, compartment_id)
        if any(c.network != "host" and not c.pod_name for c in comp.containers):
            await loop.run_in_executor(
                None, quadlet_writer.write_network_unit, compartment_id, comp
            )
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
            pod_name, log_driver, log_opt, exec_start_post, exec_stop, secrets,
            devices, runtime, service_extra, init,
            memory_reservation, cpu_weight, io_weight, network_aliases)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            json.dumps(data.secrets),
            json.dumps(data.devices),
            data.runtime,
            data.service_extra,
            int(data.init),
            data.memory_reservation,
            data.cpu_weight,
            data.io_weight,
            json.dumps(data.network_aliases),
        ),
    )
    await db.commit()

    container = await get_container(db, cid)
    comp_volumes = await list_volumes(db, compartment_id)
    all_containers = await list_containers(db, compartment_id)
    comp = await get_compartment(db, compartment_id)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _write_and_reload,
        compartment_id,
        container,
        comp_volumes,
        all_containers,
        comp,
    )

    await _log_event(db, "container_add", f"Container {data.name} added", compartment_id, cid)
    await db.commit()
    return container


def _write_and_reload(
    compartment_id: str,
    container: Container,
    volumes: list[Volume],
    all_containers: list[Container],
    comp: "Compartment | None" = None,
) -> None:
    # Collect UIDs/GIDs across ALL containers in the compartment so that sync_helper_users
    # does not delete helpers that other containers still need.
    all_ids = list({int(u) for c in all_containers for u in c.uid_map + c.gid_map})
    user_manager.sync_helper_users(compartment_id, all_ids)

    if container.network != "host":
        quadlet_writer.write_network_unit(compartment_id, comp)
    # If the container references an image unit (Image=name.image), Quadlet's generator
    # requires the .image quadlet file to be present at daemon-reload time or it will
    # silently skip generating the container's .service, causing "unit not found" on start.
    if comp and container.image.endswith(".image"):
        image_unit_name = container.image[: -len(".image")]
        for iu in comp.image_units:
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
            exec_start_post = ?, exec_stop = ?, secrets = ?,
            devices = ?, runtime = ?, service_extra = ?, init = ?,
            memory_reservation = ?, cpu_weight = ?, io_weight = ?, network_aliases = ?
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
            json.dumps(data.secrets),
            json.dumps(data.devices),
            data.runtime,
            data.service_extra,
            int(data.init),
            data.memory_reservation,
            data.cpu_weight,
            data.io_weight,
            json.dumps(data.network_aliases),
            container_id,
            compartment_id,
        ),
    )
    await db.commit()

    container = await get_container(db, container_id)
    if container is None:
        return None
    comp_volumes = await list_volumes(db, compartment_id)
    all_containers = await list_containers(db, compartment_id)
    comp = await get_compartment(db, compartment_id)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _write_and_reload, compartment_id, container, comp_volumes, all_containers, comp
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
        comp = await get_compartment(db, compartment_id)
        # Ensure pod units exist
        for pod in comp.pods:
            await loop.run_in_executor(None, quadlet_writer.write_pod_unit, compartment_id, pod)
        # Ensure quadlet-managed volume units exist
        for vol in comp.volumes:
            if vol.use_quadlet:
                await loop.run_in_executor(
                    None, quadlet_writer.write_volume_unit, compartment_id, vol
                )
        # Ensure image units exist
        for iu in comp.image_units:
            await loop.run_in_executor(None, quadlet_writer.write_image_unit, compartment_id, iu)
        # Ensure network unit exists for any container using the shared network (not in a pod)
        if any(c.network != "host" and not c.pod_name for c in containers):
            await loop.run_in_executor(
                None, quadlet_writer.write_network_unit, compartment_id, comp
            )
        # Ensure all container unit files are on disk. This is normally done when containers
        # are saved, but files can be missing after a DB reset or manual cleanup.
        for container in containers:
            await loop.run_in_executor(
                None, quadlet_writer.write_container_unit, compartment_id, container, comp.volumes
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
    comp = await get_compartment(db, compartment_id)
    if comp is None:
        return []
    timers = await list_timers(db, compartment_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: quadlet_writer.check_service_sync(
            compartment_id, comp.containers, comp.volumes, comp, timers
        ),
    )


async def resync_compartment(db: aiosqlite.Connection, compartment_id: str) -> None:
    """Re-write all quadlet unit files from DB and reload systemd."""
    comp = await get_compartment(db, compartment_id)
    if comp is None:
        return
    loop = asyncio.get_event_loop()

    timers = await list_timers(db, compartment_id)
    container_name_map = {c.id: c.name for c in comp.containers}

    def _do_resync():
        for pod in comp.pods:
            quadlet_writer.write_pod_unit(compartment_id, pod)
        for vol in comp.volumes:
            if vol.use_quadlet:
                quadlet_writer.write_volume_unit(compartment_id, vol)
        for iu in comp.image_units:
            quadlet_writer.write_image_unit(compartment_id, iu)
        if any(c.network != "host" and not c.pod_name for c in comp.containers):
            quadlet_writer.write_network_unit(compartment_id, comp)
        for container in comp.containers:
            quadlet_writer.write_container_unit(compartment_id, container, comp.volumes)
        for timer in timers:
            cname = container_name_map.get(timer.container_id, timer.container_name)
            quadlet_writer.write_timer_unit(compartment_id, timer, cname)
        systemd_manager.daemon_reload(compartment_id)
        # Restart any container that is currently active so new config takes effect
        for container in comp.containers:
            unit = f"{container.name}.service"
            props = systemd_manager.get_unit_status(compartment_id, unit)
            if props.get("ActiveState") == "active":
                systemd_manager.restart_unit(compartment_id, unit)

    await loop.run_in_executor(None, _do_resync)


async def export_compartment_bundle(db: aiosqlite.Connection, compartment_id: str) -> str | None:
    """Render all quadlet units for a compartment as a .quadlets bundle string."""
    comp = await get_compartment(db, compartment_id)
    if comp is None:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        quadlet_writer.export_service_bundle,
        compartment_id,
        comp.containers,
        comp.volumes,
        comp,
    )


async def get_quadlet_files(db: aiosqlite.Connection, compartment_id: str) -> list[dict]:
    comp = await get_compartment(db, compartment_id)
    if comp is None:
        return []
    timers = await list_timers(db, compartment_id)
    files = quadlet_writer.render_quadlet_files(
        compartment_id, comp.containers, comp.volumes, comp, timers
    )
    storage_conf = user_manager.read_storage_conf(compartment_id)
    if storage_conf is not None:
        files.append({"filename": "storage.conf", "content": storage_conf})
    containers_conf = user_manager.read_containers_conf(compartment_id)
    if containers_conf is not None:
        files.append({"filename": "containers.conf", "content": containers_conf})
    return files


async def get_status(
    db: aiosqlite.Connection,
    compartment_id: str,
    containers: list | None = None,
) -> list[dict]:
    if containers is None:
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


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


async def add_secret(db: aiosqlite.Connection, compartment_id: str, data: SecretCreate) -> Secret:
    """Register a secret in the DB and create it in the compartment's podman store."""
    sid = new_id()
    await db.execute(
        "INSERT INTO secrets (id, compartment_id, name) VALUES (?, ?, ?)",
        (sid, compartment_id, data.name),
    )
    await db.commit()
    return Secret(id=sid, compartment_id=compartment_id, name=data.name, created_at="")


async def list_secrets(db: aiosqlite.Connection, compartment_id: str) -> list[Secret]:
    async with db.execute(
        "SELECT * FROM secrets WHERE compartment_id = ? ORDER BY name", (compartment_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [Secret.from_row(r) for r in rows]


async def delete_secret(db: aiosqlite.Connection, compartment_id: str, secret_id: str) -> None:
    async with db.execute(
        "SELECT name FROM secrets WHERE id = ? AND compartment_id = ?",
        (secret_id, compartment_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return
    name = row["name"]
    loop = asyncio.get_event_loop()
    with contextlib.suppress(Exception):
        await loop.run_in_executor(None, secrets_manager.delete_podman_secret, compartment_id, name)
    await db.execute("DELETE FROM secrets WHERE id = ?", (secret_id,))
    await db.commit()


# ---------------------------------------------------------------------------
# Timers
# ---------------------------------------------------------------------------


async def create_timer(db: aiosqlite.Connection, compartment_id: str, data: TimerCreate) -> Timer:
    """Persist a timer and write the .timer unit file."""
    # Resolve container name
    async with db.execute(
        "SELECT name FROM containers WHERE id = ? AND compartment_id = ?",
        (data.container_id, compartment_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError("Container not found")
    container_name = row["name"]

    tid = new_id()
    await db.execute(
        """INSERT INTO timers
           (id, compartment_id, container_id, name,
            on_calendar, on_boot_sec, random_delay_sec, persistent, enabled)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            tid,
            compartment_id,
            data.container_id,
            data.name,
            data.on_calendar,
            data.on_boot_sec,
            data.random_delay_sec,
            int(data.persistent),
            int(data.enabled),
        ),
    )
    await db.commit()

    timer = Timer(
        id=tid,
        compartment_id=compartment_id,
        container_id=data.container_id,
        container_name=container_name,
        name=data.name,
        on_calendar=data.on_calendar,
        on_boot_sec=data.on_boot_sec,
        random_delay_sec=data.random_delay_sec,
        persistent=data.persistent,
        enabled=data.enabled,
        created_at="",
    )
    loop = asyncio.get_event_loop()
    if user_manager.user_exists(compartment_id):
        await loop.run_in_executor(
            None, quadlet_writer.write_timer_unit, compartment_id, timer, container_name
        )
        await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)
    return timer


async def list_timers(db: aiosqlite.Connection, compartment_id: str) -> list[Timer]:
    async with db.execute(
        """SELECT t.*, c.name AS container_name
           FROM timers t
           LEFT JOIN containers c ON c.id = t.container_id
           WHERE t.compartment_id = ?
           ORDER BY t.created_at""",
        (compartment_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [Timer.from_row(r) for r in rows]


async def delete_timer(db: aiosqlite.Connection, compartment_id: str, timer_id: str) -> None:
    async with db.execute(
        "SELECT name FROM timers WHERE id = ? AND compartment_id = ?",
        (timer_id, compartment_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return
    timer_name = row["name"]
    loop = asyncio.get_event_loop()
    if user_manager.user_exists(compartment_id):
        with contextlib.suppress(Exception):
            await loop.run_in_executor(
                None, quadlet_writer.remove_timer_unit, compartment_id, timer_name
            )
        with contextlib.suppress(Exception):
            await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)
    await db.execute("DELETE FROM timers WHERE id = ?", (timer_id,))
    await db.commit()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


async def save_template(db: aiosqlite.Connection, data: TemplateCreate) -> Template:
    """Serialize a compartment's config as a reusable template."""
    comp = await get_compartment(db, data.source_compartment_id)
    if comp is None:
        raise ValueError(f"Compartment '{data.source_compartment_id}' not found")

    config = {
        "containers": [c.model_dump() for c in comp.containers],
        "volumes": [v.model_dump() for v in comp.volumes],
        "pods": [p.model_dump() for p in comp.pods],
        "image_units": [iu.model_dump() for iu in comp.image_units],
    }
    tid = new_id()
    await db.execute(
        "INSERT INTO templates (id, name, description, config_json) VALUES (?, ?, ?, ?)",
        (tid, data.name, data.description, json.dumps(config)),
    )
    await db.commit()
    return Template(
        id=tid,
        name=data.name,
        description=data.description,
        config_json=json.dumps(config),
        created_at="",
    )


async def list_templates(db: aiosqlite.Connection) -> list[Template]:
    async with db.execute("SELECT * FROM templates ORDER BY created_at") as cur:
        rows = await cur.fetchall()
    return [Template.from_row(r) for r in rows]


async def delete_template(db: aiosqlite.Connection, template_id: str) -> None:
    await db.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    await db.commit()


async def create_compartment_from_template(
    db: aiosqlite.Connection,
    template_id: str,
    compartment_id: str,
    description: str,
) -> Compartment:
    """Create a new compartment by instantiating a saved template."""
    from ..models import CompartmentCreate

    async with db.execute("SELECT * FROM templates WHERE id = ?", (template_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError("Template not found")

    config = json.loads(row["config_json"])

    # Create the compartment (provisions the Linux user, quadlet dir, etc.)
    await create_compartment(db, CompartmentCreate(id=compartment_id, description=description))

    # Recreate volumes (without host_path / runtime data)
    for vd in config.get("volumes", []):
        vdata = VolumeCreate(
            name=vd["name"],
            selinux_context=vd.get("selinux_context", "container_file_t"),
            owner_uid=vd.get("owner_uid", 0),
            use_quadlet=vd.get("use_quadlet", False),
            vol_driver=vd.get("vol_driver", ""),
            vol_device=vd.get("vol_device", ""),
            vol_options=vd.get("vol_options", ""),
            vol_copy=vd.get("vol_copy", True),
            vol_group=vd.get("vol_group", ""),
        )
        await add_volume(db, compartment_id, vdata)

    # Recreate pods
    for pd in config.get("pods", []):
        pdata = PodCreate(
            name=pd["name"],
            network=pd.get("network", ""),
            publish_ports=pd.get("publish_ports", []),
        )
        await add_pod(db, compartment_id, pdata)

    # Recreate image units
    for iud in config.get("image_units", []):
        iudata = ImageUnitCreate(
            name=iud["name"],
            image=iud["image"],
            auth_file=iud.get("auth_file", ""),
            pull_policy=iud.get("pull_policy", ""),
        )
        await add_image_unit(db, compartment_id, iudata)

    # Recreate containers (reset build_context so it doesn't reference original paths)
    fresh_comp = await get_compartment(db, compartment_id)
    vol_name_to_id = {v.name: v.id for v in fresh_comp.volumes}

    for cd in config.get("containers", []):
        # Remap volume IDs from source to new compartment
        new_volumes = []
        for vm in cd.get("volumes", []):
            old_vol_id = vm.get("volume_id", "")
            # Find new vol id by matching name
            new_vol_id = old_vol_id
            for sv in config.get("volumes", []):
                if sv.get("id") == old_vol_id:
                    new_vol_id = vol_name_to_id.get(sv["name"], old_vol_id)
                    break
            new_volumes.append({**vm, "volume_id": new_vol_id})

        cdata = ContainerCreate(
            name=cd["name"],
            image=cd["image"],
            environment=cd.get("environment", {}),
            ports=cd.get("ports", []),
            volumes=[
                __import__("quadletman.models", fromlist=["VolumeMount"]).VolumeMount(**v)
                for v in new_volumes
            ],
            labels=cd.get("labels", {}),
            network=cd.get("network", "host"),
            restart_policy=cd.get("restart_policy", "always"),
            exec_start_pre=cd.get("exec_start_pre", ""),
            memory_limit=cd.get("memory_limit", ""),
            cpu_quota=cd.get("cpu_quota", ""),
            depends_on=cd.get("depends_on", []),
            sort_order=cd.get("sort_order", 0),
            apparmor_profile=cd.get("apparmor_profile", ""),
            build_context="",  # reset — build context is not portable
            build_file="",
            containerfile_content=cd.get("containerfile_content", ""),
            bind_mounts=[
                __import__("quadletman.models", fromlist=["BindMount"]).BindMount(**bm)
                for bm in cd.get("bind_mounts", [])
            ],
            run_user=cd.get("run_user", ""),
            user_ns=cd.get("user_ns", ""),
            uid_map=cd.get("uid_map", []),
            gid_map=cd.get("gid_map", []),
            health_cmd=cd.get("health_cmd", ""),
            health_interval=cd.get("health_interval", ""),
            health_timeout=cd.get("health_timeout", ""),
            health_retries=cd.get("health_retries", ""),
            health_start_period=cd.get("health_start_period", ""),
            health_on_failure=cd.get("health_on_failure", ""),
            notify_healthy=cd.get("notify_healthy", False),
            auto_update=cd.get("auto_update", ""),
            environment_file="",  # env files are not portable
            exec_cmd=cd.get("exec_cmd", ""),
            entrypoint=cd.get("entrypoint", ""),
            no_new_privileges=cd.get("no_new_privileges", False),
            read_only=cd.get("read_only", False),
            privileged=cd.get("privileged", False),
            drop_caps=cd.get("drop_caps", []),
            add_caps=cd.get("add_caps", []),
            seccomp_profile=cd.get("seccomp_profile", ""),
            mask_paths=cd.get("mask_paths", []),
            unmask_paths=cd.get("unmask_paths", []),
            sysctl=cd.get("sysctl", {}),
            working_dir=cd.get("working_dir", ""),
            hostname=cd.get("hostname", ""),
            dns=cd.get("dns", []),
            dns_search=cd.get("dns_search", []),
            dns_option=cd.get("dns_option", []),
            pod_name=cd.get("pod_name", ""),
            log_driver=cd.get("log_driver", ""),
            log_opt=cd.get("log_opt", {}),
            exec_start_post=cd.get("exec_start_post", ""),
            exec_stop=cd.get("exec_stop", ""),
            secrets=[],  # secrets are not portable
        )
        await add_container(db, compartment_id, cdata)

    return await get_compartment(db, compartment_id)


# ---------------------------------------------------------------------------
# Notification hooks
# ---------------------------------------------------------------------------


async def add_notification_hook(
    db: aiosqlite.Connection, compartment_id: str, data: NotificationHookCreate
) -> NotificationHook:
    hid = new_id()
    await db.execute(
        """INSERT INTO notification_hooks
           (id, compartment_id, container_name, event_type,
            webhook_url, webhook_secret, enabled)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            hid,
            compartment_id,
            data.container_name,
            data.event_type,
            data.webhook_url,
            data.webhook_secret,
            int(data.enabled),
        ),
    )
    await db.commit()
    return NotificationHook(
        id=hid,
        compartment_id=compartment_id,
        container_name=data.container_name,
        event_type=data.event_type,
        webhook_url=data.webhook_url,
        webhook_secret=data.webhook_secret,
        enabled=data.enabled,
        created_at="",
    )


async def list_notification_hooks(
    db: aiosqlite.Connection, compartment_id: str
) -> list[NotificationHook]:
    async with db.execute(
        "SELECT * FROM notification_hooks WHERE compartment_id = ? ORDER BY created_at",
        (compartment_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [NotificationHook.from_row(r) for r in rows]


async def delete_notification_hook(
    db: aiosqlite.Connection, compartment_id: str, hook_id: str
) -> None:
    await db.execute(
        "DELETE FROM notification_hooks WHERE id = ? AND compartment_id = ?",
        (hook_id, compartment_id),
    )
    await db.commit()


async def list_all_notification_hooks(db: aiosqlite.Connection) -> list[NotificationHook]:
    """Return all enabled hooks across all compartments (used by the notification monitor)."""
    async with db.execute("SELECT * FROM notification_hooks WHERE enabled = 1") as cur:
        rows = await cur.fetchall()
    return [NotificationHook.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Process monitor
# ---------------------------------------------------------------------------


async def upsert_process(
    db: aiosqlite.Connection, compartment_id: str, process_name: str, cmdline: str
) -> tuple[Process, bool]:
    """Insert or increment a process record. Returns (process, is_new).

    On first sight a new record is created with known=False. On subsequent polls
    times_seen and last_seen_at are updated; known is never reset by the monitor.
    """
    async with db.execute(
        """SELECT * FROM processes
           WHERE compartment_id = ? AND process_name = ? AND cmdline = ?""",
        (compartment_id, process_name, cmdline),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        pid = new_id()
        await db.execute(
            """INSERT INTO processes (id, compartment_id, process_name, cmdline)
               VALUES (?, ?, ?, ?)""",
            (pid, compartment_id, process_name, cmdline),
        )
        await db.commit()
        async with db.execute("SELECT * FROM processes WHERE id = ?", (pid,)) as cur:
            row = await cur.fetchone()
        return Process.from_row(row), True
    else:
        await db.execute(
            """UPDATE processes
               SET times_seen = times_seen + 1,
                   last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE compartment_id = ? AND process_name = ? AND cmdline = ?""",
            (compartment_id, process_name, cmdline),
        )
        await db.commit()
        async with db.execute(
            """SELECT * FROM processes
               WHERE compartment_id = ? AND process_name = ? AND cmdline = ?""",
            (compartment_id, process_name, cmdline),
        ) as cur:
            row = await cur.fetchone()
        return Process.from_row(row), False


async def list_processes(db: aiosqlite.Connection, compartment_id: str) -> list[Process]:
    async with db.execute(
        """SELECT * FROM processes WHERE compartment_id = ?
           ORDER BY known ASC, first_seen_at ASC""",
        (compartment_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [Process.from_row(r) for r in rows]


async def list_all_processes(db: aiosqlite.Connection) -> list[Process]:
    """Return all process records across all compartments (used by the monitor loop)."""
    async with db.execute("SELECT * FROM processes") as cur:
        rows = await cur.fetchall()
    return [Process.from_row(r) for r in rows]


async def set_process_known(
    db: aiosqlite.Connection, compartment_id: str, process_id: str, known: bool
) -> None:
    await db.execute(
        "UPDATE processes SET known = ? WHERE id = ? AND compartment_id = ?",
        (int(known), process_id, compartment_id),
    )
    await db.commit()


async def delete_process(db: aiosqlite.Connection, compartment_id: str, process_id: str) -> None:
    """Remove a process record entirely so it can be re-evaluated if seen again."""
    await db.execute(
        "DELETE FROM processes WHERE id = ? AND compartment_id = ?",
        (process_id, compartment_id),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Connection monitor
# ---------------------------------------------------------------------------


async def upsert_connection(
    db: aiosqlite.Connection,
    compartment_id: str,
    container_name: str,
    proto: str,
    dst_ip: str,
    dst_port: int,
) -> tuple[Connection, bool]:
    """Insert or increment a connection record. Returns (connection, is_new).

    On first sight a new record is created with known=False. On subsequent polls
    times_seen and last_seen_at are updated; known is never reset by the monitor.
    """
    async with db.execute(
        """SELECT id FROM connections
           WHERE compartment_id = ? AND container_name = ? AND proto = ?
             AND dst_ip = ? AND dst_port = ?""",
        (compartment_id, container_name, proto, dst_ip, dst_port),
    ) as cur:
        existing = await cur.fetchone()

    if existing is None:
        new_conn_id = new_id()
        await db.execute(
            """INSERT INTO connections
               (id, compartment_id, container_name, proto, dst_ip, dst_port)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (new_conn_id, compartment_id, container_name, proto, dst_ip, dst_port),
        )
        await db.commit()
        async with db.execute("SELECT * FROM connections WHERE id = ?", (new_conn_id,)) as cur:
            row = await cur.fetchone()
        return Connection.from_row(row), True

    await db.execute(
        """UPDATE connections
           SET times_seen = times_seen + 1,
               last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
           WHERE compartment_id = ? AND container_name = ? AND proto = ?
             AND dst_ip = ? AND dst_port = ?""",
        (compartment_id, container_name, proto, dst_ip, dst_port),
    )
    await db.commit()
    async with db.execute(
        """SELECT * FROM connections
           WHERE compartment_id = ? AND container_name = ? AND proto = ?
             AND dst_ip = ? AND dst_port = ?""",
        (compartment_id, container_name, proto, dst_ip, dst_port),
    ) as cur:
        row = await cur.fetchone()
    return Connection.from_row(row), False


async def list_connections(db: aiosqlite.Connection, compartment_id: str) -> list[Connection]:
    async with db.execute(
        """SELECT * FROM connections WHERE compartment_id = ?
           ORDER BY known ASC, container_name ASC, proto ASC, dst_ip ASC, dst_port ASC""",
        (compartment_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [Connection.from_row(r) for r in rows]


async def set_connection_known(
    db: aiosqlite.Connection, compartment_id: str, connection_id: str, known: bool
) -> None:
    await db.execute(
        "UPDATE connections SET known = ? WHERE id = ? AND compartment_id = ?",
        (int(known), connection_id, compartment_id),
    )
    await db.commit()


async def set_connection_monitor_enabled(
    db: aiosqlite.Connection, compartment_id: str, enabled: bool
) -> None:
    await db.execute(
        "UPDATE compartments SET connection_monitor_enabled = ? WHERE id = ?",
        (int(enabled), compartment_id),
    )
    await db.commit()


async def set_process_monitor_enabled(
    db: aiosqlite.Connection, compartment_id: str, enabled: bool
) -> None:
    await db.execute(
        "UPDATE compartments SET process_monitor_enabled = ? WHERE id = ?",
        (int(enabled), compartment_id),
    )
    await db.commit()


async def delete_connection(
    db: aiosqlite.Connection, compartment_id: str, connection_id: str
) -> None:
    """Remove a connection record so it can be re-evaluated if seen again."""
    await db.execute(
        "DELETE FROM connections WHERE id = ? AND compartment_id = ?",
        (connection_id, compartment_id),
    )
    await db.commit()

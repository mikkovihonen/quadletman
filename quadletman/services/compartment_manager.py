"""High-level compartment lifecycle orchestration."""
# ruff: noqa: E402  — AsyncSession._sanitized_enforce_model_safety must be set before project imports

import asyncio
import contextlib
import ipaddress
import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.orm import (
    AllowlistRuleRow,
    BuildUnitRow,
    CompartmentRow,
    ConnectionRow,
    ContainerRow,
    ImageUnitRow,
    NotificationHookRow,
    PodRow,
    ProcessRow,
    SecretRow,
    SystemEventRow,
    TemplateRow,
    TimerRow,
    VolumeRow,
)

# Tell @sanitized.enforce that AsyncSession is a session object, not a data model.
AsyncSession._sanitized_enforce_model_safety = True  # type: ignore[attr-defined]

from ..config import settings
from ..models import (
    AllowlistRule,
    BuildUnit,
    BuildUnitCreate,
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
    sanitized,
)
from ..models.api.common import _validate_row, _validate_rows
from ..models.sanitized import (
    SafeIpAddress,
    SafeMultilineStr,
    SafeResourceName,
    SafeSecretName,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUnitName,
    SafeUUID,
    log_safe,
    resolve_safe_path,
)
from . import quadlet_writer, secrets_manager, systemd_manager, user_manager, volume_manager

logger = logging.getLogger(__name__)


# Per-compartment lock to prevent concurrent modifications
_compartment_locks: dict[str, asyncio.Lock] = {}


@sanitized.enforce
def _get_lock(compartment_id: SafeSlug) -> asyncio.Lock:
    if compartment_id not in _compartment_locks:
        _compartment_locks[compartment_id] = asyncio.Lock()
    return _compartment_locks[compartment_id]


async def _log_event(
    db: AsyncSession,
    event_type: str,
    message: str,
    compartment_id: str | None = None,
    container_id: str | None = None,
) -> None:
    await db.execute(
        insert(SystemEventRow).values(
            compartment_id=compartment_id,
            container_id=container_id,
            event_type=event_type,
            message=message,
        )
    )


@sanitized.enforce
async def create_compartment(db: AsyncSession, data: CompartmentCreate) -> Compartment:
    linux_user = f"{settings.service_user_prefix}{data.id}"

    async with _get_lock(data.id):
        # Insert DB record first (fast fail before system ops)
        await db.execute(
            insert(CompartmentRow).values(
                id=data.id,
                description=data.description,
                linux_user=linux_user,
            )
        )
        await db.commit()

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _setup_service_user, data.id)
        except Exception as exc:
            logger.error("Failed to set up compartment user for %s: %s", log_safe(data.id), exc)
            # Best-effort OS cleanup — remove any partially-created Linux user so retries
            # get a clean slate and orphaned users don't accumulate.
            with contextlib.suppress(Exception):
                await loop.run_in_executor(None, user_manager.delete_service_user, data.id)
            try:
                await db.execute(delete(CompartmentRow).where(CompartmentRow.id == data.id))
                await db.commit()
            except Exception as rollback_exc:
                logger.error(
                    "Rollback of compartment record %s also failed: %s",
                    log_safe(data.id),
                    rollback_exc,
                )
            raise

        await _log_event(db, "create", f"Compartment {data.id} created", data.id)
        await db.commit()

    return await get_compartment(db, data.id)


@sanitized.enforce
def _setup_service_user(service_id: SafeSlug) -> None:
    user_manager.create_service_user(service_id)
    user_manager.ensure_quadlet_dir(service_id)
    user_manager.write_storage_conf(service_id)
    user_manager.write_containers_conf(service_id)
    user_manager.enable_linger(service_id)
    # /run/user/{uid} now exists — reset stale storage then migrate with new config
    user_manager.podman_reset(service_id)
    user_manager.podman_migrate(service_id)
    volume_manager.ensure_volumes_base()


@sanitized.enforce
async def get_compartment(db: AsyncSession, compartment_id: SafeSlug) -> Compartment | None:
    result = await db.execute(
        select(CompartmentRow.__table__).where(CompartmentRow.id == compartment_id)
    )
    comp = await _validate_row(db, Compartment, CompartmentRow.__table__, result.mappings().first())
    if comp is None:
        return None
    comp.containers = await list_containers(db, compartment_id)
    comp.volumes = await list_volumes(db, compartment_id)
    comp.pods = await list_pods(db, compartment_id)
    comp.image_units = await list_image_units(db, compartment_id)
    comp.build_units = await list_build_units(db, compartment_id)
    return comp


@sanitized.enforce
async def list_compartments(db: AsyncSession) -> list[Compartment]:
    result = await db.execute(select(CompartmentRow.__table__).order_by(CompartmentRow.created_at))
    compartments = await _validate_rows(
        db, Compartment, CompartmentRow.__table__, result.mappings().all()
    )
    for comp in compartments:
        comp.containers = await list_containers(db, comp.id)
        comp.volumes = await list_volumes(db, comp.id)
        comp.pods = await list_pods(db, comp.id)
        comp.image_units = await list_image_units(db, comp.id)
        comp.build_units = await list_build_units(db, comp.id)
    return compartments


@sanitized.enforce
async def update_compartment(
    db: AsyncSession,
    compartment_id: SafeSlug,
    description: SafeStr | None,
) -> Compartment | None:
    if description is not None:
        await db.execute(
            update(CompartmentRow)
            .where(CompartmentRow.id == compartment_id)
            .values(description=description)
        )
    await db.commit()
    return await get_compartment(db, compartment_id)


@sanitized.enforce
async def update_compartment_network(
    db: AsyncSession,
    compartment_id: SafeSlug,
    data: CompartmentNetworkUpdate,
) -> Compartment | None:
    """Update the shared network unit config for a compartment and re-write the unit file."""
    comp = await get_compartment(db, compartment_id)
    if comp is None:
        return None
    await db.execute(
        update(CompartmentRow)
        .where(CompartmentRow.id == compartment_id)
        .values(
            net_driver=data.net_driver,
            net_subnet=data.net_subnet,
            net_gateway=data.net_gateway,
            net_ipv6=int(data.net_ipv6),
            net_internal=int(data.net_internal),
            net_dns_enabled=int(data.net_dns_enabled),
        )
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


@sanitized.enforce
def _write_network_and_reload(compartment_id: SafeSlug, comp: Compartment) -> None:
    quadlet_writer.write_network_unit(compartment_id, comp)
    systemd_manager.daemon_reload(compartment_id)


@sanitized.enforce
async def delete_compartment(db: AsyncSession, compartment_id: SafeSlug) -> None:
    async with _get_lock(compartment_id):
        comp = await get_compartment(db, compartment_id)
        if comp is None:
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _teardown_service, comp)

        await db.execute(delete(CompartmentRow).where(CompartmentRow.id == compartment_id))
        await db.commit()


@sanitized.enforce
def _teardown_service(comp: Compartment) -> None:
    service_id = comp.id
    # Stop all containers
    for container in comp.containers:
        try:
            systemd_manager.stop_unit(
                service_id, SafeUnitName.of(f"{container.name}.service", "_teardown_service")
            )
        except Exception as e:
            logger.warning("Could not stop %s: %s", container.name, e)
        quadlet_writer.remove_container_unit(
            service_id, SafeResourceName.of(container.name, "container.name")
        )

    # Remove pod units
    for pod in comp.pods:
        with contextlib.suppress(Exception):
            quadlet_writer.remove_pod_unit(service_id, SafeResourceName.of(pod.name, "pod.name"))

    # Remove quadlet-managed volume units
    for vol in comp.volumes:
        if vol.use_quadlet:
            with contextlib.suppress(Exception):
                quadlet_writer.remove_volume_unit(
                    service_id, SafeResourceName.of(vol.name, "vol.name")
                )

    # Remove image units
    for iu in comp.image_units:
        with contextlib.suppress(Exception):
            quadlet_writer.remove_image_unit(service_id, SafeResourceName.of(iu.name, "iu.name"))

    # Remove network unit if present
    with contextlib.suppress(Exception):
        quadlet_writer.remove_network_unit(service_id)

    if user_manager.user_exists(service_id):
        with contextlib.suppress(Exception):
            systemd_manager.daemon_reload(service_id)
        user_manager.disable_linger(service_id)
        user_manager.delete_service_user(service_id)

    volume_manager.delete_all_service_volumes(service_id)


@sanitized.enforce
async def add_volume(db: AsyncSession, compartment_id: SafeSlug, data: VolumeCreate) -> Volume:
    vid = SafeUUID.trusted(str(uuid.uuid4()), "add_volume")
    await db.execute(
        insert(VolumeRow).values(
            id=vid,
            compartment_id=compartment_id,
            name=data.name,
            selinux_context=data.selinux_context,
            owner_uid=data.owner_uid,
            use_quadlet=int(data.use_quadlet),
            vol_driver=data.vol_driver,
            vol_device=data.vol_device,
            vol_options=data.vol_options,
            vol_copy=int(data.vol_copy),
            vol_group=data.vol_group,
        )
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
            created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_volume"),
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
        created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_volume"),
        use_quadlet=data.use_quadlet,
        vol_driver=data.vol_driver,
        vol_device=data.vol_device,
        vol_options=data.vol_options,
        vol_copy=data.vol_copy,
        vol_group=data.vol_group,
    )


@sanitized.enforce
async def update_volume_owner(
    db: AsyncSession, compartment_id: SafeSlug, volume_id: SafeUUID, owner_uid: int
) -> None:
    """Change the owner_uid of a managed volume and re-chown the directory."""
    result = await db.execute(
        select(VolumeRow.__table__).where(
            VolumeRow.id == volume_id, VolumeRow.compartment_id == compartment_id
        )
    )
    row = result.mappings().first()
    if row is None:
        raise ValueError("Volume not found")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        volume_manager.chown_volume_dir,
        compartment_id,
        SafeResourceName.of(row["name"], "db:volumes.name"),
        owner_uid,
    )
    await db.execute(update(VolumeRow).where(VolumeRow.id == volume_id).values(owner_uid=owner_uid))
    await db.commit()
    await _log_event(
        db, "volume_update", f"Volume {row['name']} owner_uid → {owner_uid}", compartment_id
    )


@sanitized.enforce
async def list_volumes(db: AsyncSession, compartment_id: SafeSlug) -> list[Volume]:
    result = await db.execute(
        select(VolumeRow.__table__)
        .where(VolumeRow.compartment_id == compartment_id)
        .order_by(VolumeRow.created_at)
    )
    volumes = await _validate_rows(db, Volume, VolumeRow.__table__, result.mappings().all())
    for v in volumes:
        if not v.use_quadlet:
            v.host_path = SafeStr.trusted(
                resolve_safe_path(
                    settings.volumes_base,
                    f"{compartment_id}/{v.name}",
                ),
                "internally constructed",
            )
    return volumes


@sanitized.enforce
async def delete_volume(db: AsyncSession, compartment_id: SafeSlug, volume_id: SafeUUID) -> None:
    result = await db.execute(
        select(VolumeRow.__table__).where(
            VolumeRow.id == volume_id, VolumeRow.compartment_id == compartment_id
        )
    )
    row = result.mappings().first()
    if row is None:
        return

    # Refuse deletion if any container that mounts this volume is currently running.
    containers = await list_containers(db, compartment_id)
    blocking = []
    for c in containers:
        if any(vm.volume_id == volume_id for vm in c.volumes):
            loop = asyncio.get_event_loop()
            props = await loop.run_in_executor(
                None,
                systemd_manager.get_unit_status,
                compartment_id,
                SafeUnitName.of(f"{c.name}.service", "update_volume_owner"),
            )
            if props.get("ActiveState") == "active":
                blocking.append(c.name)
    if blocking:
        raise ValueError(
            f"Volume is mounted by running container(s): {', '.join(blocking)}. "
            "Stop the container(s) first."
        )

    volume_manager.delete_volume_dir(
        compartment_id, SafeResourceName.of(row["name"], "db:volumes.name")
    )
    await db.execute(delete(VolumeRow).where(VolumeRow.id == volume_id))
    await db.commit()


@sanitized.enforce
async def add_pod(db: AsyncSession, compartment_id: SafeSlug, data: PodCreate) -> Pod:
    pid = SafeUUID.trusted(str(uuid.uuid4()), "add_pod")
    await db.execute(
        insert(PodRow).values(
            id=pid,
            compartment_id=compartment_id,
            name=data.name,
            network=data.network,
            publish_ports=json.dumps(data.publish_ports),
        )
    )
    await db.commit()

    pod = Pod(
        id=pid,
        compartment_id=compartment_id,
        name=data.name,
        network=data.network,
        publish_ports=data.publish_ports,
        created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_pod"),
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


@sanitized.enforce
async def list_pods(db: AsyncSession, compartment_id: SafeSlug) -> list[Pod]:
    result = await db.execute(
        select(PodRow.__table__)
        .where(PodRow.compartment_id == compartment_id)
        .order_by(PodRow.created_at)
    )
    return await _validate_rows(db, Pod, PodRow.__table__, result.mappings().all())


@sanitized.enforce
async def delete_pod(db: AsyncSession, compartment_id: SafeSlug, pod_id: SafeUUID) -> None:
    result = await db.execute(
        select(PodRow.__table__).where(PodRow.id == pod_id, PodRow.compartment_id == compartment_id)
    )
    row = result.mappings().first()
    if row is None:
        return
    pod_name = SafeResourceName.of(row["name"], "db:pods.name")

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

    await db.execute(delete(PodRow).where(PodRow.id == pod_id))
    await db.commit()


@sanitized.enforce
async def add_image_unit(
    db: AsyncSession, compartment_id: SafeSlug, data: ImageUnitCreate
) -> ImageUnit:
    iid = SafeUUID.trusted(str(uuid.uuid4()), "add_image_unit")
    await db.execute(
        insert(ImageUnitRow).values(
            id=iid,
            compartment_id=compartment_id,
            name=data.name,
            image=data.image,
            auth_file=data.auth_file,
            pull_policy=data.pull_policy,
        )
    )
    await db.commit()

    iu = ImageUnit(
        id=iid,
        compartment_id=compartment_id,
        name=data.name,
        image=data.image,
        auth_file=data.auth_file,
        pull_policy=data.pull_policy,
        created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_image_unit"),
    )
    loop = asyncio.get_event_loop()
    if user_manager.user_exists(compartment_id):
        await loop.run_in_executor(None, quadlet_writer.write_image_unit, compartment_id, iu)
        await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)

    await _log_event(db, "image_unit_add", f"Image unit {data.name} added", compartment_id)
    await db.commit()
    return iu


@sanitized.enforce
async def list_image_units(db: AsyncSession, compartment_id: SafeSlug) -> list[ImageUnit]:
    result = await db.execute(
        select(ImageUnitRow.__table__)
        .where(ImageUnitRow.compartment_id == compartment_id)
        .order_by(ImageUnitRow.created_at)
    )
    return await _validate_rows(db, ImageUnit, ImageUnitRow.__table__, result.mappings().all())


@sanitized.enforce
async def delete_image_unit(
    db: AsyncSession, compartment_id: SafeSlug, image_unit_id: SafeUUID
) -> None:
    result = await db.execute(
        select(ImageUnitRow.__table__).where(
            ImageUnitRow.id == image_unit_id, ImageUnitRow.compartment_id == compartment_id
        )
    )
    row = result.mappings().first()
    if row is None:
        return
    name = SafeResourceName.of(row["name"], "db:image_units.name")

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

    await db.execute(delete(ImageUnitRow).where(ImageUnitRow.id == image_unit_id))
    await db.commit()


# ---------------------------------------------------------------------------
# Build units
# ---------------------------------------------------------------------------


@sanitized.enforce
async def list_build_units(db: AsyncSession, compartment_id: SafeSlug) -> list[BuildUnit]:
    result = await db.execute(
        select(BuildUnitRow.__table__)
        .where(BuildUnitRow.compartment_id == compartment_id)
        .order_by(BuildUnitRow.created_at)
    )
    return await _validate_rows(db, BuildUnit, BuildUnitRow.__table__, result.mappings().all())


@sanitized.enforce
async def add_build_unit(
    db: AsyncSession, compartment_id: SafeSlug, data: BuildUnitCreate
) -> BuildUnit:
    bid = SafeUUID.trusted(str(uuid.uuid4()), "add_build_unit")
    now = SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_build_unit")

    # Write Containerfile to disk if content is provided
    if data.containerfile_content:
        loop = asyncio.get_event_loop()
        data.build_context = SafeStr.trusted(
            await loop.run_in_executor(
                None,
                user_manager.write_managed_containerfile,
                compartment_id,
                data.name,
                data.containerfile_content,
            ),
            "build_context",
        )

    await db.execute(
        insert(BuildUnitRow).values(
            id=bid,
            compartment_id=compartment_id,
            name=data.name,
            image_tag=data.image_tag,
            containerfile_content=data.containerfile_content,
            build_context=data.build_context,
            build_file=data.build_file,
            annotation=json.dumps(data.annotation),
            arch=data.arch,
            auth_file=data.auth_file,
            containers_conf_module=data.containers_conf_module,
            dns=json.dumps(data.dns),
            dns_option=json.dumps(data.dns_option),
            dns_search=json.dumps(data.dns_search),
            env=json.dumps(data.env),
            force_rm=data.force_rm,
            global_args=json.dumps(data.global_args),
            group_add=json.dumps(data.group_add),
            label=json.dumps(data.label),
            network=data.network,
            podman_args=json.dumps(data.podman_args),
            pull=data.pull,
            secret=json.dumps(data.secret),
            target=data.target,
            tls_verify=data.tls_verify,
            variant=data.variant,
            volume=json.dumps(data.volume),
            service_name=data.service_name,
            retry=data.retry,
            retry_delay=data.retry_delay,
            build_args=json.dumps(data.build_args),
            ignore_file=data.ignore_file,
        )
    )
    await db.commit()

    bu = BuildUnit(
        id=bid,
        compartment_id=compartment_id,
        name=data.name,
        image_tag=data.image_tag,
        containerfile_content=data.containerfile_content,
        build_context=data.build_context,
        build_file=data.build_file,
        created_at=now,
        updated_at=now,
        annotation=data.annotation,
        arch=data.arch,
        auth_file=data.auth_file,
        containers_conf_module=data.containers_conf_module,
        dns=data.dns,
        dns_option=data.dns_option,
        dns_search=data.dns_search,
        env=data.env,
        force_rm=data.force_rm,
        global_args=data.global_args,
        group_add=data.group_add,
        label=data.label,
        network=data.network,
        podman_args=data.podman_args,
        pull=data.pull,
        secret=data.secret,
        target=data.target,
        tls_verify=data.tls_verify,
        variant=data.variant,
        volume=data.volume,
        service_name=data.service_name,
        retry=data.retry,
        retry_delay=data.retry_delay,
        build_args=data.build_args,
        ignore_file=data.ignore_file,
    )

    loop = asyncio.get_event_loop()
    if user_manager.user_exists(compartment_id):
        await loop.run_in_executor(None, quadlet_writer.write_build_unit, compartment_id, bu)
        await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)

    await _log_event(db, "build_unit_add", f"Build unit {data.name} added", compartment_id)
    await db.commit()
    return bu


@sanitized.enforce
async def update_build_unit(
    db: AsyncSession,
    compartment_id: SafeSlug,
    build_unit_id: SafeUUID,
    data: BuildUnitCreate,
) -> BuildUnit | None:
    # Write Containerfile to disk if content is provided
    if data.containerfile_content:
        loop = asyncio.get_event_loop()
        data.build_context = SafeStr.trusted(
            await loop.run_in_executor(
                None,
                user_manager.write_managed_containerfile,
                compartment_id,
                data.name,
                data.containerfile_content,
            ),
            "build_context",
        )

    result = await db.execute(
        update(BuildUnitRow)
        .where(BuildUnitRow.id == build_unit_id, BuildUnitRow.compartment_id == compartment_id)
        .values(
            image_tag=data.image_tag,
            containerfile_content=data.containerfile_content,
            build_context=data.build_context,
            build_file=data.build_file,
            annotation=json.dumps(data.annotation),
            arch=data.arch,
            auth_file=data.auth_file,
            containers_conf_module=data.containers_conf_module,
            dns=json.dumps(data.dns),
            dns_option=json.dumps(data.dns_option),
            dns_search=json.dumps(data.dns_search),
            env=json.dumps(data.env),
            force_rm=data.force_rm,
            global_args=json.dumps(data.global_args),
            group_add=json.dumps(data.group_add),
            label=json.dumps(data.label),
            network=data.network,
            podman_args=json.dumps(data.podman_args),
            pull=data.pull,
            secret=json.dumps(data.secret),
            target=data.target,
            tls_verify=data.tls_verify,
            variant=data.variant,
            volume=json.dumps(data.volume),
            service_name=data.service_name,
            retry=data.retry,
            retry_delay=data.retry_delay,
            build_args=json.dumps(data.build_args),
            ignore_file=data.ignore_file,
        )
    )
    if result.rowcount == 0:
        return None
    await db.commit()

    bu_row = await db.execute(
        select(BuildUnitRow.__table__).where(BuildUnitRow.id == build_unit_id)
    )
    bu = await _validate_row(db, BuildUnit, BuildUnitRow.__table__, bu_row.mappings().first())
    if bu is None:
        return None

    loop = asyncio.get_event_loop()
    if user_manager.user_exists(compartment_id):
        await loop.run_in_executor(None, quadlet_writer.write_build_unit, compartment_id, bu)
        await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)

    await _log_event(db, "build_unit_update", f"Build unit {data.name} updated", compartment_id)
    await db.commit()
    return bu


@sanitized.enforce
async def delete_build_unit(
    db: AsyncSession, compartment_id: SafeSlug, build_unit_id: SafeUUID
) -> None:
    result = await db.execute(
        select(BuildUnitRow.__table__).where(
            BuildUnitRow.id == build_unit_id, BuildUnitRow.compartment_id == compartment_id
        )
    )
    row = result.mappings().first()
    if row is None:
        return
    name = SafeResourceName.of(row["name"], "db:build_units.name")

    # Refuse deletion if any container references this build unit
    containers = await list_containers(db, compartment_id)
    blocking = [c.name for c in containers if c.build_unit_name == name]
    if blocking:
        raise ValueError(
            f"Build unit is referenced by container(s): {', '.join(blocking)}. "
            "Update or remove the container(s) first."
        )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, quadlet_writer.remove_build_unit, compartment_id, name)
    await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)

    await db.execute(delete(BuildUnitRow).where(BuildUnitRow.id == build_unit_id))
    await db.commit()


@sanitized.enforce
async def add_container(
    db: AsyncSession, compartment_id: SafeSlug, data: ContainerCreate
) -> Container:
    cid = SafeUUID.trusted(str(uuid.uuid4()), "add_container")

    await db.execute(
        insert(ContainerRow).values(
            id=cid,
            compartment_id=compartment_id,
            name=data.name,
            image=data.image,
            environment=json.dumps(data.environment),
            ports=json.dumps(data.ports),
            volumes=json.dumps([vm.model_dump() for vm in data.volumes]),
            labels=json.dumps(data.labels),
            network=data.network,
            restart_policy=data.restart_policy,
            exec_start_pre=data.exec_start_pre,
            memory_limit=data.memory_limit,
            cpu_quota=data.cpu_quota,
            depends_on=json.dumps(data.depends_on),
            sort_order=data.sort_order,
            apparmor_profile=data.apparmor_profile,
            build_unit_name=data.build_unit_name,
            bind_mounts=json.dumps([bm.model_dump() for bm in data.bind_mounts]),
            run_user=data.run_user,
            user_ns=data.user_ns,
            uid_map=json.dumps(data.uid_map),
            gid_map=json.dumps(data.gid_map),
            health_cmd=data.health_cmd,
            health_interval=data.health_interval,
            health_timeout=data.health_timeout,
            health_retries=data.health_retries,
            health_start_period=data.health_start_period,
            health_on_failure=data.health_on_failure,
            notify_healthy=int(data.notify_healthy),
            auto_update=data.auto_update,
            environment_file=data.environment_file,
            exec_cmd=data.exec_cmd,
            entrypoint=data.entrypoint,
            no_new_privileges=int(data.no_new_privileges),
            read_only=int(data.read_only),
            working_dir=data.working_dir,
            drop_caps=json.dumps(data.drop_caps),
            add_caps=json.dumps(data.add_caps),
            sysctl=json.dumps(data.sysctl),
            seccomp_profile=data.seccomp_profile,
            mask_paths=json.dumps(data.mask_paths),
            unmask_paths=json.dumps(data.unmask_paths),
            privileged=int(data.privileged),
            hostname=data.hostname,
            dns=json.dumps(data.dns),
            dns_search=json.dumps(data.dns_search),
            dns_option=json.dumps(data.dns_option),
            pod_name=data.pod_name,
            log_driver=data.log_driver,
            log_opt=json.dumps(data.log_opt),
            exec_start_post=data.exec_start_post,
            exec_stop=data.exec_stop,
            secrets=json.dumps(data.secrets),
            devices=json.dumps(data.devices),
            runtime=data.runtime,
            service_extra=data.service_extra,
            init=int(data.init),
            memory_reservation=data.memory_reservation,
            cpu_weight=data.cpu_weight,
            io_weight=data.io_weight,
            network_aliases=json.dumps(data.network_aliases),
        )
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


@sanitized.enforce
def _write_and_reload(
    compartment_id: SafeSlug,
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
    unit = SafeUnitName.of(f"{container.name}.service", "_write_and_reload")
    props = systemd_manager.get_unit_status(compartment_id, unit)
    if props.get("ActiveState") == "active":
        systemd_manager.restart_unit(compartment_id, unit)


@sanitized.enforce
async def get_container(db: AsyncSession, container_id: SafeUUID) -> Container | None:
    result = await db.execute(select(ContainerRow.__table__).where(ContainerRow.id == container_id))
    return await _validate_row(db, Container, ContainerRow.__table__, result.mappings().first())


@sanitized.enforce
async def list_containers(db: AsyncSession, compartment_id: SafeSlug) -> list[Container]:
    result = await db.execute(
        select(ContainerRow.__table__)
        .where(ContainerRow.compartment_id == compartment_id)
        .order_by(ContainerRow.sort_order, ContainerRow.created_at)
    )
    return await _validate_rows(db, Container, ContainerRow.__table__, result.mappings().all())


@sanitized.enforce
async def update_container(
    db: AsyncSession,
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    data: ContainerCreate,
) -> Container | None:
    await db.execute(
        update(ContainerRow)
        .where(ContainerRow.id == container_id, ContainerRow.compartment_id == compartment_id)
        .values(
            image=data.image,
            environment=json.dumps(data.environment),
            ports=json.dumps(data.ports),
            volumes=json.dumps([vm.model_dump() for vm in data.volumes]),
            labels=json.dumps(data.labels),
            network=data.network,
            restart_policy=data.restart_policy,
            exec_start_pre=data.exec_start_pre,
            memory_limit=data.memory_limit,
            cpu_quota=data.cpu_quota,
            depends_on=json.dumps(data.depends_on),
            sort_order=data.sort_order,
            apparmor_profile=data.apparmor_profile,
            build_unit_name=data.build_unit_name,
            bind_mounts=json.dumps([bm.model_dump() for bm in data.bind_mounts]),
            run_user=data.run_user,
            user_ns=data.user_ns,
            uid_map=json.dumps(data.uid_map),
            gid_map=json.dumps(data.gid_map),
            health_cmd=data.health_cmd,
            health_interval=data.health_interval,
            health_timeout=data.health_timeout,
            health_retries=data.health_retries,
            health_start_period=data.health_start_period,
            health_on_failure=data.health_on_failure,
            notify_healthy=int(data.notify_healthy),
            auto_update=data.auto_update,
            environment_file=data.environment_file,
            exec_cmd=data.exec_cmd,
            entrypoint=data.entrypoint,
            no_new_privileges=int(data.no_new_privileges),
            read_only=int(data.read_only),
            working_dir=data.working_dir,
            drop_caps=json.dumps(data.drop_caps),
            add_caps=json.dumps(data.add_caps),
            sysctl=json.dumps(data.sysctl),
            seccomp_profile=data.seccomp_profile,
            mask_paths=json.dumps(data.mask_paths),
            unmask_paths=json.dumps(data.unmask_paths),
            privileged=int(data.privileged),
            hostname=data.hostname,
            dns=json.dumps(data.dns),
            dns_search=json.dumps(data.dns_search),
            dns_option=json.dumps(data.dns_option),
            pod_name=data.pod_name,
            log_driver=data.log_driver,
            log_opt=json.dumps(data.log_opt),
            exec_start_post=data.exec_start_post,
            exec_stop=data.exec_stop,
            secrets=json.dumps(data.secrets),
            devices=json.dumps(data.devices),
            runtime=data.runtime,
            service_extra=data.service_extra,
            init=int(data.init),
            memory_reservation=data.memory_reservation,
            cpu_weight=data.cpu_weight,
            io_weight=data.io_weight,
            network_aliases=json.dumps(data.network_aliases),
        )
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
        None,
        _write_and_reload,
        compartment_id,
        container,
        comp_volumes,
        all_containers,
        comp,
    )
    return container


@sanitized.enforce
async def delete_container(
    db: AsyncSession, compartment_id: SafeSlug, container_id: SafeUUID
) -> None:
    result = await db.execute(
        select(ContainerRow.__table__).where(
            ContainerRow.id == container_id, ContainerRow.compartment_id == compartment_id
        )
    )
    row = result.mappings().first()
    if row is None:
        return
    name = SafeResourceName.of(row["name"], "db:containers.name")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _stop_and_remove_container,
        compartment_id,
        name,
    )

    await db.execute(delete(ContainerRow).where(ContainerRow.id == container_id))
    await db.commit()


@sanitized.enforce
def _stop_and_remove_container(service_id: SafeSlug, container_name: SafeResourceName) -> None:
    try:
        systemd_manager.stop_unit(
            service_id, SafeUnitName.of(f"{container_name}.service", "_stop_and_remove_container")
        )
    except Exception as e:
        logger.warning("Could not stop container %s: %s", container_name, e)
    quadlet_writer.remove_container_unit(service_id, container_name)
    try:
        systemd_manager.daemon_reload(service_id)
    except Exception as e:
        logger.warning("daemon-reload after container remove failed: %s", e)


@sanitized.enforce
async def enable_compartment(db: AsyncSession, compartment_id: SafeSlug) -> None:
    containers = await list_containers(db, compartment_id)
    loop = asyncio.get_event_loop()
    for container in containers:
        await loop.run_in_executor(
            None,
            systemd_manager.enable_unit,
            compartment_id,
            SafeUnitName.of(container.name, "enable_compartment"),
        )
    await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)


@sanitized.enforce
async def disable_compartment(db: AsyncSession, compartment_id: SafeSlug) -> None:
    containers = await list_containers(db, compartment_id)
    loop = asyncio.get_event_loop()
    for container in containers:
        await loop.run_in_executor(
            None,
            systemd_manager.disable_unit,
            compartment_id,
            SafeUnitName.of(container.name, "disable_compartment"),
        )
    await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)


@sanitized.enforce
async def start_compartment(db: AsyncSession, compartment_id: SafeSlug) -> list[dict]:
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
                None,
                quadlet_writer.write_container_unit,
                compartment_id,
                container,
                comp.volumes,
            )
        # Always reload so Quadlet generates .service files from the unit files written above.
        await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)
        errors = []
        for container in sorted(containers, key=lambda c: c.sort_order):
            unit = SafeUnitName.of(f"{container.name}.service", "start_compartment")
            try:
                await loop.run_in_executor(None, systemd_manager.start_unit, compartment_id, unit)
            except Exception as e:
                logger.error("Failed to start %s: %s", unit, e)
                errors.append({"unit": unit, "error": str(e)})
        await _log_event(db, "start", f"Compartment {compartment_id} started", compartment_id)
        await db.commit()
        return errors


@sanitized.enforce
async def stop_compartment(db: AsyncSession, compartment_id: SafeSlug) -> list[dict]:
    async with _get_lock(compartment_id):
        containers = await list_containers(db, compartment_id)
        loop = asyncio.get_event_loop()
        errors = []
        for container in sorted(containers, key=lambda c: c.sort_order, reverse=True):
            unit = SafeUnitName.of(f"{container.name}.service", "stop_compartment")
            try:
                await loop.run_in_executor(None, systemd_manager.stop_unit, compartment_id, unit)
            except Exception as e:
                logger.warning("Failed to stop %s: %s", unit, e)
                errors.append({"unit": unit, "error": str(e)})
        await _log_event(db, "stop", f"Compartment {compartment_id} stopped", compartment_id)
        await db.commit()
        return errors


@sanitized.enforce
async def restart_compartment(db: AsyncSession, compartment_id: SafeSlug) -> list[dict]:
    await stop_compartment(db, compartment_id)
    return await start_compartment(db, compartment_id)


@sanitized.enforce
async def check_sync(db: AsyncSession, compartment_id: SafeSlug) -> list[dict]:
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


@sanitized.enforce
async def resync_compartment(db: AsyncSession, compartment_id: SafeSlug) -> None:
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
            unit = SafeUnitName.of(f"{container.name}.service", "resync_compartment")
            props = systemd_manager.get_unit_status(compartment_id, unit)
            if props.get("ActiveState") == "active":
                systemd_manager.restart_unit(compartment_id, unit)

    await loop.run_in_executor(None, _do_resync)


@sanitized.enforce
async def export_compartment_bundle(db: AsyncSession, compartment_id: SafeSlug) -> str | None:
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


@sanitized.enforce
async def get_quadlet_files(db: AsyncSession, compartment_id: SafeSlug) -> list[dict]:
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


@sanitized.enforce
async def get_status(
    db: AsyncSession,
    compartment_id: SafeSlug,
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
        [SafeStr.of(c.name, "container_name") for c in containers],
    )


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


@sanitized.enforce
async def add_secret(db: AsyncSession, compartment_id: SafeSlug, data: SecretCreate) -> Secret:
    """Register a secret in the DB and create it in the compartment's podman store."""
    sid = SafeUUID.trusted(str(uuid.uuid4()), "add_secret")
    await db.execute(
        insert(SecretRow).values(id=sid, compartment_id=compartment_id, name=data.name)
    )
    await db.commit()
    return Secret(
        id=sid,
        compartment_id=compartment_id,
        name=data.name,
        created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_secret"),
    )


@sanitized.enforce
async def list_secrets(db: AsyncSession, compartment_id: SafeSlug) -> list[Secret]:
    result = await db.execute(
        select(SecretRow.__table__)
        .where(SecretRow.compartment_id == compartment_id)
        .order_by(SecretRow.name)
    )
    return await _validate_rows(db, Secret, SecretRow.__table__, result.mappings().all())


@sanitized.enforce
async def delete_secret(db: AsyncSession, compartment_id: SafeSlug, secret_id: SafeUUID) -> None:
    result = await db.execute(
        select(SecretRow.__table__).where(
            SecretRow.id == secret_id, SecretRow.compartment_id == compartment_id
        )
    )
    row = result.mappings().first()
    if row is None:
        return
    name = SafeSecretName.of(row["name"], "name")
    loop = asyncio.get_event_loop()
    with contextlib.suppress(Exception):
        await loop.run_in_executor(None, secrets_manager.delete_podman_secret, compartment_id, name)
    await db.execute(delete(SecretRow).where(SecretRow.id == secret_id))
    await db.commit()


# ---------------------------------------------------------------------------
# Timers
# ---------------------------------------------------------------------------


@sanitized.enforce
async def create_timer(db: AsyncSession, compartment_id: SafeSlug, data: TimerCreate) -> Timer:
    """Persist a timer and write the .timer unit file."""
    # Resolve container name
    result = await db.execute(
        select(ContainerRow.__table__).where(
            ContainerRow.id == data.container_id, ContainerRow.compartment_id == compartment_id
        )
    )
    row = result.mappings().first()
    if row is None:
        raise ValueError("Container not found")
    container_name = SafeResourceName.of(row["name"], "db:containers.name")

    tid = SafeUUID.trusted(str(uuid.uuid4()), "create_timer")
    await db.execute(
        insert(TimerRow).values(
            id=tid,
            compartment_id=compartment_id,
            container_id=data.container_id,
            name=data.name,
            on_calendar=data.on_calendar,
            on_boot_sec=data.on_boot_sec,
            random_delay_sec=data.random_delay_sec,
            persistent=int(data.persistent),
            enabled=int(data.enabled),
        )
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
        created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "create_timer"),
    )
    loop = asyncio.get_event_loop()
    if user_manager.user_exists(compartment_id):
        await loop.run_in_executor(
            None, quadlet_writer.write_timer_unit, compartment_id, timer, container_name
        )
        await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)
    return timer


@sanitized.enforce
async def list_timers(db: AsyncSession, compartment_id: SafeSlug) -> list[Timer]:
    result = await db.execute(
        select(TimerRow.__table__, ContainerRow.name.label("container_name"))
        .outerjoin(ContainerRow, TimerRow.container_id == ContainerRow.id)
        .where(TimerRow.compartment_id == compartment_id)
        .order_by(TimerRow.created_at)
    )
    return await _validate_rows(db, Timer, TimerRow.__table__, result.mappings().all())


@sanitized.enforce
async def delete_timer(db: AsyncSession, compartment_id: SafeSlug, timer_id: SafeUUID) -> None:
    result = await db.execute(
        select(TimerRow.__table__).where(
            TimerRow.id == timer_id, TimerRow.compartment_id == compartment_id
        )
    )
    row = result.mappings().first()
    if row is None:
        return
    timer_name = SafeResourceName.of(row["name"], "db:timers.name")
    loop = asyncio.get_event_loop()
    if user_manager.user_exists(compartment_id):
        with contextlib.suppress(Exception):
            await loop.run_in_executor(
                None, quadlet_writer.remove_timer_unit, compartment_id, timer_name
            )
        with contextlib.suppress(Exception):
            await loop.run_in_executor(None, systemd_manager.daemon_reload, compartment_id)
    await db.execute(delete(TimerRow).where(TimerRow.id == timer_id))
    await db.commit()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@sanitized.enforce
async def save_template(db: AsyncSession, data: TemplateCreate) -> Template:
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
    tid = SafeUUID.trusted(str(uuid.uuid4()), "save_template")
    await db.execute(
        insert(TemplateRow).values(
            id=tid,
            name=data.name,
            description=data.description,
            config_json=json.dumps(config),
        )
    )
    await db.commit()
    return Template(
        id=tid,
        name=data.name,
        description=data.description,
        config_json=json.dumps(config),
        created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "save_template"),
    )


@sanitized.enforce
async def list_templates(db: AsyncSession) -> list[Template]:
    result = await db.execute(select(TemplateRow.__table__).order_by(TemplateRow.created_at))
    return await _validate_rows(db, Template, TemplateRow.__table__, result.mappings().all())


@sanitized.enforce
async def delete_template(db: AsyncSession, template_id: SafeUUID) -> None:
    await db.execute(delete(TemplateRow).where(TemplateRow.id == template_id))
    await db.commit()


@sanitized.enforce
async def create_compartment_from_template(
    db: AsyncSession,
    template_id: SafeUUID,
    compartment_id: SafeSlug,
    description: SafeStr,
) -> Compartment:
    """Create a new compartment by instantiating a saved template."""
    from ..models import CompartmentCreate

    result = await db.execute(select(TemplateRow.__table__).where(TemplateRow.id == template_id))
    row = result.mappings().first()
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
            build_unit_name=cd.get("build_unit_name", ""),
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


@sanitized.enforce
async def add_notification_hook(
    db: AsyncSession, compartment_id: SafeSlug, data: NotificationHookCreate
) -> NotificationHook:
    hid = SafeUUID.trusted(str(uuid.uuid4()), "add_notification_hook")
    await db.execute(
        insert(NotificationHookRow).values(
            id=hid,
            compartment_id=compartment_id,
            container_name=data.container_name,
            event_type=data.event_type,
            webhook_url=data.webhook_url,
            webhook_secret=data.webhook_secret,
            enabled=int(data.enabled),
        )
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
        created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_notification_hook"),
    )


@sanitized.enforce
async def list_notification_hooks(
    db: AsyncSession, compartment_id: SafeSlug
) -> list[NotificationHook]:
    result = await db.execute(
        select(NotificationHookRow.__table__)
        .where(NotificationHookRow.compartment_id == compartment_id)
        .order_by(NotificationHookRow.created_at)
    )
    return await _validate_rows(
        db, NotificationHook, NotificationHookRow.__table__, result.mappings().all()
    )


@sanitized.enforce
async def delete_notification_hook(
    db: AsyncSession, compartment_id: SafeSlug, hook_id: SafeUUID
) -> None:
    await db.execute(
        delete(NotificationHookRow).where(
            NotificationHookRow.id == hook_id,
            NotificationHookRow.compartment_id == compartment_id,
        )
    )
    await db.commit()


@sanitized.enforce
async def list_all_notification_hooks(db: AsyncSession) -> list[NotificationHook]:
    """Return all enabled hooks across all compartments (used by the notification monitor)."""
    result = await db.execute(
        select(NotificationHookRow.__table__).where(NotificationHookRow.enabled == 1)
    )
    return await _validate_rows(
        db, NotificationHook, NotificationHookRow.__table__, result.mappings().all()
    )


# ---------------------------------------------------------------------------
# Process monitor
# ---------------------------------------------------------------------------


@sanitized.enforce
async def upsert_process(
    db: AsyncSession,
    compartment_id: SafeSlug,
    process_name: SafeStr,
    cmdline: SafeMultilineStr,
) -> tuple[Process, bool]:
    """Insert or increment a process record. Returns (process, is_new).

    On first sight a new record is created with known=False. On subsequent polls
    times_seen and last_seen_at are updated; known is never reset by the monitor.
    """
    result = await db.execute(
        select(ProcessRow.__table__).where(
            ProcessRow.compartment_id == compartment_id,
            ProcessRow.process_name == process_name,
            ProcessRow.cmdline == cmdline,
        )
    )
    row = result.mappings().first()

    if row is None:
        pid = SafeUUID.trusted(str(uuid.uuid4()), "upsert_process")
        await db.execute(
            insert(ProcessRow).values(
                id=pid,
                compartment_id=compartment_id,
                process_name=process_name,
                cmdline=cmdline,
            )
        )
        await db.commit()
        result2 = await db.execute(select(ProcessRow.__table__).where(ProcessRow.id == pid))
        return await _validate_row(
            db, Process, ProcessRow.__table__, result2.mappings().first()
        ), True
    else:
        await db.execute(
            update(ProcessRow)
            .where(
                ProcessRow.compartment_id == compartment_id,
                ProcessRow.process_name == process_name,
                ProcessRow.cmdline == cmdline,
            )
            .values(
                times_seen=ProcessRow.times_seen + 1,
                last_seen_at=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
            )
        )
        await db.commit()
        result2 = await db.execute(
            select(ProcessRow.__table__).where(
                ProcessRow.compartment_id == compartment_id,
                ProcessRow.process_name == process_name,
                ProcessRow.cmdline == cmdline,
            )
        )
        return await _validate_row(
            db, Process, ProcessRow.__table__, result2.mappings().first()
        ), False


@sanitized.enforce
async def list_processes(db: AsyncSession, compartment_id: SafeSlug) -> list[Process]:
    result = await db.execute(
        select(ProcessRow.__table__)
        .where(ProcessRow.compartment_id == compartment_id)
        .order_by(ProcessRow.known.asc(), ProcessRow.first_seen_at.asc())
    )
    return await _validate_rows(db, Process, ProcessRow.__table__, result.mappings().all())


@sanitized.enforce
async def list_all_processes(db: AsyncSession) -> list[Process]:
    """Return all process records across all compartments (used by the monitor loop)."""
    result = await db.execute(select(ProcessRow.__table__))
    return await _validate_rows(db, Process, ProcessRow.__table__, result.mappings().all())


@sanitized.enforce
async def set_process_known(
    db: AsyncSession, compartment_id: SafeSlug, process_id: SafeUUID, known: bool
) -> None:
    await db.execute(
        update(ProcessRow)
        .where(ProcessRow.id == process_id, ProcessRow.compartment_id == compartment_id)
        .values(known=int(known))
    )
    await db.commit()


@sanitized.enforce
async def delete_process(db: AsyncSession, compartment_id: SafeSlug, process_id: SafeUUID) -> None:
    """Remove a process record entirely so it can be re-evaluated if seen again."""
    await db.execute(
        delete(ProcessRow).where(
            ProcessRow.id == process_id, ProcessRow.compartment_id == compartment_id
        )
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Connection monitor — allowlist rule helpers
# ---------------------------------------------------------------------------


@sanitized.enforce
def _rule_matches(
    rule: AllowlistRule,
    proto,
    dst_ip: SafeIpAddress,
    dst_port: int,
    container_name: SafeResourceName,
    direction,
) -> bool:
    """Return True if *rule* matches the given connection fields.

    NULL fields on the rule are wildcards.  dst_ip may be an exact address or
    a CIDR prefix (e.g. ``10.0.0.0/8``).  direction must match when the rule
    specifies one; NULL on the rule matches both directions.
    """
    if rule.direction and rule.direction != direction:
        return False
    if rule.container_name and rule.container_name != container_name:
        return False
    if rule.proto and rule.proto.lower() != proto.lower():
        return False
    if rule.dst_ip:
        try:
            if "/" in rule.dst_ip:
                if ipaddress.ip_address(dst_ip) not in ipaddress.ip_network(
                    rule.dst_ip, strict=False
                ):
                    return False
            elif rule.dst_ip != dst_ip:
                return False
        except ValueError:
            return False
    return rule.dst_port is None or rule.dst_port == dst_port


@sanitized.enforce
def connection_is_allowlisted(
    rules: list[AllowlistRule],
    proto,
    dst_ip: SafeIpAddress,
    dst_port: int,
    container_name: SafeResourceName,
    direction,
) -> bool:
    """Return True if any rule in *rules* matches the connection."""
    return any(_rule_matches(r, proto, dst_ip, dst_port, container_name, direction) for r in rules)


# ---------------------------------------------------------------------------
# Connection monitor — allowlist rule CRUD
# ---------------------------------------------------------------------------


@sanitized.enforce
async def list_allowlist_rules(db: AsyncSession, compartment_id: SafeSlug) -> list[AllowlistRule]:
    result = await db.execute(
        select(AllowlistRuleRow.__table__)
        .where(AllowlistRuleRow.compartment_id == compartment_id)
        .order_by(AllowlistRuleRow.sort_order.asc(), AllowlistRuleRow.created_at.asc())
    )
    return await _validate_rows(
        db, AllowlistRule, AllowlistRuleRow.__table__, result.mappings().all()
    )


@sanitized.enforce
async def add_allowlist_rule(
    db: AsyncSession,
    compartment_id: SafeSlug,
    description: SafeStr,
    container_name: SafeStr | None,
    proto: SafeStr | None,
    dst_ip: SafeIpAddress | None,
    dst_port: int | None,
    direction: SafeStr | None,
) -> AllowlistRule:
    result = await db.execute(
        select(func.coalesce(func.max(AllowlistRuleRow.sort_order), 0)).where(
            AllowlistRuleRow.compartment_id == compartment_id
        )
    )
    sort_order = result.scalar() + 1
    rule_id = SafeUUID.trusted(str(uuid.uuid4()), "add_allowlist_rule")
    await db.execute(
        insert(AllowlistRuleRow).values(
            id=rule_id,
            compartment_id=compartment_id,
            description=description or "",
            container_name=container_name or None,
            proto=proto or None,
            dst_ip=dst_ip or None,
            dst_port=dst_port,
            direction=direction or None,
            sort_order=sort_order,
        )
    )
    await db.commit()
    result2 = await db.execute(
        select(AllowlistRuleRow.__table__).where(AllowlistRuleRow.id == rule_id)
    )
    return await _validate_row(
        db, AllowlistRule, AllowlistRuleRow.__table__, result2.mappings().first()
    )


@sanitized.enforce
async def delete_allowlist_rule(
    db: AsyncSession, compartment_id: SafeSlug, rule_id: SafeUUID
) -> None:
    await db.execute(
        delete(AllowlistRuleRow).where(
            AllowlistRuleRow.id == rule_id,
            AllowlistRuleRow.compartment_id == compartment_id,
        )
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Connection monitor — connection CRUD
# ---------------------------------------------------------------------------


@sanitized.enforce
async def upsert_connection(
    db: AsyncSession,
    compartment_id: SafeSlug,
    container_name: SafeResourceName,
    proto: SafeStr,
    dst_ip: SafeIpAddress,
    dst_port: int,
    direction: SafeStr,
) -> tuple[Connection, bool]:
    """Insert or increment a connection record. Returns (connection, is_new)."""
    _conn_where = [
        ConnectionRow.compartment_id == compartment_id,
        ConnectionRow.container_name == container_name,
        ConnectionRow.proto == proto,
        ConnectionRow.dst_ip == dst_ip,
        ConnectionRow.dst_port == dst_port,
        ConnectionRow.direction == direction,
    ]
    result = await db.execute(select(ConnectionRow.id).where(*_conn_where))
    existing = result.mappings().first()

    if existing is None:
        new_conn_id = SafeUUID.trusted(str(uuid.uuid4()), "upsert_connection")
        await db.execute(
            insert(ConnectionRow).values(
                id=new_conn_id,
                compartment_id=compartment_id,
                container_name=container_name,
                proto=proto,
                dst_ip=dst_ip,
                dst_port=dst_port,
                direction=direction,
            )
        )
        await db.commit()
        result2 = await db.execute(
            select(ConnectionRow.__table__).where(ConnectionRow.id == new_conn_id)
        )
        return await _validate_row(
            db, Connection, ConnectionRow.__table__, result2.mappings().first()
        ), True

    await db.execute(
        update(ConnectionRow)
        .where(*_conn_where)
        .values(
            times_seen=ConnectionRow.times_seen + 1,
            last_seen_at=func.strftime("%Y-%m-%dT%H:%M:%SZ", "now"),
        )
    )
    await db.commit()
    result2 = await db.execute(select(ConnectionRow.__table__).where(*_conn_where))
    return await _validate_row(
        db, Connection, ConnectionRow.__table__, result2.mappings().first()
    ), False


@sanitized.enforce
async def list_connections(db: AsyncSession, compartment_id: SafeSlug) -> list[Connection]:
    """Return all connection history rows, each annotated with allowlisted=True/False."""
    rules = await list_allowlist_rules(db, compartment_id)
    result = await db.execute(
        select(ConnectionRow.__table__)
        .where(ConnectionRow.compartment_id == compartment_id)
        .order_by(
            ConnectionRow.container_name.asc(),
            ConnectionRow.proto.asc(),
            ConnectionRow.dst_ip.asc(),
            ConnectionRow.dst_port.asc(),
        )
    )
    conns = await _validate_rows(db, Connection, ConnectionRow.__table__, result.mappings().all())
    for c in conns:
        c.allowlisted = connection_is_allowlisted(
            rules, c.proto, c.dst_ip, c.dst_port, c.container_name, c.direction
        )
    return conns


@sanitized.enforce
async def delete_connection(
    db: AsyncSession, compartment_id: SafeSlug, connection_id: SafeUUID
) -> None:
    """Remove a single connection record from history."""
    await db.execute(
        delete(ConnectionRow).where(
            ConnectionRow.id == connection_id,
            ConnectionRow.compartment_id == compartment_id,
        )
    )
    await db.commit()


@sanitized.enforce
async def clear_connections_history(db: AsyncSession, compartment_id: SafeSlug) -> None:
    """Delete all connection history for a compartment."""
    await db.execute(delete(ConnectionRow).where(ConnectionRow.compartment_id == compartment_id))
    await db.commit()


@sanitized.enforce
async def cleanup_stale_connections(db: AsyncSession) -> None:
    """Delete connection records older than each compartment's retention setting."""
    result = await db.execute(
        select(CompartmentRow.id, CompartmentRow.connection_history_retention_days).where(
            CompartmentRow.connection_history_retention_days.is_not(None)
        )
    )
    rows = result.mappings().all()
    for row in rows:
        cid, days = row["id"], row["connection_history_retention_days"]
        await db.execute(
            delete(ConnectionRow).where(
                ConnectionRow.compartment_id == cid,
                ConnectionRow.last_seen_at
                < func.strftime("%Y-%m-%dT%H:%M:%SZ", "now", f"-{days} days"),
            )
        )
    await db.commit()


@sanitized.enforce
async def set_connection_monitor_enabled(
    db: AsyncSession, compartment_id: SafeSlug, enabled: bool
) -> None:
    await db.execute(
        update(CompartmentRow)
        .where(CompartmentRow.id == compartment_id)
        .values(connection_monitor_enabled=int(enabled))
    )
    await db.commit()


@sanitized.enforce
async def set_connection_history_retention(
    db: AsyncSession, compartment_id: SafeSlug, days: int | None
) -> None:
    await db.execute(
        update(CompartmentRow)
        .where(CompartmentRow.id == compartment_id)
        .values(connection_history_retention_days=days)
    )
    await db.commit()


@sanitized.enforce
async def set_process_monitor_enabled(
    db: AsyncSession, compartment_id: SafeSlug, enabled: bool
) -> None:
    await db.execute(
        update(CompartmentRow)
        .where(CompartmentRow.id == compartment_id)
        .values(process_monitor_enabled=int(enabled))
    )
    await db.commit()

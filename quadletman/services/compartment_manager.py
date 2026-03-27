"""High-level compartment lifecycle orchestration."""
# ruff: noqa: E402  — AsyncSession._sanitized_enforce_model_safety must be set before project imports

import asyncio
import contextlib
import contextvars
import ipaddress
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.orm import (
    AllowlistRuleRow,
    ArtifactRow,
    BuildRow,
    CompartmentRow,
    ConnectionRow,
    ContainerRow,
    ImageRow,
    NetworkRow,
    NotificationHookRow,
    PodRow,
    ProcessPatternRow,
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
    Artifact,
    ArtifactCreate,
    Build,
    BuildCreate,
    Compartment,
    CompartmentCreate,
    Connection,
    Container,
    ContainerCreate,
    Image,
    ImageCreate,
    Network,
    NetworkCreate,
    NotificationHook,
    NotificationHookCreate,
    Pod,
    PodCreate,
    Process,
    ProcessPattern,
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
    SafeRegex,
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


async def _run_in_ctx(fn, *args):
    """Run *fn* in the default executor, preserving the current ContextVars.

    ``loop.run_in_executor`` copies the event-loop context, not the task
    context set by Starlette middleware.  Wrapping with
    ``copy_context().run()`` ensures ContextVars (e.g. admin credentials)
    are visible inside the executor thread.
    """
    ctx = contextvars.copy_context()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, ctx.run, fn, *args)


# Per-compartment lock to prevent concurrent modifications
_compartment_locks: dict[str, asyncio.Lock] = {}
_LOCK_TIMEOUT = settings.lock_timeout


class ServiceCondition(Exception):
    """Base for service-layer conditions that must propagate to app-level exception handlers.

    Router ``except Exception`` catch-alls re-raise any ``ServiceCondition`` subclass so that
    the app-level handlers in ``main.py`` can convert them to proper HTTP responses.
    """


class CompartmentBusy(ServiceCondition):
    """Raised when a compartment lock cannot be acquired within the timeout."""


class FileWriteFailed(ServiceCondition):
    """Raised when a filesystem/systemd write fails after a DB commit.

    The DB insert has been rolled back (for add operations) or the DB is ahead
    of the filesystem state (for update operations).  The user should be told
    the operation failed and, for updates, that a resync may be needed.
    """

    def __init__(self, resource_type: str, resource_id: str, *, rolled_back: bool) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.rolled_back = rolled_back
        verb = (
            "rolled back"
            if rolled_back
            else "saved but unit files are out of sync — resync recommended"
        )
        super().__init__(f"{resource_type} {resource_id}: filesystem write failed, {verb}")


@sanitized.enforce
def _get_lock(compartment_id: SafeSlug) -> asyncio.Lock:
    if compartment_id not in _compartment_locks:
        _compartment_locks[compartment_id] = asyncio.Lock()
    return _compartment_locks[compartment_id]


@contextlib.asynccontextmanager
async def _compartment_lock(compartment_id: SafeSlug):
    """Acquire the per-compartment lock with a timeout."""
    lock = _get_lock(compartment_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=_LOCK_TIMEOUT)
    except TimeoutError:
        raise CompartmentBusy(compartment_id) from None
    try:
        yield
    finally:
        lock.release()


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


# Default process patterns seeded for every new compartment so that
# agent and systemd infrastructure processes are automatically recognised
# as known by the process monitor.
# Process names come from /proc/pid/comm (max 15 chars, hence truncation).
_DEFAULT_PROCESS_PATTERNS: list[tuple[str, str]] = [
    # quadletman monitoring agent (comm truncated from "quadletman-agent")
    ("quadletman-agen", r".*quadletman-agent\b.*"),
    # Agent subprocess calls
    ("systemctl", r"systemctl --user .*"),
    ("podman", r"podman .*"),
    # systemd --user infrastructure (always present for linger-enabled users)
    ("systemd", r".*/systemd --user"),
    ("(sd-pam)", r"\(sd-pam\)"),
    ("dbus-daemon", r".*/dbus-daemon --session .*"),
    # Podman container init process
    ("catatonit", r"catatonit .*"),
]


@sanitized.enforce
async def _seed_default_patterns(db: AsyncSession, compartment_id: SafeSlug) -> None:
    """Insert default process patterns for agent command lines."""
    for process_name, cmdline_pattern in _DEFAULT_PROCESS_PATTERNS:
        await db.execute(
            insert(ProcessPatternRow)
            .values(
                id=str(uuid.uuid4()),
                compartment_id=compartment_id,
                process_name=SafeStr.of(process_name, "default_pattern"),
                cmdline_pattern=SafeRegex.of(cmdline_pattern, "default_pattern"),
                segments_json="[]",
            )
            .prefix_with("OR IGNORE")
        )


@sanitized.enforce
async def create_compartment(db: AsyncSession, data: CompartmentCreate) -> Compartment:
    linux_user = f"{settings.service_user_prefix}{data.id}"

    async with _compartment_lock(data.id):
        # Insert DB record first (fast fail before system ops)
        await db.execute(
            insert(CompartmentRow).values(
                id=data.id,
                description=data.description,
                linux_user=linux_user,
            )
        )
        await _seed_default_patterns(db, data.id)
        await db.commit()

        try:
            await _run_in_ctx(_setup_service_user, data.id)
        except Exception as exc:
            logger.error("Failed to set up compartment user for %s: %s", log_safe(data.id), exc)
            # Best-effort OS cleanup — remove any partially-created Linux user so retries
            # get a clean slate and orphaned users don't accumulate.
            with contextlib.suppress(Exception):
                await _run_in_ctx(user_manager.delete_service_user, data.id)
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
    # Deploy per-user monitoring agent (no-op when running as root)
    quadlet_writer.deploy_agent_service(service_id)
    if os.getuid() != 0:
        systemd_manager.daemon_reload(service_id)
        agent_unit = SafeUnitName.of("quadletman-agent.service", "agent_unit")
        systemd_manager.start_unit(service_id, agent_unit)
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
    comp.images = await list_images(db, compartment_id)
    comp.builds = await list_builds(db, compartment_id)
    comp.networks = await list_networks(db, compartment_id)
    comp.artifacts = await list_artifacts(db, compartment_id)
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
        comp.images = await list_images(db, comp.id)
        comp.builds = await list_builds(db, comp.id)
        comp.networks = await list_networks(db, comp.id)
        comp.artifacts = await list_artifacts(db, comp.id)
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
async def delete_compartment(db: AsyncSession, compartment_id: SafeSlug) -> None:
    async with _compartment_lock(compartment_id):
        comp = await get_compartment(db, compartment_id)
        if comp is None:
            return

        await _run_in_ctx(_teardown_service, comp)

        await db.execute(delete(CompartmentRow).where(CompartmentRow.id == compartment_id))
        await db.commit()
        _compartment_locks.pop(compartment_id, None)


@sanitized.enforce
def _teardown_service(comp: Compartment) -> None:
    service_id = comp.id
    # Stop all containers
    for container in comp.containers:
        try:
            systemd_manager.stop_unit(
                service_id, SafeUnitName.of(f"{container.qm_name}.service", "_teardown_service")
            )
        except Exception as e:
            logger.warning("Could not stop %s: %s", container.qm_name, e)
        quadlet_writer.remove_container_unit(
            service_id, SafeResourceName.of(container.qm_name, "container.qm_name")
        )

    # Remove pod units
    for pod in comp.pods:
        with contextlib.suppress(Exception):  # Best-effort: pod unit may already be removed
            quadlet_writer.remove_pod_unit(
                service_id, SafeResourceName.of(pod.qm_name, "pod.qm_name")
            )

    # Remove quadlet-managed volume units
    for vol in comp.volumes:
        if vol.qm_use_quadlet:
            with contextlib.suppress(Exception):  # Best-effort: volume unit may already be removed
                quadlet_writer.remove_volume_unit(
                    service_id, SafeResourceName.of(vol.qm_name, "vol.qm_name")
                )

    # Remove image units
    for iu in comp.images:
        with contextlib.suppress(Exception):  # Best-effort: image unit may already be removed
            quadlet_writer.remove_image_unit(
                service_id, SafeResourceName.of(iu.qm_name, "iu.qm_name")
            )

    # Remove network units
    for net in comp.networks:
        with contextlib.suppress(Exception):  # Best-effort: network unit may already be removed
            quadlet_writer.remove_network_unit(
                service_id, SafeResourceName.of(net.qm_name, "net.qm_name")
            )

    # Remove per-user monitoring agent (no-op when running as root)
    with contextlib.suppress(Exception):  # Best-effort: agent unit may not exist
        quadlet_writer.remove_agent_service(service_id)

    if user_manager.user_exists(service_id):
        with contextlib.suppress(
            Exception
        ):  # Best-effort: systemd reload may fail if user session is torn down
            systemd_manager.daemon_reload(service_id)
        user_manager.disable_linger(service_id)
        user_manager.delete_service_user(service_id)

    volume_manager.delete_all_service_volumes(service_id)


@sanitized.enforce
async def add_volume(db: AsyncSession, compartment_id: SafeSlug, data: VolumeCreate) -> Volume:
    async with _compartment_lock(compartment_id):
        vid = SafeUUID.trusted(str(uuid.uuid4()), "add_volume")
        await db.execute(
            insert(VolumeRow).values(
                id=vid,
                compartment_id=compartment_id,
                qm_name=data.qm_name,
                qm_selinux_context=data.qm_selinux_context,
                qm_owner_uid=data.qm_owner_uid,
                qm_use_quadlet=int(data.qm_use_quadlet),
                driver=data.driver,
                device=data.device,
                options=data.options,
                copy=int(data.copy),
                group=data.group,
            )
        )
        await db.commit()

        host_path = ""
        try:
            if not data.qm_use_quadlet:
                host_path = await _run_in_ctx(
                    volume_manager.create_volume_dir,
                    compartment_id,
                    data.qm_name,
                    data.qm_selinux_context,
                    data.qm_owner_uid,
                )
            else:
                # Write the .volume quadlet file so systemd can create the Podman volume
                vol = Volume(
                    id=vid,
                    compartment_id=compartment_id,
                    qm_name=data.qm_name,
                    qm_selinux_context=data.qm_selinux_context,
                    qm_owner_uid=data.qm_owner_uid,
                    qm_host_path="",
                    created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_volume"),
                    qm_use_quadlet=data.qm_use_quadlet,
                    driver=data.driver,
                    device=data.device,
                    options=data.options,
                    copy=data.copy,
                    group=data.group,
                )
                if user_manager.user_exists(compartment_id):
                    await _run_in_ctx(quadlet_writer.write_volume_unit, compartment_id, vol)
                    await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        except Exception as exc:
            logger.warning("Filesystem write failed for volume %s, rolling back DB insert", vid)
            if not data.qm_use_quadlet:
                with contextlib.suppress(
                    Exception
                ):  # Best-effort: directory may not have been created
                    volume_manager.delete_volume_dir(compartment_id, data.qm_name)
            await db.execute(delete(VolumeRow).where(VolumeRow.id == vid))
            await db.commit()
            raise FileWriteFailed("volume", str(vid), rolled_back=True) from exc

        await _log_event(db, "volume_create", f"Volume {data.qm_name} created", compartment_id)
        await db.commit()

        return Volume(
            id=vid,
            compartment_id=compartment_id,
            qm_name=data.qm_name,
            qm_selinux_context=data.qm_selinux_context,
            qm_owner_uid=data.qm_owner_uid,
            qm_host_path=host_path,
            created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_volume"),
            qm_use_quadlet=data.qm_use_quadlet,
            driver=data.driver,
            device=data.device,
            options=data.options,
            copy=data.copy,
            group=data.group,
        )


@sanitized.enforce
async def update_volume_owner(
    db: AsyncSession, compartment_id: SafeSlug, volume_id: SafeUUID, owner_uid: int
) -> None:
    """Change the qm_owner_uid of a managed volume and re-chown the directory."""
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(VolumeRow.__table__).where(
                VolumeRow.id == volume_id, VolumeRow.compartment_id == compartment_id
            )
        )
        row = result.mappings().first()
        if row is None:
            raise ValueError("Volume not found")

        await _run_in_ctx(
            volume_manager.chown_volume_dir,
            compartment_id,
            SafeResourceName.of(row["qm_name"], "db:volumes.qm_name"),
            owner_uid,
        )
        await db.execute(
            update(VolumeRow).where(VolumeRow.id == volume_id).values(qm_owner_uid=owner_uid)
        )
        await db.commit()
        await _log_event(
            db, "volume_update", f"Volume {row['qm_name']} owner_uid → {owner_uid}", compartment_id
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
        if not v.qm_use_quadlet:
            v.qm_host_path = SafeStr.trusted(
                resolve_safe_path(
                    settings.volumes_base,
                    f"{compartment_id}/{v.qm_name}",
                ),
                "internally constructed",
            )
    return volumes


@sanitized.enforce
async def delete_volume(db: AsyncSession, compartment_id: SafeSlug, volume_id: SafeUUID) -> None:
    async with _compartment_lock(compartment_id):
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
                props = await _run_in_ctx(
                    systemd_manager.get_unit_status,
                    compartment_id,
                    SafeUnitName.of(f"{c.qm_name}.service", "update_volume_owner"),
                )
                if props.get("ActiveState") == "active":
                    blocking.append(c.qm_name)
        if blocking:
            raise ValueError(
                f"Volume is mounted by running container(s): {', '.join(blocking)}. "
                "Stop the container(s) first."
            )

        volume_name = SafeResourceName.of(row["qm_name"], "db:volumes.qm_name")
        volume_manager.delete_volume_dir(compartment_id, volume_name)
        user_manager.cleanup_resource_config_dir(
            compartment_id, SafeStr.of("volume", "resource_type"), volume_name
        )
        await db.execute(delete(VolumeRow).where(VolumeRow.id == volume_id))
        await db.commit()


# ---------------------------------------------------------------------------
# Networks
# ---------------------------------------------------------------------------


@sanitized.enforce
async def list_networks(db: AsyncSession, compartment_id: SafeSlug) -> list[Network]:
    result = await db.execute(
        select(NetworkRow.__table__)
        .where(NetworkRow.compartment_id == compartment_id)
        .order_by(NetworkRow.created_at)
    )
    return await _validate_rows(db, Network, NetworkRow.__table__, result.mappings().all())


@sanitized.enforce
async def get_network(db: AsyncSession, network_id: SafeUUID) -> Network | None:
    result = await db.execute(select(NetworkRow.__table__).where(NetworkRow.id == network_id))
    return await _validate_row(db, Network, NetworkRow.__table__, result.mappings().first())


@sanitized.enforce
async def add_network(db: AsyncSession, compartment_id: SafeSlug, data: NetworkCreate) -> Network:
    async with _compartment_lock(compartment_id):
        nid = SafeUUID.trusted(str(uuid.uuid4()), "add_network")
        now = SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_network")
        await db.execute(
            insert(NetworkRow).values(
                id=nid,
                compartment_id=compartment_id,
                qm_name=data.qm_name,
                network_name=data.network_name,
                driver=data.driver,
                subnet=data.subnet,
                gateway=data.gateway,
                ipv6=int(data.ipv6),
                internal=int(data.internal),
                dns_enabled=int(data.dns_enabled),
                disable_dns=int(data.disable_dns),
                ip_range=data.ip_range,
                label=json.dumps(data.label),
                options=data.options,
                containers_conf_module=data.containers_conf_module,
                global_args=json.dumps(list(data.global_args)),
                podman_args=json.dumps(list(data.podman_args)),
                ipam_driver=data.ipam_driver,
                dns=data.dns,
                service_name=data.service_name,
                network_delete_on_stop=int(data.network_delete_on_stop),
                interface_name=data.interface_name,
            )
        )
        await db.commit()

        network = Network(
            id=nid,
            compartment_id=compartment_id,
            qm_name=data.qm_name,
            driver=data.driver,
            subnet=data.subnet,
            gateway=data.gateway,
            ipv6=data.ipv6,
            internal=data.internal,
            dns_enabled=data.dns_enabled,
            disable_dns=data.disable_dns,
            ip_range=data.ip_range,
            label=data.label,
            options=data.options,
            containers_conf_module=data.containers_conf_module,
            global_args=data.global_args,
            podman_args=data.podman_args,
            ipam_driver=data.ipam_driver,
            dns=data.dns,
            service_name=data.service_name,
            network_delete_on_stop=data.network_delete_on_stop,
            interface_name=data.interface_name,
            created_at=now,
        )

        # Write the .network unit file if service user exists
        try:
            if user_manager.user_exists(compartment_id):
                await _run_in_ctx(quadlet_writer.write_network_unit, compartment_id, network)
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        except Exception as exc:
            logger.warning("Filesystem write failed for network %s, rolling back DB insert", nid)
            await db.execute(delete(NetworkRow).where(NetworkRow.id == nid))
            await db.commit()
            raise FileWriteFailed("network", str(nid), rolled_back=True) from exc

        await _log_event(db, "network_create", f"Network {data.qm_name} created", compartment_id)
        await db.commit()
        return network


@sanitized.enforce
async def update_network(
    db: AsyncSession,
    compartment_id: SafeSlug,
    network_id: SafeUUID,
    data: NetworkCreate,
) -> Network | None:
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(NetworkRow.__table__).where(
                NetworkRow.id == network_id, NetworkRow.compartment_id == compartment_id
            )
        )
        row = result.mappings().first()
        if row is None:
            return None

        await db.execute(
            update(NetworkRow)
            .where(NetworkRow.id == network_id)
            .values(
                qm_name=data.qm_name,
                network_name=data.network_name,
                driver=data.driver,
                subnet=data.subnet,
                gateway=data.gateway,
                ipv6=int(data.ipv6),
                internal=int(data.internal),
                dns_enabled=int(data.dns_enabled),
                disable_dns=int(data.disable_dns),
                ip_range=data.ip_range,
                label=json.dumps(data.label),
                options=data.options,
                containers_conf_module=data.containers_conf_module,
                global_args=json.dumps(list(data.global_args)),
                podman_args=json.dumps(list(data.podman_args)),
                ipam_driver=data.ipam_driver,
                dns=data.dns,
                service_name=data.service_name,
                network_delete_on_stop=int(data.network_delete_on_stop),
                interface_name=data.interface_name,
            )
        )
        await db.commit()

        # Re-read the updated network
        result = await db.execute(select(NetworkRow.__table__).where(NetworkRow.id == network_id))
        network = await _validate_row(db, Network, NetworkRow.__table__, result.mappings().first())
        if network is None:
            return None

        # Re-write the .network unit file if service user exists
        try:
            if user_manager.user_exists(compartment_id):
                await _run_in_ctx(quadlet_writer.write_network_unit, compartment_id, network)
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        except Exception as exc:
            logger.error(
                "Filesystem write failed after DB update for network %s — resync recommended",
                network_id,
            )
            raise FileWriteFailed("network", str(network_id), rolled_back=False) from exc

        await _log_event(db, "network_update", f"Network {data.qm_name} updated", compartment_id)
        await db.commit()
        return network


@sanitized.enforce
async def delete_network(db: AsyncSession, compartment_id: SafeSlug, network_id: SafeUUID) -> None:
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(NetworkRow.__table__).where(
                NetworkRow.id == network_id, NetworkRow.compartment_id == compartment_id
            )
        )
        row = result.mappings().first()
        if row is None:
            return

        network_name = SafeResourceName.of(row["qm_name"], "db:networks.qm_name")

        # Block deletion if any container references this network
        containers = await list_containers(db, compartment_id)
        referencing = [c.qm_name for c in containers if c.network == str(network_name)]
        if referencing:
            raise ValueError(
                f"Network is referenced by container(s): {', '.join(str(n) for n in referencing)}"
            )

        # Remove the .network unit file
        if user_manager.user_exists(compartment_id):
            with contextlib.suppress(Exception):  # Best-effort: unit file may already be absent
                await _run_in_ctx(quadlet_writer.remove_network_unit, compartment_id, network_name)
            with contextlib.suppress(Exception):  # Best-effort: unit file may already be absent
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)

        await _run_in_ctx(
            user_manager.cleanup_resource_config_dir,
            compartment_id,
            SafeStr.of("network", "resource_type"),
            network_name,
        )

        await db.execute(delete(NetworkRow).where(NetworkRow.id == network_id))
        await db.commit()

        await _log_event(db, "network_delete", f"Network {network_name} deleted", compartment_id)
        await db.commit()


@sanitized.enforce
def _pod_values(data: PodCreate) -> dict:
    """Build a column-value dict from a PodCreate model."""
    return {
        "qm_name": data.qm_name,
        "pod_name_override": data.pod_name_override,
        "network": data.network,
        "hostname": data.hostname,
        "exit_policy": data.exit_policy,
        "stop_timeout": data.stop_timeout,
        "shm_size": data.shm_size,
        "ip": data.ip,
        "ip6": data.ip6,
        "user_ns": data.user_ns,
        "sub_uid_map": data.sub_uid_map,
        "sub_gid_map": data.sub_gid_map,
        "containers_conf_module": data.containers_conf_module,
        "service_name": data.service_name,
        "publish_ports": json.dumps([str(p) for p in data.publish_ports]),
        "global_args": json.dumps([str(a) for a in data.global_args]),
        "podman_args": json.dumps([str(a) for a in data.podman_args]),
        "volumes": json.dumps([str(v) for v in data.volumes]),
        "dns": json.dumps([str(d) for d in data.dns]),
        "dns_search": json.dumps([str(d) for d in data.dns_search]),
        "dns_option": json.dumps([str(d) for d in data.dns_option]),
        "add_host": json.dumps([str(h) for h in data.add_host]),
        "uid_map": json.dumps([str(m) for m in data.uid_map]),
        "gid_map": json.dumps([str(m) for m in data.gid_map]),
        "network_aliases": json.dumps([str(a) for a in data.network_aliases]),
        "labels": json.dumps({str(k): str(v) for k, v in data.labels.items()}),
    }


@sanitized.enforce
async def add_pod(db: AsyncSession, compartment_id: SafeSlug, data: PodCreate) -> Pod:
    async with _compartment_lock(compartment_id):
        pid = SafeUUID.trusted(str(uuid.uuid4()), "add_pod")
        vals = _pod_values(data)
        vals.update(id=pid, compartment_id=compartment_id)
        await db.execute(insert(PodRow).values(**vals))
        await db.commit()

        pod = Pod(
            id=pid,
            compartment_id=compartment_id,
            created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_pod"),
            **data.model_dump(),
        )
        try:
            if user_manager.user_exists(compartment_id):
                # Write network units first if needed, then the pod unit
                comp = await get_compartment(db, compartment_id)
                net_names_used = {
                    c.network
                    for c in comp.containers
                    if c.network not in ("host", "none", "slirp4netns", "pasta") and not c.pod
                }
                for net in comp.networks:
                    if net.qm_name in net_names_used:
                        await _run_in_ctx(quadlet_writer.write_network_unit, compartment_id, net)
                await _run_in_ctx(quadlet_writer.write_pod_unit, compartment_id, pod)
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        except Exception as exc:
            logger.warning("Filesystem write failed for pod %s, rolling back DB insert", pid)
            await db.execute(delete(PodRow).where(PodRow.id == pid))
            await db.commit()
            raise FileWriteFailed("pod", str(pid), rolled_back=True) from exc

        await _log_event(db, "pod_add", f"Pod {data.qm_name} added", compartment_id)
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
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(PodRow.__table__).where(
                PodRow.id == pod_id, PodRow.compartment_id == compartment_id
            )
        )
        row = result.mappings().first()
        if row is None:
            return
        pod_name = SafeResourceName.of(row["qm_name"], "db:pods.qm_name")

        # Refuse if any container still references this pod
        containers = await list_containers(db, compartment_id)
        using = [c.qm_name for c in containers if c.pod == pod_name]
        if using:
            raise ValueError(
                f"Pod is used by container(s): {', '.join(using)}. "
                "Remove the pod assignment from containers first."
            )

        await _run_in_ctx(quadlet_writer.remove_pod_unit, compartment_id, pod_name)
        await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        await _run_in_ctx(
            user_manager.cleanup_resource_config_dir,
            compartment_id,
            SafeStr.of("pod", "resource_type"),
            pod_name,
        )

        await db.execute(delete(PodRow).where(PodRow.id == pod_id))
        await db.commit()


@sanitized.enforce
async def update_pod(
    db: AsyncSession,
    compartment_id: SafeSlug,
    pod_id: SafeUUID,
    data: PodCreate,
) -> Pod:
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(PodRow.__table__).where(
                PodRow.id == str(pod_id),
                PodRow.compartment_id == str(compartment_id),
            )
        )
        row = result.mappings().first()
        if row is None:
            msg = "Pod not found"
            raise ValueError(msg)
        vals = _pod_values(data)
        vals.pop("qm_name", None)  # qm_name is immutable
        await db.execute(PodRow.__table__.update().where(PodRow.id == str(pod_id)).values(**vals))
        await db.commit()
        pod = await _validate_row(
            db,
            Pod,
            PodRow.__table__,
            dict(row) | vals,
        )
        try:
            if user_manager.user_exists(compartment_id):
                await _run_in_ctx(quadlet_writer.write_pod_unit, compartment_id, pod)
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        except Exception as exc:
            logger.error(
                "Filesystem write failed after DB update for pod %s — resync recommended",
                pod_id,
            )
            raise FileWriteFailed("pod", str(pod_id), rolled_back=False) from exc
        await _log_event(db, "pod_update", f"Pod {data.qm_name} updated", compartment_id)
        await db.commit()
        return pod


@sanitized.enforce
def _image_values(data: ImageCreate) -> dict:
    """Build a column-value dict from an ImageCreate model."""
    return {
        "qm_name": data.qm_name,
        "image": data.image,
        "auth_file": data.auth_file,
        "all_tags": data.all_tags,
        "arch": data.arch,
        "cert_dir": data.cert_dir,
        "creds": data.creds,
        "decryption_key": data.decryption_key,
        "os": data.os,
        "tls_verify": data.tls_verify,
        "variant": data.variant,
        "containers_conf_module": data.containers_conf_module,
        "global_args": json.dumps([str(a) for a in data.global_args]),
        "podman_args": json.dumps([str(a) for a in data.podman_args]),
        "service_name": data.service_name,
        "image_tags": json.dumps([str(t) for t in data.image_tags]),
        "retry": data.retry,
        "retry_delay": data.retry_delay,
        "policy": data.policy,
    }


@sanitized.enforce
async def add_image(db: AsyncSession, compartment_id: SafeSlug, data: ImageCreate) -> Image:
    async with _compartment_lock(compartment_id):
        iid = SafeUUID.trusted(str(uuid.uuid4()), "add_image")
        now = SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_image")
        vals = _image_values(data)
        vals.update(id=iid, compartment_id=compartment_id)
        await db.execute(insert(ImageRow).values(**vals))
        await db.commit()

        iu = Image(id=iid, compartment_id=compartment_id, created_at=now, **data.model_dump())
        try:
            if user_manager.user_exists(compartment_id):
                await _run_in_ctx(quadlet_writer.write_image_unit, compartment_id, iu)
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        except Exception as exc:
            logger.warning("Filesystem write failed for image %s, rolling back DB insert", iid)
            await db.execute(delete(ImageRow).where(ImageRow.id == iid))
            await db.commit()
            raise FileWriteFailed("image", str(iid), rolled_back=True) from exc

        await _log_event(db, "image_add", f"Image {data.qm_name} added", compartment_id)
        await db.commit()
        return iu


@sanitized.enforce
async def update_image(
    db: AsyncSession,
    compartment_id: SafeSlug,
    image_unit_id: SafeUUID,
    data: ImageCreate,
) -> Image:
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(ImageRow.__table__).where(
                ImageRow.id == str(image_unit_id),
                ImageRow.compartment_id == str(compartment_id),
            )
        )
        row = result.mappings().first()
        if row is None:
            msg = "Image not found"
            raise ValueError(msg)
        vals = _image_values(data)
        vals.pop("qm_name", None)  # qm_name is immutable
        await db.execute(
            ImageRow.__table__.update().where(ImageRow.id == str(image_unit_id)).values(**vals)
        )
        await db.commit()
        iu = await _validate_row(
            db,
            Image,
            ImageRow.__table__,
            dict(row) | vals,
        )
        try:
            if user_manager.user_exists(compartment_id):
                await _run_in_ctx(quadlet_writer.write_image_unit, compartment_id, iu)
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        except Exception as exc:
            logger.error(
                "Filesystem write failed after DB update for image %s — resync recommended",
                image_unit_id,
            )
            raise FileWriteFailed("image", str(image_unit_id), rolled_back=False) from exc
        await _log_event(db, "image_update", f"Image {data.qm_name} updated", compartment_id)
        await db.commit()
        return iu


@sanitized.enforce
async def list_images(db: AsyncSession, compartment_id: SafeSlug) -> list[Image]:
    result = await db.execute(
        select(ImageRow.__table__)
        .where(ImageRow.compartment_id == compartment_id)
        .order_by(ImageRow.created_at)
    )
    return await _validate_rows(db, Image, ImageRow.__table__, result.mappings().all())


@sanitized.enforce
async def delete_image(db: AsyncSession, compartment_id: SafeSlug, image_unit_id: SafeUUID) -> None:
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(ImageRow.__table__).where(
                ImageRow.id == image_unit_id, ImageRow.compartment_id == compartment_id
            )
        )
        row = result.mappings().first()
        if row is None:
            return
        name = SafeResourceName.of(row["qm_name"], "db:images.qm_name")

        # Refuse deletion if any container references this image
        containers = await list_containers(db, compartment_id)
        blocking = [c.qm_name for c in containers if c.image == f"{name}.image"]
        if blocking:
            raise ValueError(
                f"Image is referenced by container(s): {', '.join(blocking)}. "
                "Update or remove the container(s) first."
            )

        await _run_in_ctx(quadlet_writer.remove_image_unit, compartment_id, name)
        await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        await _run_in_ctx(
            user_manager.cleanup_resource_config_dir,
            compartment_id,
            SafeStr.of("image", "resource_type"),
            name,
        )

        await db.execute(delete(ImageRow).where(ImageRow.id == image_unit_id))
        await db.commit()


# ---------------------------------------------------------------------------
# Artifact units
# ---------------------------------------------------------------------------


@sanitized.enforce
async def list_artifacts(db: AsyncSession, compartment_id: SafeSlug) -> list[Artifact]:
    result = await db.execute(
        select(ArtifactRow.__table__)
        .where(ArtifactRow.compartment_id == compartment_id)
        .order_by(ArtifactRow.created_at)
    )
    return await _validate_rows(db, Artifact, ArtifactRow.__table__, result.mappings().all())


@sanitized.enforce
async def add_artifact(
    db: AsyncSession, compartment_id: SafeSlug, data: ArtifactCreate
) -> Artifact:
    async with _compartment_lock(compartment_id):
        aid = SafeUUID.trusted(str(uuid.uuid4()), "add_artifact")
        now = SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_artifact")
        await db.execute(
            insert(ArtifactRow).values(
                id=aid,
                compartment_id=compartment_id,
                qm_name=data.qm_name,
                artifact=data.artifact,
                auth_file=data.auth_file,
                cert_dir=data.cert_dir,
                containers_conf_module=data.containers_conf_module,
                creds=data.creds,
                decryption_key=data.decryption_key,
                global_args=json.dumps(list(data.global_args)),
                podman_args=json.dumps(list(data.podman_args)),
                quiet=data.quiet,
                retry=data.retry,
                retry_delay=data.retry_delay,
                service_name=data.service_name,
                tls_verify=data.tls_verify,
            )
        )
        await db.commit()

        artifact = Artifact(
            id=aid, compartment_id=compartment_id, created_at=now, **data.model_dump()
        )
        try:
            if user_manager.user_exists(compartment_id):
                await _run_in_ctx(quadlet_writer.write_artifact_unit, compartment_id, artifact)
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        except Exception as exc:
            logger.warning("Filesystem write failed for artifact %s, rolling back DB insert", aid)
            await db.execute(delete(ArtifactRow).where(ArtifactRow.id == aid))
            await db.commit()
            raise FileWriteFailed("artifact", str(aid), rolled_back=True) from exc

        await _log_event(db, "artifact_add", f"Artifact {data.qm_name} added", compartment_id)
        await db.commit()
        return artifact


@sanitized.enforce
async def update_artifact(
    db: AsyncSession,
    compartment_id: SafeSlug,
    artifact_id: SafeUUID,
    data: ArtifactCreate,
) -> Artifact:
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(ArtifactRow.__table__).where(
                ArtifactRow.id == str(artifact_id),
                ArtifactRow.compartment_id == str(compartment_id),
            )
        )
        row = result.mappings().first()
        if row is None:
            msg = "Artifact not found"
            raise ValueError(msg)
        vals = {
            "artifact": data.artifact,
            "auth_file": data.auth_file,
            "cert_dir": data.cert_dir,
            "containers_conf_module": data.containers_conf_module,
            "creds": data.creds,
            "decryption_key": data.decryption_key,
            "global_args": json.dumps(list(data.global_args)),
            "podman_args": json.dumps(list(data.podman_args)),
            "quiet": data.quiet,
            "retry": data.retry,
            "retry_delay": data.retry_delay,
            "service_name": data.service_name,
            "tls_verify": data.tls_verify,
        }
        await db.execute(
            ArtifactRow.__table__.update().where(ArtifactRow.id == str(artifact_id)).values(**vals)
        )
        await db.commit()
        artifact = await _validate_row(db, Artifact, ArtifactRow.__table__, dict(row) | vals)
        try:
            if user_manager.user_exists(compartment_id):
                await _run_in_ctx(quadlet_writer.write_artifact_unit, compartment_id, artifact)
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        except Exception as exc:
            logger.error(
                "Filesystem write failed after DB update for artifact %s — resync recommended",
                artifact_id,
            )
            raise FileWriteFailed("artifact", str(artifact_id), rolled_back=False) from exc
        await _log_event(db, "artifact_update", f"Artifact {data.qm_name} updated", compartment_id)
        await db.commit()
        return artifact


@sanitized.enforce
async def delete_artifact(
    db: AsyncSession, compartment_id: SafeSlug, artifact_id: SafeUUID
) -> None:
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(ArtifactRow.__table__).where(
                ArtifactRow.id == artifact_id, ArtifactRow.compartment_id == compartment_id
            )
        )
        row = result.mappings().first()
        if row is None:
            return
        name = SafeResourceName.of(row["qm_name"], "db:artifacts.qm_name")
        await _run_in_ctx(quadlet_writer.remove_artifact_unit, compartment_id, name)
        await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        await _run_in_ctx(
            user_manager.cleanup_resource_config_dir,
            compartment_id,
            SafeStr.of("artifact", "resource_type"),
            name,
        )
        await db.execute(delete(ArtifactRow).where(ArtifactRow.id == artifact_id))
        await db.commit()


# ---------------------------------------------------------------------------
# Builds
# ---------------------------------------------------------------------------


@sanitized.enforce
async def list_builds(db: AsyncSession, compartment_id: SafeSlug) -> list[Build]:
    result = await db.execute(
        select(BuildRow.__table__)
        .where(BuildRow.compartment_id == compartment_id)
        .order_by(BuildRow.created_at)
    )
    return await _validate_rows(db, Build, BuildRow.__table__, result.mappings().all())


@sanitized.enforce
async def add_build(db: AsyncSession, compartment_id: SafeSlug, data: BuildCreate) -> Build:
    async with _compartment_lock(compartment_id):
        bid = SafeUUID.trusted(str(uuid.uuid4()), "add_build")
        now = SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "add_build")

        # Write Containerfile to disk if content is provided
        if data.qm_containerfile_content:
            data.build_context = SafeStr.trusted(
                await _run_in_ctx(
                    user_manager.write_managed_containerfile,
                    compartment_id,
                    data.qm_name,
                    data.qm_containerfile_content,
                ),
                "build_context",
            )

        await db.execute(
            insert(BuildRow).values(
                id=bid,
                compartment_id=compartment_id,
                qm_name=data.qm_name,
                image_tag=data.image_tag,
                qm_containerfile_content=data.qm_containerfile_content,
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

        bu = Build(
            id=bid,
            compartment_id=compartment_id,
            qm_name=data.qm_name,
            image_tag=data.image_tag,
            qm_containerfile_content=data.qm_containerfile_content,
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

        try:
            if user_manager.user_exists(compartment_id):
                await _run_in_ctx(quadlet_writer.write_build, compartment_id, bu)
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        except Exception as exc:
            logger.warning("Filesystem write failed for build %s, rolling back DB insert", bid)
            await db.execute(delete(BuildRow).where(BuildRow.id == bid))
            await db.commit()
            raise FileWriteFailed("build", str(bid), rolled_back=True) from exc

        await _log_event(db, "build_add", f"Build {data.qm_name} added", compartment_id)
        await db.commit()
        return bu


@sanitized.enforce
async def update_build(
    db: AsyncSession,
    compartment_id: SafeSlug,
    build_unit_id: SafeUUID,
    data: BuildCreate,
) -> Build | None:
    async with _compartment_lock(compartment_id):
        # Write Containerfile to disk if content is provided
        if data.qm_containerfile_content:
            data.build_context = SafeStr.trusted(
                await _run_in_ctx(
                    user_manager.write_managed_containerfile,
                    compartment_id,
                    data.qm_name,
                    data.qm_containerfile_content,
                ),
                "build_context",
            )

        result = await db.execute(
            update(BuildRow)
            .where(BuildRow.id == build_unit_id, BuildRow.compartment_id == compartment_id)
            .values(
                image_tag=data.image_tag,
                qm_containerfile_content=data.qm_containerfile_content,
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

        bu_row = await db.execute(select(BuildRow.__table__).where(BuildRow.id == build_unit_id))
        bu = await _validate_row(db, Build, BuildRow.__table__, bu_row.mappings().first())
        if bu is None:
            return None

        if user_manager.user_exists(compartment_id):
            await _run_in_ctx(quadlet_writer.write_build, compartment_id, bu)
            await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)

        await _log_event(db, "build_update", f"Build {data.qm_name} updated", compartment_id)
        await db.commit()
        return bu


@sanitized.enforce
async def delete_build(db: AsyncSession, compartment_id: SafeSlug, build_unit_id: SafeUUID) -> None:
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(BuildRow.__table__).where(
                BuildRow.id == build_unit_id, BuildRow.compartment_id == compartment_id
            )
        )
        row = result.mappings().first()
        if row is None:
            return
        name = SafeResourceName.of(row["qm_name"], "db:builds.qm_name")

        # Refuse deletion if any container references this build
        containers = await list_containers(db, compartment_id)
        blocking = [c.qm_name for c in containers if c.qm_build_unit_name == name]
        if blocking:
            raise ValueError(
                f"Build is referenced by container(s): {', '.join(blocking)}. "
                "Update or remove the container(s) first."
            )

        await _run_in_ctx(quadlet_writer.remove_build_unit, compartment_id, name)
        await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        await _run_in_ctx(
            user_manager.cleanup_resource_config_dir,
            compartment_id,
            SafeStr.of("build", "resource_type"),
            name,
        )

        await db.execute(delete(BuildRow).where(BuildRow.id == build_unit_id))
        await db.commit()


@sanitized.enforce
async def add_container(
    db: AsyncSession, compartment_id: SafeSlug, data: ContainerCreate
) -> Container:
    async with _compartment_lock(compartment_id):
        cid = SafeUUID.trusted(str(uuid.uuid4()), "add_container")

        await db.execute(
            insert(ContainerRow).values(
                id=cid,
                compartment_id=compartment_id,
                qm_name=data.qm_name,
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
                qm_sort_order=data.qm_sort_order,
                apparmor_profile=data.apparmor_profile,
                qm_build_unit_name=data.qm_build_unit_name,
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
                hostname=data.hostname,
                dns=json.dumps(data.dns),
                dns_search=json.dumps(data.dns_search),
                dns_option=json.dumps(data.dns_option),
                pod=data.pod,
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

        try:
            await _run_in_ctx(
                _write_and_reload,
                compartment_id,
                container,
                comp_volumes,
                all_containers,
                comp,
            )
        except Exception as exc:
            logger.warning("Filesystem write failed for container %s, rolling back DB insert", cid)
            await db.execute(delete(ContainerRow).where(ContainerRow.id == cid))
            await db.commit()
            raise FileWriteFailed("container", str(cid), rolled_back=True) from exc

        await _log_event(
            db, "container_add", f"Container {data.qm_name} added", compartment_id, cid
        )
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

    if comp and container.network not in ("host", "none", "slirp4netns", "pasta"):
        for net in comp.networks:
            if net.qm_name == container.network:
                quadlet_writer.write_network_unit(compartment_id, net)
                break
    # If the container references an image unit (Image=name.image), Quadlet's generator
    # requires the .image quadlet file to be present at daemon-reload time or it will
    # silently skip generating the container's .service, causing "unit not found" on start.
    if comp and container.image.endswith(".image"):
        image_unit_name = container.image[: -len(".image")]
        for iu in comp.images:
            if iu.qm_name == image_unit_name:
                quadlet_writer.write_image_unit(compartment_id, iu)
                break
    quadlet_writer.write_container_unit(compartment_id, container, volumes)
    systemd_manager.daemon_reload(compartment_id)
    unit = SafeUnitName.of(f"{container.qm_name}.service", "_write_and_reload")
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
        .order_by(ContainerRow.qm_sort_order, ContainerRow.created_at)
    )
    return await _validate_rows(db, Container, ContainerRow.__table__, result.mappings().all())


@sanitized.enforce
async def update_container(
    db: AsyncSession,
    compartment_id: SafeSlug,
    container_id: SafeUUID,
    data: ContainerCreate,
) -> Container | None:
    async with _compartment_lock(compartment_id):
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
                qm_sort_order=data.qm_sort_order,
                apparmor_profile=data.apparmor_profile,
                qm_build_unit_name=data.qm_build_unit_name,
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
                hostname=data.hostname,
                dns=json.dumps(data.dns),
                dns_search=json.dumps(data.dns_search),
                dns_option=json.dumps(data.dns_option),
                pod=data.pod,
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

        try:
            await _run_in_ctx(
                _write_and_reload,
                compartment_id,
                container,
                comp_volumes,
                all_containers,
                comp,
            )
        except Exception as exc:
            logger.error(
                "Filesystem write failed after DB update for container %s — resync recommended",
                container_id,
            )
            raise FileWriteFailed("container", str(container_id), rolled_back=False) from exc
        return container


@sanitized.enforce
async def delete_container(
    db: AsyncSession, compartment_id: SafeSlug, container_id: SafeUUID
) -> None:
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(ContainerRow.__table__).where(
                ContainerRow.id == container_id, ContainerRow.compartment_id == compartment_id
            )
        )
        row = result.mappings().first()
        if row is None:
            return
        name = SafeResourceName.of(row["qm_name"], "db:containers.qm_name")

        await _run_in_ctx(
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
    user_manager.cleanup_resource_config_dir(
        service_id, SafeStr.of("container", "resource_type"), container_name
    )


@sanitized.enforce
async def enable_compartment(db: AsyncSession, compartment_id: SafeSlug) -> None:
    async with _compartment_lock(compartment_id):
        containers = await list_containers(db, compartment_id)
        for container in containers:
            await _run_in_ctx(
                systemd_manager.enable_unit,
                compartment_id,
                SafeUnitName.of(container.qm_name, "enable_compartment"),
            )
        await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)


@sanitized.enforce
async def disable_compartment(db: AsyncSession, compartment_id: SafeSlug) -> None:
    async with _compartment_lock(compartment_id):
        containers = await list_containers(db, compartment_id)
        for container in containers:
            await _run_in_ctx(
                systemd_manager.disable_unit,
                compartment_id,
                SafeUnitName.of(container.qm_name, "disable_compartment"),
            )
        await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)


@sanitized.enforce
async def start_compartment(db: AsyncSession, compartment_id: SafeSlug) -> list[dict]:
    async with _compartment_lock(compartment_id):
        # Ensure subuid/subgid are configured (idempotent — skipped if already set)
        username = user_manager._username(compartment_id)
        await _run_in_ctx(user_manager._setup_subuid_subgid, username)
        containers = await list_containers(db, compartment_id)
        comp = await get_compartment(db, compartment_id)
        # Ensure pod units exist
        for pod in comp.pods:
            await _run_in_ctx(quadlet_writer.write_pod_unit, compartment_id, pod)
        # Ensure quadlet-managed volume units exist
        for vol in comp.volumes:
            if vol.qm_use_quadlet:
                await _run_in_ctx(quadlet_writer.write_volume_unit, compartment_id, vol)
        # Ensure image units exist
        for iu in comp.images:
            await _run_in_ctx(quadlet_writer.write_image_unit, compartment_id, iu)
        # Ensure network units exist for containers using named networks (not in a pod)
        net_names_used = {
            c.network
            for c in containers
            if c.network not in ("host", "none", "slirp4netns", "pasta") and not c.pod
        }
        for net in comp.networks:
            if net.qm_name in net_names_used:
                await _run_in_ctx(quadlet_writer.write_network_unit, compartment_id, net)
        # Ensure all container unit files are on disk. This is normally done when containers
        # are saved, but files can be missing after a DB reset or manual cleanup.
        for container in containers:
            await _run_in_ctx(
                quadlet_writer.write_container_unit,
                compartment_id,
                container,
                comp.volumes,
            )
        # Always reload so Quadlet generates .service files from the unit files written above.
        await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        errors = []
        for container in sorted(containers, key=lambda c: c.qm_sort_order):
            unit = SafeUnitName.of(f"{container.qm_name}.service", "start_compartment")
            try:
                await _run_in_ctx(systemd_manager.start_unit, compartment_id, unit)
            except Exception as e:
                logger.error("Failed to start %s: %s", unit, e)
                errors.append({"unit": unit, "error": str(e)})
        await _log_event(db, "start", f"Compartment {compartment_id} started", compartment_id)
        await db.commit()
        return errors


@sanitized.enforce
async def stop_compartment(db: AsyncSession, compartment_id: SafeSlug) -> list[dict]:
    async with _compartment_lock(compartment_id):
        containers = await list_containers(db, compartment_id)
        errors = []
        for container in sorted(containers, key=lambda c: c.qm_sort_order, reverse=True):
            unit = SafeUnitName.of(f"{container.qm_name}.service", "stop_compartment")
            try:
                await _run_in_ctx(systemd_manager.stop_unit, compartment_id, unit)
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
async def start_container(
    db: AsyncSession, compartment_id: SafeSlug, container_id: SafeUUID
) -> None:
    async with _compartment_lock(compartment_id):
        container = await get_container(db, container_id)
        if container is None or container.compartment_id != compartment_id:
            raise ValueError("Container not found")
        unit = SafeUnitName.of(f"{container.qm_name}.service", "start_container")
        await _run_in_ctx(systemd_manager.start_unit, compartment_id, unit)
        await _log_event(
            db,
            "container_start",
            f"Container {container.qm_name} started",
            compartment_id,
            container_id,
        )
        await db.commit()


@sanitized.enforce
async def stop_container(
    db: AsyncSession, compartment_id: SafeSlug, container_id: SafeUUID
) -> None:
    async with _compartment_lock(compartment_id):
        container = await get_container(db, container_id)
        if container is None or container.compartment_id != compartment_id:
            raise ValueError("Container not found")
        unit = SafeUnitName.of(f"{container.qm_name}.service", "stop_container")
        await _run_in_ctx(systemd_manager.stop_unit, compartment_id, unit)
        await _log_event(
            db,
            "container_stop",
            f"Container {container.qm_name} stopped",
            compartment_id,
            container_id,
        )
        await db.commit()


@sanitized.enforce
async def check_sync(db: AsyncSession, compartment_id: SafeSlug) -> list[dict]:
    """Return out-of-sync quadlet files for a compartment."""
    comp = await get_compartment(db, compartment_id)
    if comp is None:
        return []
    timers = await list_timers(db, compartment_id)
    return await _run_in_ctx(
        lambda: quadlet_writer.check_service_sync(
            compartment_id, comp.containers, comp.volumes, comp, timers
        ),
    )


@sanitized.enforce
async def resync_compartment(db: AsyncSession, compartment_id: SafeSlug) -> None:
    """Re-write all quadlet unit files from DB and reload systemd."""
    async with _compartment_lock(compartment_id):
        comp = await get_compartment(db, compartment_id)
        if comp is None:
            return

        timers = await list_timers(db, compartment_id)
        container_name_map = {c.id: c.qm_name for c in comp.containers}

        def _do_resync():
            for pod in comp.pods:
                quadlet_writer.write_pod_unit(compartment_id, pod)
            for vol in comp.volumes:
                if vol.qm_use_quadlet:
                    quadlet_writer.write_volume_unit(compartment_id, vol)
            for iu in comp.images:
                quadlet_writer.write_image_unit(compartment_id, iu)
            net_names_used = {
                c.network
                for c in comp.containers
                if c.network not in ("host", "none", "slirp4netns", "pasta") and not c.pod
            }
            for net in comp.networks:
                if net.qm_name in net_names_used:
                    quadlet_writer.write_network_unit(compartment_id, net)
            for container in comp.containers:
                quadlet_writer.write_container_unit(compartment_id, container, comp.volumes)
            for timer in timers:
                cname = container_name_map.get(timer.qm_container_id, timer.qm_container_name)
                quadlet_writer.write_timer_unit(compartment_id, timer, cname)
            systemd_manager.daemon_reload(compartment_id)
            # Restart any container that is currently active so new config takes effect
            for container in comp.containers:
                unit = SafeUnitName.of(f"{container.qm_name}.service", "resync_compartment")
                props = systemd_manager.get_unit_status(compartment_id, unit)
                if props.get("ActiveState") == "active":
                    systemd_manager.restart_unit(compartment_id, unit)

        await _run_in_ctx(_do_resync)


@sanitized.enforce
async def export_compartment_bundle(db: AsyncSession, compartment_id: SafeSlug) -> str | None:
    """Render all quadlet units for a compartment as a .quadlets bundle string."""
    comp = await get_compartment(db, compartment_id)
    if comp is None:
        return None
    return await _run_in_ctx(
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
    return await _run_in_ctx(
        systemd_manager.get_service_status,
        compartment_id,
        [SafeStr.of(c.qm_name, "container_name") for c in containers],
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
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(SecretRow.__table__).where(
                SecretRow.id == secret_id, SecretRow.compartment_id == compartment_id
            )
        )
        row = result.mappings().first()
        if row is None:
            return
        name = SafeSecretName.of(row["name"], "name")
        with contextlib.suppress(Exception):  # Best-effort: podman secret may already be removed
            await _run_in_ctx(secrets_manager.delete_podman_secret, compartment_id, name)
        await db.execute(delete(SecretRow).where(SecretRow.id == secret_id))
        await db.commit()


# ---------------------------------------------------------------------------
# Timers
# ---------------------------------------------------------------------------


@sanitized.enforce
async def create_timer(db: AsyncSession, compartment_id: SafeSlug, data: TimerCreate) -> Timer:
    """Persist a timer and write the .timer unit file."""
    async with _compartment_lock(compartment_id):
        # Resolve container name
        result = await db.execute(
            select(ContainerRow.__table__).where(
                ContainerRow.id == data.qm_container_id,
                ContainerRow.compartment_id == compartment_id,
            )
        )
        row = result.mappings().first()
        if row is None:
            raise ValueError("Container not found")
        container_name = SafeResourceName.of(row["qm_name"], "db:containers.qm_name")

        tid = SafeUUID.trusted(str(uuid.uuid4()), "create_timer")
        await db.execute(
            insert(TimerRow).values(
                id=tid,
                compartment_id=compartment_id,
                qm_container_id=data.qm_container_id,
                qm_name=data.qm_name,
                on_calendar=data.on_calendar,
                on_boot_sec=data.on_boot_sec,
                random_delay_sec=data.random_delay_sec,
                persistent=int(data.persistent),
                qm_enabled=int(data.qm_enabled),
            )
        )
        await db.commit()

        timer = Timer(
            id=tid,
            compartment_id=compartment_id,
            qm_container_id=data.qm_container_id,
            qm_container_name=container_name,
            qm_name=data.qm_name,
            on_calendar=data.on_calendar,
            on_boot_sec=data.on_boot_sec,
            random_delay_sec=data.random_delay_sec,
            persistent=data.persistent,
            qm_enabled=data.qm_enabled,
            created_at=SafeTimestamp.trusted(datetime.now(UTC).isoformat(), "create_timer"),
        )
        try:
            if user_manager.user_exists(compartment_id):
                await _run_in_ctx(
                    quadlet_writer.write_timer_unit, compartment_id, timer, container_name
                )
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
        except Exception as exc:
            logger.warning("Filesystem write failed for timer %s, rolling back DB insert", tid)
            await db.execute(delete(TimerRow).where(TimerRow.id == tid))
            await db.commit()
            raise FileWriteFailed("timer", str(tid), rolled_back=True) from exc
        return timer


@sanitized.enforce
async def list_timers(db: AsyncSession, compartment_id: SafeSlug) -> list[Timer]:
    result = await db.execute(
        select(TimerRow.__table__, ContainerRow.qm_name.label("qm_container_name"))
        .outerjoin(ContainerRow, TimerRow.qm_container_id == ContainerRow.id)
        .where(TimerRow.compartment_id == compartment_id)
        .order_by(TimerRow.created_at)
    )
    return await _validate_rows(db, Timer, TimerRow.__table__, result.mappings().all())


@sanitized.enforce
async def delete_timer(db: AsyncSession, compartment_id: SafeSlug, timer_id: SafeUUID) -> None:
    async with _compartment_lock(compartment_id):
        result = await db.execute(
            select(TimerRow.__table__).where(
                TimerRow.id == timer_id, TimerRow.compartment_id == compartment_id
            )
        )
        row = result.mappings().first()
        if row is None:
            return
        timer_name = SafeResourceName.of(row["qm_name"], "db:timers.qm_name")
        if user_manager.user_exists(compartment_id):
            with contextlib.suppress(Exception):  # Best-effort: timer unit may already be absent
                await _run_in_ctx(quadlet_writer.remove_timer_unit, compartment_id, timer_name)
            with contextlib.suppress(Exception):  # Best-effort: timer unit may already be absent
                await _run_in_ctx(systemd_manager.daemon_reload, compartment_id)
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
        "images": [iu.model_dump() for iu in comp.images],
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
    result = await db.execute(select(TemplateRow.__table__).where(TemplateRow.id == template_id))
    row = result.mappings().first()
    if row is None:
        raise ValueError("Template not found")

    config = json.loads(row["config_json"])

    # Create the compartment (provisions the Linux user, quadlet dir, etc.)
    await create_compartment(db, CompartmentCreate(id=compartment_id, description=description))

    # Recreate volumes (without qm_host_path / runtime data)
    for vd in config.get("volumes", []):
        vdata = VolumeCreate(
            qm_name=vd.get("qm_name", vd.get("name", "")),
            qm_selinux_context=vd.get(
                "qm_selinux_context", vd.get("selinux_context", "container_file_t")
            ),
            qm_owner_uid=vd.get("qm_owner_uid", vd.get("owner_uid", 0)),
            qm_use_quadlet=vd.get("qm_use_quadlet", vd.get("use_quadlet", False)),
            driver=vd.get("driver", vd.get("vol_driver", "")),
            device=vd.get("device", vd.get("vol_device", "")),
            options=vd.get("options", vd.get("vol_options", "")),
            copy=vd.get("copy", vd.get("vol_copy", True)),
            group=vd.get("group", vd.get("vol_group", "")),
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

    # Recreate images
    for iud in config.get("images", []):
        iudata = ImageCreate(
            qm_name=iud["qm_name"],
            image=iud["image"],
            auth_file=iud.get("auth_file", ""),
        )
        await add_image(db, compartment_id, iudata)

    # Recreate containers (reset build_context so it doesn't reference original paths)
    fresh_comp = await get_compartment(db, compartment_id)
    vol_name_to_id = {v.qm_name: v.id for v in fresh_comp.volumes}

    for cd in config.get("containers", []):
        # Remap volume IDs from source to new compartment
        new_volumes = []
        for vm in cd.get("volumes", []):
            old_vol_id = vm.get("volume_id", "")
            # Find new vol id by matching name
            new_vol_id = old_vol_id
            for sv in config.get("volumes", []):
                if sv.get("id") == old_vol_id:
                    sv_name = sv.get("qm_name", sv.get("name", ""))
                    new_vol_id = vol_name_to_id.get(sv_name, old_vol_id)
                    break
            new_volumes.append({**vm, "volume_id": new_vol_id})

        cdata = ContainerCreate(
            qm_name=cd.get("qm_name", cd.get("name", "")),
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
            qm_sort_order=cd.get("qm_sort_order", cd.get("sort_order", 0)),
            apparmor_profile=cd.get("apparmor_profile", ""),
            qm_build_unit_name=cd.get("qm_build_unit_name", cd.get("build_unit_name", "")),
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
            pod=cd.get("pod", cd.get("pod_name", "")),
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
            qm_container_name=data.qm_container_name,
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
        qm_container_name=data.qm_container_name,
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
        # Check if any existing pattern matches this new process
        patterns = await list_process_patterns(db, compartment_id)
        match = match_process_against_patterns(patterns, process_name, cmdline)
        await db.execute(
            insert(ProcessRow).values(
                id=pid,
                compartment_id=compartment_id,
                process_name=process_name,
                cmdline=cmdline,
                known=int(bool(match)),
                pattern_id=match.id if match else None,
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
) -> ProcessPattern | None:
    """Mark a process as known (creating a pattern) or unknown (unlinking from pattern).

    Returns the created pattern when known=True, or None when known=False.
    """
    if known:
        # Read the process to get its name and cmdline
        result = await db.execute(
            select(ProcessRow.__table__).where(
                ProcessRow.id == process_id, ProcessRow.compartment_id == compartment_id
            )
        )
        row = result.mappings().first()
        if not row:
            return None
        # Already linked to a pattern — nothing to do
        if row["pattern_id"]:
            pat_result = await db.execute(
                select(ProcessPatternRow.__table__).where(ProcessPatternRow.id == row["pattern_id"])
            )
            pat_row = pat_result.mappings().first()
            if pat_row:
                return await _validate_row(db, ProcessPattern, ProcessPatternRow.__table__, pat_row)
        process_name = SafeStr.of(row["process_name"], "db:processes.process_name")
        cmdline = str(row["cmdline"])
        escaped = re.escape(cmdline)
        cmdline_pattern = SafeRegex.of(escaped, "escaped_cmdline")
        segments = json.dumps([{"t": "l", "v": cmdline}])
        segments_safe = SafeStr.of(segments, "segments_json")
        return await create_process_pattern(
            db, compartment_id, process_name, cmdline_pattern, segments_safe
        )
    else:
        # Unlink from pattern but don't delete the pattern
        await db.execute(
            update(ProcessRow)
            .where(ProcessRow.id == process_id, ProcessRow.compartment_id == compartment_id)
            .values(known=0, pattern_id=None)
        )
        await db.commit()
        return None


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
# Process monitor — pattern CRUD
# ---------------------------------------------------------------------------


@sanitized.enforce
async def list_process_patterns(db: AsyncSession, compartment_id: SafeSlug) -> list[ProcessPattern]:
    result = await db.execute(
        select(ProcessPatternRow.__table__)
        .where(ProcessPatternRow.compartment_id == compartment_id)
        .order_by(ProcessPatternRow.created_at.asc())
    )
    return await _validate_rows(
        db, ProcessPattern, ProcessPatternRow.__table__, result.mappings().all()
    )


@sanitized.enforce
def match_process_against_patterns(
    patterns: list[ProcessPattern],
    process_name: SafeStr,
    cmdline: SafeMultilineStr,
) -> ProcessPattern | None:
    """Return the first pattern matching this process, or None."""
    for p in patterns:
        if p.process_name == process_name:
            with contextlib.suppress(re.error):
                if re.fullmatch(str(p.cmdline_pattern), str(cmdline)):
                    return p
    return None


@sanitized.enforce
async def _check_pattern_overlap(
    db: AsyncSession,
    compartment_id: SafeSlug,
    process_name: SafeStr,
    cmdline_pattern: SafeRegex,
    exclude_pattern_id: SafeUUID | None = None,
) -> ProcessPattern | None:
    """Return the conflicting pattern if the new pattern would overlap, else None."""
    # codeql[py/regex-injection] cmdline_pattern is SafeRegex — pre-validated branded type
    compiled = re.compile(str(cmdline_pattern))
    result = await db.execute(
        select(ProcessRow.__table__).where(
            ProcessRow.compartment_id == compartment_id,
            ProcessRow.process_name == process_name,
            ProcessRow.pattern_id.isnot(None),
        )
    )
    for row in result.mappings().all():
        if exclude_pattern_id and row["pattern_id"] == str(exclude_pattern_id):
            continue
        with contextlib.suppress(re.error):
            if compiled.fullmatch(row["cmdline"]):
                # This process is already covered by another pattern — find it
                pat_result = await db.execute(
                    select(ProcessPatternRow.__table__).where(
                        ProcessPatternRow.id == row["pattern_id"]
                    )
                )
                pat_row = pat_result.mappings().first()
                if pat_row:
                    return await _validate_row(
                        db, ProcessPattern, ProcessPatternRow.__table__, pat_row
                    )
    return None


@sanitized.enforce
async def create_process_pattern(
    db: AsyncSession,
    compartment_id: SafeSlug,
    process_name: SafeStr,
    cmdline_pattern: SafeRegex,
    segments_json: SafeStr,
) -> ProcessPattern:
    """Create a process pattern and link matching processes."""
    # Check overlap
    conflict = await _check_pattern_overlap(db, compartment_id, process_name, cmdline_pattern)
    if conflict:
        raise ValueError(f"Pattern overlaps with existing pattern {conflict.cmdline_pattern!r}")

    pid = SafeUUID.trusted(str(uuid.uuid4()), "create_process_pattern")
    try:
        await db.execute(
            insert(ProcessPatternRow).values(
                id=pid,
                compartment_id=compartment_id,
                process_name=process_name,
                cmdline_pattern=cmdline_pattern,
                segments_json=segments_json,
            )
        )
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError(f"Pattern already exists for {process_name!r} with this cmdline") from exc

    # Link matching unlinked processes
    # codeql[py/regex-injection] cmdline_pattern is SafeRegex — pre-validated branded type
    compiled = re.compile(str(cmdline_pattern))
    result = await db.execute(
        select(ProcessRow.__table__).where(
            ProcessRow.compartment_id == compartment_id,
            ProcessRow.process_name == process_name,
            ProcessRow.pattern_id.is_(None),
        )
    )
    for row in result.mappings().all():
        with contextlib.suppress(re.error):
            if compiled.fullmatch(row["cmdline"]):
                await db.execute(
                    update(ProcessRow)
                    .where(ProcessRow.id == row["id"])
                    .values(known=1, pattern_id=pid)
                )

    await db.commit()
    pat_result = await db.execute(
        select(ProcessPatternRow.__table__).where(ProcessPatternRow.id == pid)
    )
    return await _validate_row(
        db, ProcessPattern, ProcessPatternRow.__table__, pat_result.mappings().first()
    )


@sanitized.enforce
async def update_process_pattern(
    db: AsyncSession,
    compartment_id: SafeSlug,
    pattern_id: SafeUUID,
    cmdline_pattern: SafeRegex,
    segments_json: SafeStr,
) -> ProcessPattern:
    """Update a pattern's regex and relink processes."""
    # Get the pattern to know its process_name
    pat_result = await db.execute(
        select(ProcessPatternRow.__table__).where(
            ProcessPatternRow.id == pattern_id,
            ProcessPatternRow.compartment_id == compartment_id,
        )
    )
    pat_row = pat_result.mappings().first()
    if not pat_row:
        raise ValueError("Pattern not found")

    process_name = SafeStr.of(pat_row["process_name"], "db:process_patterns.process_name")

    # Check overlap excluding self
    conflict = await _check_pattern_overlap(
        db, compartment_id, process_name, cmdline_pattern, exclude_pattern_id=pattern_id
    )
    if conflict:
        raise ValueError(f"Pattern overlaps with existing pattern {conflict.cmdline_pattern!r}")

    # Update the pattern
    await db.execute(
        update(ProcessPatternRow)
        .where(ProcessPatternRow.id == pattern_id)
        .values(cmdline_pattern=cmdline_pattern, segments_json=segments_json)
    )

    # Unlink processes that no longer match
    # codeql[py/regex-injection] cmdline_pattern is SafeRegex — pre-validated branded type
    compiled = re.compile(str(cmdline_pattern))
    linked_result = await db.execute(
        select(ProcessRow.__table__).where(ProcessRow.pattern_id == pattern_id)
    )
    for row in linked_result.mappings().all():
        matches = False
        with contextlib.suppress(re.error):
            matches = bool(compiled.fullmatch(row["cmdline"]))
        if not matches:
            await db.execute(
                update(ProcessRow)
                .where(ProcessRow.id == row["id"])
                .values(known=0, pattern_id=None)
            )

    # Link newly matching unlinked processes
    unlinked_result = await db.execute(
        select(ProcessRow.__table__).where(
            ProcessRow.compartment_id == compartment_id,
            ProcessRow.process_name == process_name,
            ProcessRow.pattern_id.is_(None),
        )
    )
    for row in unlinked_result.mappings().all():
        with contextlib.suppress(re.error):
            if compiled.fullmatch(row["cmdline"]):
                await db.execute(
                    update(ProcessRow)
                    .where(ProcessRow.id == row["id"])
                    .values(known=1, pattern_id=pattern_id)
                )

    await db.commit()
    updated_result = await db.execute(
        select(ProcessPatternRow.__table__).where(ProcessPatternRow.id == pattern_id)
    )
    return await _validate_row(
        db, ProcessPattern, ProcessPatternRow.__table__, updated_result.mappings().first()
    )


@sanitized.enforce
async def delete_process_pattern(
    db: AsyncSession, compartment_id: SafeSlug, pattern_id: SafeUUID
) -> None:
    """Delete a pattern and unlink all its processes."""
    # Unlink processes
    await db.execute(
        update(ProcessRow)
        .where(ProcessRow.pattern_id == pattern_id)
        .values(known=0, pattern_id=None)
    )
    # Delete the pattern
    await db.execute(
        delete(ProcessPatternRow).where(
            ProcessPatternRow.id == pattern_id,
            ProcessPatternRow.compartment_id == compartment_id,
        )
    )
    await db.commit()


@sanitized.enforce
async def get_pattern_match_count(db: AsyncSession, pattern_id: SafeUUID) -> int:
    """Return the number of processes linked to a pattern."""
    result = await db.execute(
        select(func.count())
        .select_from(ProcessRow.__table__)
        .where(ProcessRow.pattern_id == pattern_id)
    )
    return result.scalar_one()


@sanitized.enforce
async def get_pattern_matches(
    db: AsyncSession, compartment_id: SafeSlug, pattern_id: SafeUUID
) -> list[Process]:
    """Return all processes linked to a pattern."""
    result = await db.execute(
        select(ProcessRow.__table__).where(
            ProcessRow.compartment_id == compartment_id,
            ProcessRow.pattern_id == pattern_id,
        )
    )
    return await _validate_rows(db, Process, ProcessRow.__table__, result.mappings().all())


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

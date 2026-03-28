"""Background notification monitor — fires webhooks on container state transitions.

Also runs:
  - metrics_loop: records per-compartment CPU/memory/disk every METRICS_INTERVAL seconds
    into the metrics_history table (Feature 9).
  - restart/failure counters: updated in container_restart_stats on every state transition
    that matches on_failure or on_restart (Feature 10).
"""

import asyncio
import contextlib
import datetime
import json as _json
import logging
import random
import urllib.error
import urllib.request
from collections.abc import AsyncGenerator

from sqlalchemy import delete, insert, update

from quadletman.config.settings import settings
from quadletman.db.orm import CompartmentRow, ContainerRestartStatsRow, MetricsHistoryRow
from quadletman.models import sanitized
from quadletman.models.sanitized import (
    SafeIpAddress,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeWebhookUrl,
)
from quadletman.podman import get_features
from quadletman.services import compartment_manager, metrics, systemd_manager, user_manager
from quadletman.services.user_manager import get_uid

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _loop_session(db_factory) -> AsyncGenerator:
    """Yield a DB session with guaranteed rollback on exception and proper cleanup."""
    gen = db_factory()
    db = await gen.__anext__()
    try:
        yield db
    except Exception:
        await db.rollback()
        raise
    finally:
        with contextlib.suppress(StopAsyncIteration):
            await gen.__anext__()


# In-memory: {compartment_id/container_name -> last known active_state}
_last_states: dict[str, str] = {}

# Retry configuration (module-level so tests can patch them)
_MAX_ATTEMPTS = settings.webhook_max_retries
_RETRY_BASE_DELAY = settings.webhook_retry_delay  # seconds; actual delays: 2s, 4s


def _sync_post(url: str, data: bytes, headers: dict[str, str]) -> int:
    """Blocking HTTP POST; returns the status code (or -1 on network error)."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=settings.webhook_timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return -1


@sanitized.enforce
async def fire_webhook(webhook_url: SafeWebhookUrl, webhook_secret: SafeStr, payload: dict) -> None:
    """POST the payload to a webhook URL with exponential-backoff retry.

    Errors are logged but not raised. Up to _MAX_ATTEMPTS total attempts are made.
    """
    headers = {"Content-Type": "application/json"}
    if webhook_secret:
        headers["X-Webhook-Secret"] = webhook_secret
    data = _json.dumps(payload).encode()
    loop = asyncio.get_event_loop()

    for attempt in range(_MAX_ATTEMPTS):
        try:
            status = await loop.run_in_executor(None, _sync_post, webhook_url, data, headers)
            if 0 < status < 400:
                return  # success
            logger.warning(
                "Webhook delivery to %s returned HTTP %s (attempt %d/%d)",
                webhook_url,
                status,
                attempt + 1,
                _MAX_ATTEMPTS,
            )
        except Exception as exc:
            logger.warning(
                "Webhook delivery to %s failed: %s (attempt %d/%d)",
                webhook_url,
                exc,
                attempt + 1,
                _MAX_ATTEMPTS,
            )
        if attempt < _MAX_ATTEMPTS - 1:
            await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))

    logger.error("Webhook delivery to %s failed after %d attempts", webhook_url, _MAX_ATTEMPTS)


@sanitized.enforce
async def monitor_loop(db_factory) -> None:
    """Continuously poll container states and trigger webhooks on transitions.

    db_factory is an async generator (same as FastAPI's get_db dependency).
    """
    # Brief startup delay to let the app fully initialise
    await asyncio.sleep(10)

    while True:
        try:
            await _check_once(db_factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Notification monitor error: %s", exc)
        await asyncio.sleep(settings.poll_interval + random.uniform(-5, 5))


@sanitized.enforce
async def metrics_loop(db_factory) -> None:
    """Periodically snapshot per-compartment metrics into metrics_history (Feature 9)."""
    await asyncio.sleep(15)  # brief startup delay

    while True:
        try:
            async with _loop_session(db_factory) as db:
                compartments = await compartment_manager.list_compartments(db)
                loop = asyncio.get_event_loop()
                for comp in compartments:
                    if not user_manager.user_exists(comp.id):
                        continue
                    try:
                        uid = await loop.run_in_executor(None, get_uid, comp.id)
                        m = await loop.run_in_executor(None, metrics.get_metrics, comp.id, uid)
                        await db.execute(
                            insert(MetricsHistoryRow).values(
                                compartment_id=comp.id,
                                cpu_percent=m["cpu_percent"],
                                memory_bytes=m["mem_bytes"],
                                disk_bytes=m["disk_bytes"],
                            )
                        )
                    except Exception as exc:
                        await db.rollback()
                        logger.warning("Metrics snapshot failed for %s: %s", comp.id, exc)
                await db.commit()

                # Prune rows older than the retention window
                retention_h = settings.metrics_retention_hours
                if retention_h > 0:
                    cutoff = (
                        datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=retention_h)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    await db.execute(
                        delete(MetricsHistoryRow).where(MetricsHistoryRow.recorded_at < cutoff)
                    )
                    await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Metrics loop error: %s", exc)
        await asyncio.sleep(settings.metrics_interval + random.uniform(-15, 15))


@sanitized.enforce
async def process_monitor_loop(db_factory) -> None:
    """Periodically record running processes and fire webhooks for newly discovered ones.

    Each unique (process_name, cmdline) pair is upserted into the processes table.
    A webhook fires only when a process is seen for the very first time (is_new=True).
    The known flag is user-managed and never reset by the monitor.
    """
    await asyncio.sleep(20)  # brief startup delay

    while True:
        try:
            async with _loop_session(db_factory) as db:
                compartments = await compartment_manager.list_compartments(db)
                hooks = await compartment_manager.list_all_notification_hooks(db)

                # {compartment_id -> [hook, ...]} for on_unexpected_process hooks
                alert_hooks: dict[str, list] = {}
                for h in hooks:
                    if h.event_type == "on_unexpected_process" and h.enabled:
                        alert_hooks.setdefault(h.compartment_id, []).append(h)

                if any(comp.process_monitor_enabled for comp in compartments):
                    loop = asyncio.get_event_loop()
                    for comp in compartments:
                        if not comp.process_monitor_enabled:
                            continue
                        if not user_manager.user_exists(comp.id):
                            continue
                        try:
                            uid = await loop.run_in_executor(None, get_uid, comp.id)
                            procs = await loop.run_in_executor(None, metrics.get_processes, uid)

                            for proc in procs:
                                name = proc["name"]
                                cmdline = proc.get("cmdline", name)
                                process, is_new = await compartment_manager.upsert_process(
                                    db, comp.id, name, cmdline
                                )
                                if is_new and not process.known:
                                    now_iso = datetime.datetime.now(datetime.UTC).isoformat() + "Z"
                                    payload = {
                                        "event": "on_unexpected_process",
                                        "compartment_id": comp.id,
                                        "process_name": name,
                                        "cmdline": cmdline,
                                        "timestamp": now_iso,
                                    }
                                    for hook in alert_hooks.get(comp.id, []):
                                        asyncio.create_task(
                                            fire_webhook(
                                                hook.webhook_url, hook.webhook_secret, payload
                                            )
                                        )
                        except Exception as exc:
                            await db.rollback()
                            logger.warning("Process monitor failed for %s: %s", comp.id, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Process monitor loop error: %s", exc)

        await asyncio.sleep(settings.process_monitor_interval + random.uniform(-5, 5))


@sanitized.enforce
async def connection_monitor_loop(db_factory) -> None:
    """Periodically record container connections and fire webhooks for new ones.

    Each unique (container_name, proto, dst_ip, dst_port, direction) tuple is upserted
    into the connections table. A webhook fires only when a connection is seen for the
    very first time (is_new=True) AND the connection does not match any allowlist rule
    for the compartment. History cleanup (retention policy) runs at the end of every
    poll cycle. Uses /proc/<pid>/net/tcp to read the container's network namespace.
    """
    await asyncio.sleep(25)  # brief startup delay

    while True:
        try:
            async with _loop_session(db_factory) as db:
                compartments = await compartment_manager.list_compartments(db)
                hooks = await compartment_manager.list_all_notification_hooks(db)

                # {compartment_id -> [hook, ...]} for on_unexpected_connection hooks
                alert_hooks: dict[str, list] = {}
                for h in hooks:
                    if h.event_type == "on_unexpected_connection" and h.enabled:
                        alert_hooks.setdefault(h.compartment_id, []).append(h)

                if any(comp.connection_monitor_enabled for comp in compartments):
                    loop = asyncio.get_event_loop()
                    for comp in compartments:
                        if not comp.connection_monitor_enabled:
                            continue
                        if not user_manager.user_exists(comp.id):
                            continue
                        try:
                            rules = await compartment_manager.list_allowlist_rules(db, comp.id)
                            conns = await loop.run_in_executor(
                                None, metrics.get_connections, comp.id
                            )
                            for conn in conns:
                                _connection, is_new = await compartment_manager.upsert_connection(
                                    db,
                                    comp.id,
                                    SafeResourceName.of(
                                        conn["container_name"], "metrics:container_name"
                                    ),
                                    SafeStr.of(conn["proto"], "metrics:proto"),
                                    SafeIpAddress.of(conn["dst_ip"], "metrics:dst_ip"),
                                    conn["dst_port"],
                                    SafeStr.of(conn["direction"], "metrics:direction"),
                                )
                                if is_new and not compartment_manager.connection_is_allowlisted(
                                    rules,
                                    conn["proto"],
                                    SafeIpAddress.of(conn["dst_ip"], "metrics:dst_ip"),
                                    conn["dst_port"],
                                    SafeResourceName.of(
                                        conn["container_name"], "metrics:container_name"
                                    ),
                                    conn["direction"],
                                ):
                                    now_iso = datetime.datetime.now(datetime.UTC).isoformat() + "Z"
                                    payload = {
                                        "event": "on_unexpected_connection",
                                        "compartment_id": comp.id,
                                        "container_name": conn["container_name"],
                                        "proto": conn["proto"],
                                        "dst_ip": conn["dst_ip"],
                                        "dst_port": conn["dst_port"],
                                        "direction": conn["direction"],
                                        "timestamp": now_iso,
                                    }
                                    for hook in alert_hooks.get(comp.id, []):
                                        if (
                                            hook.qm_container_name
                                            and hook.qm_container_name != conn["container_name"]
                                        ):
                                            continue
                                        asyncio.create_task(
                                            fire_webhook(
                                                hook.webhook_url, hook.webhook_secret, payload
                                            )
                                        )
                        except Exception as exc:
                            await db.rollback()
                            logger.warning("Connection monitor failed for %s: %s", comp.id, exc)

                    # Apply per-compartment history retention policy
                    try:
                        await compartment_manager.cleanup_stale_connections(db)
                    except Exception as exc:
                        await db.rollback()
                        logger.warning("Connection cleanup failed: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Connection monitor loop error: %s", exc)

        await asyncio.sleep(settings.connection_monitor_interval + random.uniform(-5, 5))


# In-memory: {compartment_id/container_name/image -> True} — tracks notified
# pending image updates to avoid repeated webhook fires for the same update.
_notified_image_updates: dict[str, bool] = {}


@sanitized.enforce
async def image_update_monitor_loop(db_factory) -> None:
    """Periodically check for pending container image updates and fire webhooks.

    Uses ``podman auto-update --dry-run --format=json`` to detect containers
    with newer images available in the registry.  Only checks compartments that
    have ``on_image_update`` hooks and containers with ``auto_update=registry``.

    Deduplication: a webhook fires only once per (compartment, container, image)
    tuple.  The dedup cache is cleared when the update is no longer pending
    (i.e. the image was pulled or the container was removed).
    """
    await asyncio.sleep(30)  # startup delay

    while True:
        try:
            if not get_features().auto_update_dry_run:
                await asyncio.sleep(settings.image_update_check_interval + random.uniform(-30, 30))
                continue

            async with _loop_session(db_factory) as db:
                compartments = await compartment_manager.list_compartments(db)
                hooks = await compartment_manager.list_all_notification_hooks(db)

                # {compartment_id -> [hook, ...]} for on_image_update hooks
                alert_hooks: dict[str, list] = {}
                for h in hooks:
                    if h.event_type == "on_image_update" and h.enabled:
                        alert_hooks.setdefault(h.compartment_id, []).append(h)

                if alert_hooks:
                    loop = asyncio.get_event_loop()
                    still_pending: set[str] = set()

                    for comp in compartments:
                        if comp.id not in alert_hooks:
                            continue
                        # Only check if at least one container has auto_update=registry
                        if not any(c.auto_update == "registry" for c in (comp.containers or [])):
                            continue
                        if not user_manager.user_exists(comp.id):
                            continue
                        try:
                            updates = await loop.run_in_executor(
                                None, systemd_manager.auto_update_dry_run, comp.id
                            )
                            for upd in updates:
                                if upd.get("Updated") != "pending":
                                    continue
                                # Extract container name from unit name (strip .service)
                                unit = upd.get("Unit", "")
                                container_name = unit.removesuffix(".service")
                                image = upd.get("Image", "")
                                policy = upd.get("Policy", "")
                                dedup_key = f"{comp.id}/{container_name}/{image}"
                                still_pending.add(dedup_key)

                                if dedup_key in _notified_image_updates:
                                    continue  # already notified

                                _notified_image_updates[dedup_key] = True
                                now_iso = datetime.datetime.now(datetime.UTC).isoformat() + "Z"
                                payload = {
                                    "event": "on_image_update",
                                    "compartment_id": comp.id,
                                    "container_name": container_name,
                                    "image": image,
                                    "policy": policy,
                                    "timestamp": now_iso,
                                }
                                for hook in alert_hooks.get(comp.id, []):
                                    if (
                                        hook.qm_container_name
                                        and hook.qm_container_name != container_name
                                    ):
                                        continue
                                    asyncio.create_task(
                                        fire_webhook(hook.webhook_url, hook.webhook_secret, payload)
                                    )
                        except Exception as exc:
                            logger.warning("Image update check failed for %s: %s", comp.id, exc)

                    # Clean up dedup entries for updates that are no longer pending
                    stale = [k for k in _notified_image_updates if k not in still_pending]
                    for k in stale:
                        del _notified_image_updates[k]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Image update monitor loop error: %s", exc)

        await asyncio.sleep(settings.image_update_check_interval + random.uniform(-30, 30))


@sanitized.enforce
async def _check_once(db_factory) -> None:
    async with _loop_session(db_factory) as db:
        compartments = await compartment_manager.list_compartments(db)
        hooks = await compartment_manager.list_all_notification_hooks(db)

        # Build hook lookup: (compartment_id, qm_container_name or '') -> list[hook]
        hook_map: dict[tuple[str, str], list] = {}
        for h in hooks:
            key = (h.compartment_id, h.qm_container_name)
            hook_map.setdefault(key, []).append(h)

        loop = asyncio.get_event_loop()

        for comp in compartments:
            if not comp.containers:
                continue
            if not user_manager.user_exists(comp.id):
                continue

            statuses = await loop.run_in_executor(
                None,
                systemd_manager.get_service_status,
                comp.id,
                [SafeStr.of(c.qm_name, "container_name") for c in comp.containers],
            )

            for s in statuses:
                container_name = s["container"]
                state_key = f"{comp.id}/{container_name}"
                new_state = s.get("active_state", "")
                old_state = _last_states.get(state_key)

                if old_state is not None and old_state != new_state:
                    now_iso = datetime.datetime.now(datetime.UTC).isoformat() + "Z"

                    # Determine failure/restart event type
                    if new_state == "failed":
                        event_type = "on_failure"
                    elif new_state == "activating" and old_state in ("active", "failed"):
                        event_type = "on_restart"
                    else:
                        event_type = None

                    # Feature 17: start/stop event type
                    if new_state == "active" and old_state in ("activating", "inactive", "dead"):
                        start_stop_event = "on_start"
                    elif new_state in ("inactive", "dead") and old_state in (
                        "active",
                        "deactivating",
                    ):
                        start_stop_event = "on_stop"
                    else:
                        start_stop_event = None

                    for fired_event in filter(None, (event_type, start_stop_event)):
                        payload = {
                            "event": fired_event,
                            "compartment_id": comp.id,
                            "container_name": container_name,
                            "previous_state": old_state,
                            "state": new_state,
                            "timestamp": now_iso,
                        }
                        matching = hook_map.get((comp.id, container_name), []) + hook_map.get(
                            (comp.id, ""), []
                        )
                        for hook in matching:
                            if hook.event_type == fired_event and hook.enabled:
                                asyncio.create_task(
                                    fire_webhook(hook.webhook_url, hook.webhook_secret, payload)
                                )

                    # Feature 10: persist restart/failure counters
                    if event_type:
                        try:
                            if event_type == "on_failure":
                                await db.execute(
                                    insert(ContainerRestartStatsRow)
                                    .values(
                                        compartment_id=comp.id,
                                        container_name=container_name,
                                        restart_count=0,
                                        last_failure_at=now_iso,
                                    )
                                    .on_conflict_do_update(
                                        index_elements=["compartment_id", "container_name"],
                                        set_={"last_failure_at": now_iso},
                                    )
                                )
                            elif event_type == "on_restart":
                                await db.execute(
                                    insert(ContainerRestartStatsRow)
                                    .values(
                                        compartment_id=comp.id,
                                        container_name=container_name,
                                        restart_count=1,
                                        last_restart_at=now_iso,
                                    )
                                    .on_conflict_do_update(
                                        index_elements=["compartment_id", "container_name"],
                                        set_={
                                            "restart_count": ContainerRestartStatsRow.restart_count
                                            + 1,
                                            "last_restart_at": now_iso,
                                        },
                                    )
                                )
                            await db.commit()
                        except Exception as exc:
                            await db.rollback()
                            logger.warning("Failed to update restart stats: %s", exc)

                _last_states[state_key] = new_state

            # Record centralized-monitoring heartbeat (equivalent to agent heartbeat)
            try:
                now_hb = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                await db.execute(
                    update(CompartmentRow)
                    .where(CompartmentRow.id == comp.id)
                    .values(agent_last_seen=now_hb)
                )
                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.warning("Failed to update heartbeat for %s: %s", comp.id, exc)

        # Prune _last_states for deleted compartments/containers to prevent
        # unbounded memory growth over long uptimes.
        active_keys = {f"{comp.id}/{c.qm_name}" for comp in compartments for c in comp.containers}
        stale = [k for k in _last_states if k not in active_keys]
        for k in stale:
            del _last_states[k]


async def _start_event_stream(service_id: SafeSlug) -> asyncio.subprocess.Process | None:
    """Start a ``podman events`` stream for a compartment user.

    Returns the subprocess or None if the feature is unavailable.

    .. note::
        This helper is provided for future use.  The current poll-based
        ``monitor_loop`` remains the primary monitoring path.  A future
        iteration can replace the poll loop with an event-driven approach
        that reads JSON events from the returned process's stdout.
    """
    # podman events has been available since early Podman versions — no
    # version gate needed beyond the base QUADLET check.
    # TODO: Replace the poll loop in monitor_loop with this event stream
    # when the feature is proven stable across Podman versions.
    if not get_features().quadlet:
        return None

    uid = get_uid(service_id)
    proc = await asyncio.create_subprocess_exec(
        "sudo",
        "-u",
        f"qm-{service_id}",
        "/usr/bin/env",
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
        "podman",
        "events",
        "--stream",
        "--format=json",
        "--filter",
        "type=container",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return proc

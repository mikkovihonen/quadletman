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
import logging

import httpx

logger = logging.getLogger(__name__)

# In-memory: {compartment_id/container_name -> last known active_state}
_last_states: dict[str, str] = {}


# How often to poll systemd unit states (seconds)
_POLL_INTERVAL = 30

# How often to record metrics history samples (seconds)
_METRICS_INTERVAL = 300  # 5 minutes

# Retry configuration (module-level so tests can patch them)
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 2  # seconds; actual delays: 2s, 4s


async def fire_webhook(webhook_url: str, webhook_secret: str, payload: dict) -> None:
    """POST the payload to a webhook URL with exponential-backoff retry.

    Errors are logged but not raised. Up to _MAX_ATTEMPTS total attempts are made.
    """
    headers = {"Content-Type": "application/json"}
    if webhook_secret:
        headers["X-Webhook-Secret"] = webhook_secret

    for attempt in range(_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(webhook_url, json=payload, headers=headers, timeout=10)
                if resp.status_code < 400:
                    return  # success
                logger.warning(
                    "Webhook delivery to %s returned HTTP %s (attempt %d/%d)",
                    webhook_url,
                    resp.status_code,
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


async def monitor_loop(db_factory) -> None:
    """Continuously poll container states and trigger webhooks on transitions.

    db_factory is an async generator (same as FastAPI's get_db dependency).
    """
    # Brief startup delay to let the app fully initialise
    await asyncio.sleep(10)

    while True:
        try:
            await _check_once(db_factory)
        except Exception as exc:
            logger.warning("Notification monitor error: %s", exc)
        await asyncio.sleep(_POLL_INTERVAL)


async def metrics_loop(db_factory) -> None:
    """Periodically snapshot per-compartment metrics into metrics_history (Feature 9)."""
    from . import compartment_manager, metrics
    from .user_manager import get_uid

    await asyncio.sleep(15)  # brief startup delay

    while True:
        try:
            gen = db_factory()
            db = await gen.__anext__()
            try:
                compartments = await compartment_manager.list_compartments(db)
                loop = asyncio.get_event_loop()
                for comp in compartments:
                    try:
                        uid = await loop.run_in_executor(None, get_uid, comp.id)
                        m = await loop.run_in_executor(None, metrics.get_metrics, comp.id, uid)
                        await db.execute(
                            """INSERT INTO metrics_history
                               (compartment_id, cpu_percent, memory_bytes, disk_bytes)
                               VALUES (?, ?, ?, ?)""",
                            (comp.id, m["cpu_percent"], m["mem_bytes"], m["disk_bytes"]),
                        )
                    except Exception as exc:
                        logger.debug("Metrics snapshot failed for %s: %s", comp.id, exc)
                await db.commit()
            finally:
                with contextlib.suppress(StopAsyncIteration):
                    await gen.__anext__()
        except Exception as exc:
            logger.warning("Metrics loop error: %s", exc)
        await asyncio.sleep(_METRICS_INTERVAL)


async def process_monitor_loop(db_factory) -> None:
    """Periodically record running processes and fire webhooks for newly discovered ones.

    Each unique (process_name, cmdline) pair is upserted into the processes table.
    A webhook fires only when a process is seen for the very first time (is_new=True).
    The known flag is user-managed and never reset by the monitor.
    """
    from ..config import settings as _s
    from . import compartment_manager, metrics
    from .user_manager import get_uid

    await asyncio.sleep(20)  # brief startup delay

    while True:
        try:
            gen = db_factory()
            db = await gen.__anext__()
            try:
                compartments = await compartment_manager.list_compartments(db)
                hooks = await compartment_manager.list_all_notification_hooks(db)

                # {compartment_id -> [hook, ...]} for on_unexpected_process hooks
                alert_hooks: dict[str, list] = {}
                for h in hooks:
                    if h.event_type == "on_unexpected_process" and h.enabled:
                        alert_hooks.setdefault(h.compartment_id, []).append(h)

                if not any(comp.process_monitor_enabled for comp in compartments):
                    continue

                loop = asyncio.get_event_loop()
                for comp in compartments:
                    if not comp.process_monitor_enabled:
                        continue
                    with contextlib.suppress(Exception):
                        uid = await loop.run_in_executor(None, get_uid, comp.id)
                        procs = await loop.run_in_executor(None, metrics.get_processes, uid)

                        for proc in procs:
                            name = proc["name"]
                            cmdline = proc.get("cmdline", name)
                            process, is_new = await compartment_manager.upsert_process(
                                db, comp.id, name, cmdline
                            )
                            if is_new and not process.known:
                                now_iso = datetime.datetime.utcnow().isoformat() + "Z"
                                payload = {
                                    "event": "on_unexpected_process",
                                    "compartment_id": comp.id,
                                    "process_name": name,
                                    "cmdline": cmdline,
                                    "timestamp": now_iso,
                                }
                                for hook in alert_hooks.get(comp.id, []):
                                    asyncio.create_task(
                                        fire_webhook(hook.webhook_url, hook.webhook_secret, payload)
                                    )
            finally:
                with contextlib.suppress(StopAsyncIteration):
                    await gen.__anext__()
        except Exception as exc:
            logger.warning("Process monitor loop error: %s", exc)

        await asyncio.sleep(_s.process_monitor_interval)


async def connection_monitor_loop(db_factory) -> None:
    """Periodically record outbound container connections and fire webhooks for new ones.

    Each unique (container_name, proto, dst_ip, dst_port) tuple is upserted into the
    connections table. A webhook fires only when a connection is seen for the very first
    time (is_new=True). The known flag is user-managed and never reset by the monitor.
    Requires conntrack to be installed on the host; silently skips compartments where
    no container IPs are found or conntrack is unavailable.
    """
    from ..config import settings as _s
    from . import compartment_manager, metrics

    await asyncio.sleep(25)  # brief startup delay

    while True:
        try:
            gen = db_factory()
            db = await gen.__anext__()
            try:
                compartments = await compartment_manager.list_compartments(db)
                hooks = await compartment_manager.list_all_notification_hooks(db)

                # {compartment_id -> [hook, ...]} for on_unexpected_connection hooks
                alert_hooks: dict[str, list] = {}
                for h in hooks:
                    if h.event_type == "on_unexpected_connection" and h.enabled:
                        alert_hooks.setdefault(h.compartment_id, []).append(h)

                if not any(comp.connection_monitor_enabled for comp in compartments):
                    continue

                loop = asyncio.get_event_loop()
                for comp in compartments:
                    if not comp.connection_monitor_enabled:
                        continue
                    with contextlib.suppress(Exception):
                        conns = await loop.run_in_executor(None, metrics.get_connections, comp.id)
                        for conn in conns:
                            connection, is_new = await compartment_manager.upsert_connection(
                                db,
                                comp.id,
                                conn["container_name"],
                                conn["proto"],
                                conn["dst_ip"],
                                conn["dst_port"],
                            )
                            if is_new and not connection.known:
                                now_iso = datetime.datetime.utcnow().isoformat() + "Z"
                                payload = {
                                    "event": "on_unexpected_connection",
                                    "compartment_id": comp.id,
                                    "container_name": conn["container_name"],
                                    "proto": conn["proto"],
                                    "dst_ip": conn["dst_ip"],
                                    "dst_port": conn["dst_port"],
                                    "timestamp": now_iso,
                                }
                                for hook in alert_hooks.get(comp.id, []):
                                    if (
                                        hook.container_name
                                        and hook.container_name != conn["container_name"]
                                    ):
                                        continue
                                    asyncio.create_task(
                                        fire_webhook(hook.webhook_url, hook.webhook_secret, payload)
                                    )
            finally:
                with contextlib.suppress(StopAsyncIteration):
                    await gen.__anext__()
        except Exception as exc:
            logger.warning("Connection monitor loop error: %s", exc)

        await asyncio.sleep(_s.connection_monitor_interval)


async def _check_once(db_factory) -> None:
    from . import compartment_manager, systemd_manager

    # Keep the DB connection open so we can write restart stats inside the loop.
    gen = db_factory()
    db = await gen.__anext__()
    try:
        compartments = await compartment_manager.list_compartments(db)
        hooks = await compartment_manager.list_all_notification_hooks(db)

        # Build hook lookup: (compartment_id, container_name or '') -> list[hook]
        hook_map: dict[tuple[str, str], list] = {}
        for h in hooks:
            key = (h.compartment_id, h.container_name)
            hook_map.setdefault(key, []).append(h)

        loop = asyncio.get_event_loop()

        for comp in compartments:
            if not comp.containers:
                continue

            statuses = await loop.run_in_executor(
                None,
                systemd_manager.get_service_status,
                comp.id,
                [c.name for c in comp.containers],
            )

            for s in statuses:
                container_name = s["container"]
                state_key = f"{comp.id}/{container_name}"
                new_state = s.get("active_state", "")
                old_state = _last_states.get(state_key)

                if old_state is not None and old_state != new_state:
                    now_iso = datetime.datetime.utcnow().isoformat() + "Z"

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
                                    """INSERT INTO container_restart_stats
                                       (compartment_id, container_name, restart_count,
                                        last_failure_at)
                                       VALUES (?, ?, 0, ?)
                                       ON CONFLICT(compartment_id, container_name) DO UPDATE SET
                                       last_failure_at = excluded.last_failure_at""",
                                    (comp.id, container_name, now_iso),
                                )
                            elif event_type == "on_restart":
                                await db.execute(
                                    """INSERT INTO container_restart_stats
                                       (compartment_id, container_name, restart_count,
                                        last_restart_at)
                                       VALUES (?, ?, 1, ?)
                                       ON CONFLICT(compartment_id, container_name) DO UPDATE SET
                                       restart_count = restart_count + 1,
                                       last_restart_at = excluded.last_restart_at""",
                                    (comp.id, container_name, now_iso),
                                )
                            await db.commit()
                        except Exception as exc:
                            logger.warning("Failed to update restart stats: %s", exc)

                _last_states[state_key] = new_state
    finally:
        with contextlib.suppress(StopAsyncIteration):
            await gen.__anext__()

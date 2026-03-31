"""Internal Unix socket API for per-user monitoring agents.

The main quadletman app exposes a Unix socket that agents POST to.
This module handles incoming agent reports and writes them to the DB,
firing webhooks as appropriate.
"""

import asyncio
import contextlib
import datetime
import json
import logging
import os
import pwd
import socket
import struct

from sqlalchemy import insert, update

from quadletman.config.settings import settings
from quadletman.db.orm import CompartmentRow, ContainerRestartStatsRow, MetricsHistoryRow
from quadletman.models.sanitized import (
    SafeIpAddress,
    SafeMultilineStr,
    SafeResourceName,
    SafeSlug,
    SafeStr,
)
from quadletman.services import compartment_manager, notification_service

logger = logging.getLogger(__name__)

# In-memory dedup for image update webhooks fired from agent reports.
_notified_image_updates: dict[str, bool] = {}
_MAX_DEDUP_ENTRIES = settings.webhook_dedup_max_entries


async def _touch_agent_heartbeat(db, compartment_id: str) -> None:
    """Update the agent_last_seen timestamp for a compartment."""
    now_iso = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        await db.execute(
            update(CompartmentRow)
            .where(CompartmentRow.id == compartment_id)
            .values(agent_last_seen=now_iso)
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.warning("Failed to update agent heartbeat for %s: %s", compartment_id, exc)


async def handle_state_report(db, data: dict) -> None:
    """Handle container state transition report from an agent."""
    compartment_id = data["compartment_id"]
    transitions = data.get("transitions", [])

    if not transitions:
        return

    hooks = await compartment_manager.list_all_notification_hooks(db)
    hook_map: dict[tuple[str, str], list] = {}
    for h in hooks:
        key = (h.compartment_id, h.qm_container_name)
        hook_map.setdefault(key, []).append(h)

    for t in transitions:
        container_name = t["container"]
        old_state = t.get("previous_state", "")
        new_state = t["state"]
        now_iso = datetime.datetime.now(datetime.UTC).isoformat() + "Z"

        # Determine event types
        events = []
        if new_state == "failed":
            events.append("on_failure")
        elif new_state == "activating" and old_state in ("active", "failed"):
            events.append("on_restart")
        if new_state == "active" and old_state in ("activating", "inactive", "dead"):
            events.append("on_start")
        elif new_state in ("inactive", "dead") and old_state in ("active", "deactivating"):
            events.append("on_stop")

        for event_type in events:
            payload = {
                "event": event_type,
                "compartment_id": compartment_id,
                "container_name": container_name,
                "previous_state": old_state,
                "state": new_state,
                "timestamp": now_iso,
            }
            matching = hook_map.get((compartment_id, container_name), []) + hook_map.get(
                (compartment_id, ""), []
            )
            for hook in matching:
                if hook.event_type == event_type and hook.enabled:
                    asyncio.create_task(
                        notification_service.fire_webhook(
                            hook.webhook_url, hook.webhook_secret, payload
                        )
                    )

            # Persist restart/failure counters
            if event_type in ("on_failure", "on_restart"):
                try:
                    if event_type == "on_failure":
                        await db.execute(
                            insert(ContainerRestartStatsRow)
                            .values(
                                compartment_id=compartment_id,
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
                                compartment_id=compartment_id,
                                container_name=container_name,
                                restart_count=1,
                                last_restart_at=now_iso,
                            )
                            .on_conflict_do_update(
                                index_elements=["compartment_id", "container_name"],
                                set_={
                                    "restart_count": ContainerRestartStatsRow.restart_count + 1,
                                    "last_restart_at": now_iso,
                                },
                            )
                        )
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    logger.warning("Failed to update restart stats: %s", exc)


async def handle_metrics_report(db, data: dict) -> None:
    """Handle metrics snapshot report from an agent."""
    try:
        await db.execute(
            insert(MetricsHistoryRow).values(
                compartment_id=data["compartment_id"],
                cpu_percent=data.get("cpu_percent", 0.0),
                memory_bytes=data.get("mem_bytes", 0),
                disk_bytes=data.get("disk_bytes", 0),
            )
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.warning("Metrics insert failed for %s: %s", data.get("compartment_id"), exc)


async def handle_processes_report(db, data: dict) -> None:
    """Handle process discovery report from an agent."""
    compartment_id = SafeSlug.of(data["compartment_id"], "agent:compartment_id")
    processes = data.get("processes", [])

    hooks = await compartment_manager.list_all_notification_hooks(db)
    alert_hooks: list = []
    for h in hooks:
        if (
            h.compartment_id == compartment_id
            and h.event_type == "on_unexpected_process"
            and h.enabled
        ):
            alert_hooks.append(h)

    errors = 0
    for proc in processes:
        name = SafeStr.of(proc.get("name", ""), "agent:process_name")
        cmdline = SafeMultilineStr.of(proc.get("cmdline", name), "agent:cmdline")
        try:
            process, is_new = await compartment_manager.upsert_process(
                db, compartment_id, name, cmdline
            )
            if is_new and not process.known:
                now_iso = datetime.datetime.now(datetime.UTC).isoformat() + "Z"
                payload = {
                    "event": "on_unexpected_process",
                    "compartment_id": compartment_id,
                    "process_name": name,
                    "cmdline": cmdline,
                    "timestamp": now_iso,
                }
                for hook in alert_hooks:
                    asyncio.create_task(
                        notification_service.fire_webhook(
                            hook.webhook_url, hook.webhook_secret, payload
                        )
                    )
        except Exception as exc:
            await db.rollback()
            errors += 1
            logger.warning("Process upsert failed for %s/%s: %s", compartment_id, name, exc)
    if errors:
        logger.warning(
            "Process report for %s: %d/%d upserts failed", compartment_id, errors, len(processes)
        )


async def handle_connections_report(db, data: dict) -> None:
    """Handle connection discovery report from an agent.

    Upserts each connection, checks allowlist rules, fires webhooks for new
    non-allowlisted connections, and runs retention cleanup.
    """
    compartment_id = SafeSlug.of(data["compartment_id"], "agent:compartment_id")
    connections = data.get("connections", [])
    if not connections:
        return

    rules = await compartment_manager.list_allowlist_rules(db, compartment_id)
    hooks = await compartment_manager.list_all_notification_hooks(db)
    alert_hooks: dict[str, list] = {}
    for h in hooks:
        if (
            h.compartment_id == compartment_id
            and h.event_type == "on_unexpected_connection"
            and h.enabled
        ):
            alert_hooks.setdefault(h.compartment_id, []).append(h)

    errors = 0
    for conn in connections:
        container_name = conn.get("container_name", "")
        proto = conn.get("proto", "tcp")
        dst_ip = conn.get("dst_ip", "")
        dst_port = conn.get("dst_port", 0)
        direction = conn.get("direction", "outbound")
        try:
            _connection, is_new = await compartment_manager.upsert_connection(
                db,
                compartment_id,
                SafeResourceName.of(container_name, "agent:container_name"),
                SafeStr.of(proto, "agent:proto"),
                SafeIpAddress.of(dst_ip, "agent:dst_ip"),
                dst_port,
                SafeStr.of(direction, "agent:direction"),
            )
            if is_new and not compartment_manager.connection_is_allowlisted(
                rules,
                proto,
                SafeIpAddress.of(dst_ip, "agent:dst_ip"),
                dst_port,
                SafeResourceName.of(container_name, "agent:container_name"),
                direction,
            ):
                now_iso = datetime.datetime.now(datetime.UTC).isoformat() + "Z"
                payload = {
                    "event": "on_unexpected_connection",
                    "compartment_id": compartment_id,
                    "container_name": container_name,
                    "proto": proto,
                    "dst_ip": dst_ip,
                    "dst_port": dst_port,
                    "direction": direction,
                    "timestamp": now_iso,
                }
                for hook in alert_hooks.get(compartment_id, []):
                    if hook.qm_container_name and hook.qm_container_name != container_name:
                        continue
                    asyncio.create_task(
                        notification_service.fire_webhook(
                            hook.webhook_url, hook.webhook_secret, payload
                        )
                    )
        except Exception as exc:
            await db.rollback()
            errors += 1
            logger.warning(
                "Connection upsert failed for %s/%s: %s", compartment_id, container_name, exc
            )
    if errors:
        logger.warning(
            "Connection report for %s: %d/%d upserts failed",
            compartment_id,
            errors,
            len(connections),
        )

    # Apply per-compartment history retention policy
    try:
        await compartment_manager.cleanup_stale_connections(db)
    except Exception as exc:
        await db.rollback()
        logger.warning("Connection cleanup failed: %s", exc)


async def handle_image_updates_report(db, data: dict) -> None:
    """Handle image update check report from an agent.

    Fires ``on_image_update`` webhooks for newly detected pending updates.
    Deduplicates across calls so the same (compartment, container, image)
    tuple only triggers one webhook.
    """
    compartment_id = SafeSlug.of(data["compartment_id"], "agent:compartment_id")
    updates = data.get("updates", [])
    if not updates:
        return

    hooks = await compartment_manager.list_all_notification_hooks(db)
    alert_hooks: list = []
    for h in hooks:
        if h.compartment_id == compartment_id and h.event_type == "on_image_update" and h.enabled:
            alert_hooks.append(h)

    if not alert_hooks:
        return

    still_pending: set[str] = set()
    for upd in updates:
        unit = upd.get("Unit", "")
        container_name = unit.removesuffix(".service")
        image = upd.get("Image", "")
        policy = upd.get("Policy", "")
        dedup_key = f"{compartment_id}/{container_name}/{image}"
        still_pending.add(dedup_key)

        if dedup_key in _notified_image_updates:
            continue

        if len(_notified_image_updates) >= _MAX_DEDUP_ENTRIES:
            _notified_image_updates.clear()

        _notified_image_updates[dedup_key] = True
        now_iso = datetime.datetime.now(datetime.UTC).isoformat() + "Z"
        payload = {
            "event": "on_image_update",
            "compartment_id": compartment_id,
            "container_name": container_name,
            "image": image,
            "policy": policy,
            "timestamp": now_iso,
        }
        for hook in alert_hooks:
            if hook.qm_container_name and hook.qm_container_name != container_name:
                continue
            asyncio.create_task(
                notification_service.fire_webhook(hook.webhook_url, hook.webhook_secret, payload)
            )

    # Clean up stale dedup entries for this compartment
    prefix = f"{compartment_id}/"
    stale = [k for k in _notified_image_updates if k.startswith(prefix) and k not in still_pending]
    for k in stale:
        del _notified_image_updates[k]


def _get_peer_uid(conn: socket.socket) -> int | None:
    """Get the UID of the peer process via SO_PEERCRED."""
    try:
        cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII"))
        _pid, uid, _gid = struct.unpack("iII", cred)
        return uid
    except Exception:
        return None


def _uid_to_compartment_id(uid: int) -> str | None:
    """Derive compartment ID from a qm-* user's UID.

    Returns the compartment ID (the part after the service_user_prefix) if the
    UID belongs to a qm-* user, or None otherwise.  Also accepts the app's own
    UID (for self-connections during testing/health checks).
    """
    if uid == os.getuid():
        return None  # App's own UID — allow but don't restrict compartment
    try:
        pw = pwd.getpwuid(uid)
    except KeyError:
        return None
    prefix = str(settings.service_user_prefix)
    if not pw.pw_name.startswith(prefix):
        return None
    # qm-{compartment_id} or qm-{compartment_id}-{helper_uid}
    suffix = pw.pw_name[len(prefix) :]
    # Strip helper user suffix (e.g. "myapp-1000" → "myapp")
    parts = suffix.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return suffix


async def _handle_connection(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, db_factory
) -> None:
    """Handle a single agent connection on the Unix socket."""
    try:
        async with asyncio.timeout(settings.agent_request_timeout):
            # Authenticate peer via SO_PEERCRED
            sock = writer.get_extra_info("socket")
            peer_uid = _get_peer_uid(sock) if sock else None
            if peer_uid is None:
                logger.warning("Agent API: rejected connection — could not determine peer UID")
                writer.write(b"HTTP/1.0 403 Forbidden\r\n\r\n")
                return

            allowed_compartment = _uid_to_compartment_id(peer_uid)
            # If allowed_compartment is None and uid != our own, reject
            if allowed_compartment is None and peer_uid != os.getuid():
                logger.warning("Agent API: rejected connection from non-agent UID %d", peer_uid)
                writer.write(b"HTTP/1.0 403 Forbidden\r\n\r\n")
                return

            # Read the HTTP-like request
            raw = await asyncio.wait_for(reader.read(65536), timeout=30)
            text = raw.decode("utf-8", errors="replace")

            # Parse minimal HTTP/1.0 request
            header_end = text.find("\r\n\r\n")
            if header_end < 0:
                writer.write(b"HTTP/1.0 400 Bad Request\r\n\r\n")
                return

            header_part = text[:header_end]
            body = text[header_end + 4 :]
            request_line = header_part.split("\r\n", 1)[0]
            parts = request_line.split(" ", 2)
            if len(parts) < 2:
                writer.write(b"HTTP/1.0 400 Bad Request\r\n\r\n")
                return

            method, path = parts[0], parts[1]
            if method != "POST":
                writer.write(b"HTTP/1.0 405 Method Not Allowed\r\n\r\n")
                return

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                writer.write(b"HTTP/1.0 400 Bad Request\r\n\r\n")
                return

            # Enforce compartment_id matches the authenticated peer
            if allowed_compartment is not None:
                reported_id = data.get("compartment_id", "")
                if reported_id != allowed_compartment:
                    logger.warning(
                        "Agent API: UID %d (compartment %s) tried to report for compartment %s",
                        peer_uid,
                        allowed_compartment,
                        reported_id,
                    )
                    writer.write(b"HTTP/1.0 403 Forbidden\r\n\r\n")
                    return

            # Dispatch to handler
            gen = db_factory()
            db = await gen.__anext__()
            try:
                if path == "/agent/state":
                    await handle_state_report(db, data)
                elif path == "/agent/metrics":
                    await handle_metrics_report(db, data)
                elif path == "/agent/processes":
                    await handle_processes_report(db, data)
                elif path == "/agent/connections":
                    await handle_connections_report(db, data)
                elif path == "/agent/image-updates":
                    await handle_image_updates_report(db, data)
                else:
                    writer.write(b"HTTP/1.0 404 Not Found\r\n\r\n")
                    return

                # Record agent heartbeat on every successful report
                compartment_id = data.get("compartment_id")
                if compartment_id:
                    await _touch_agent_heartbeat(db, compartment_id)
                    logger.info("Agent report %s from %s", path, compartment_id)
            finally:
                with contextlib.suppress(StopAsyncIteration):
                    await gen.__anext__()

            writer.write(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{"ok":true}')
    except TimeoutError:
        logger.warning("Agent API request timed out")
        writer.write(b"HTTP/1.0 408 Request Timeout\r\n\r\n")
    except Exception as exc:
        logger.warning("Agent API error: %s", exc)
        writer.write(b"HTTP/1.0 500 Internal Server Error\r\n\r\n")
    finally:
        writer.close()
        async with asyncio.timeout(5):
            await writer.wait_closed()


async def start_agent_api(sock_path: str, db_factory) -> asyncio.Server | None:
    """Start the agent API Unix socket server.

    Returns the asyncio Server object, or None if the socket could not be created.
    """
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)

    # Remove stale socket file
    with contextlib.suppress(FileNotFoundError):
        os.unlink(sock_path)

    async def client_handler(reader, writer):
        await _handle_connection(reader, writer, db_factory)

    server = await asyncio.start_unix_server(client_handler, path=sock_path)

    # Set socket permissions — group-writable only (UID-based access control
    # in _handle_connection enforces that only qm-* users are accepted)
    # codeql[py/overly-permissive-file] intentional — group-write for agent socket access
    os.chmod(sock_path, 0o660)

    logger.info("Agent API listening on %s", sock_path)
    return server

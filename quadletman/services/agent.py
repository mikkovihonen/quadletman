"""Per-compartment monitoring agent — runs as a systemd --user service for each qm-* user.

Each agent monitors its own containers, processes, and resource usage natively
(no sudo needed) and reports data to the main quadletman app via a Unix socket API.

Usage::

    quadletman-agent --api-socket /run/quadletman/agent.sock

The agent is deployed as a .service unit file by quadlet_writer and auto-managed
by systemd (restart on failure, persistent via loginctl linger).
"""

import argparse
import contextlib
import json
import logging
import os
import socket
import subprocess
import time

import psutil

from quadletman.services.metrics import parse_proc_net_tcp

logger = logging.getLogger("quadletman.agent")

# Poll intervals (seconds)
_STATE_INTERVAL = 30
_METRICS_INTERVAL = 300
_PROCESS_INTERVAL = 60
_CONNECTION_INTERVAL = 60
_IMAGE_UPDATE_INTERVAL = 21600  # 6 hours

# Path to volumes base for disk usage calculation
_VOLUMES_BASE = "/var/lib/quadletman/volumes"


def _get_uid() -> int:
    return os.getuid()


def _get_compartment_id() -> str:
    """Derive compartment ID from the current username (qm-{id})."""
    cid = os.environ.get("QUADLETMAN_COMPARTMENT_ID")
    if cid:
        return cid
    import pwd

    username = pwd.getpwuid(os.getuid()).pw_name
    if username.startswith("qm-"):
        return username[3:]
    raise RuntimeError(f"Cannot derive compartment ID from username {username!r}")


def _post_to_api(sock_path: str, endpoint: str, data: dict) -> bool:
    """POST JSON data to the main app's agent API via Unix socket.

    Returns True on success, False on error.
    """
    try:
        body = json.dumps(data).encode("utf-8")
        request = (
            f"POST {endpoint} HTTP/1.0\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"\r\n"
        ).encode() + body

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect(sock_path)
            s.sendall(request)
            # Read response (we just check status)
            resp = s.recv(4096).decode("utf-8", errors="replace")
            ok = "200" in resp.split("\r\n", 1)[0]
            if ok:
                logger.info("Posted %s report", endpoint.rsplit("/", 1)[-1])
            else:
                logger.warning("Report %s rejected: %s", endpoint, resp.split("\r\n", 1)[0])
            return ok
    except Exception as exc:
        logger.warning("Failed to post to %s: %s", endpoint, exc)
        return False


def _get_container_units() -> list[str]:
    """List container unit names managed by systemd --user."""
    try:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "list-units",
                "--type=service",
                "--state=loaded",
                "--no-legend",
                "--no-pager",
                "--plain",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        units = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts and parts[0].endswith(".service"):
                units.append(parts[0])
        return units
    except Exception as exc:
        logger.warning("Could not list units: %s", exc)
        return []


def _get_unit_states(units: list[str]) -> list[dict]:
    """Get active_state for each unit."""
    states = []
    for unit in units:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "show", unit, "--property=ActiveState,SubState,LoadState"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            props = {}
            for line in result.stdout.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    props[k] = v
            # Strip .service suffix for container name
            container = unit.removesuffix(".service")
            states.append(
                {
                    "container": container,
                    "active_state": props.get("ActiveState", "unknown"),
                    "sub_state": props.get("SubState", ""),
                    "load_state": props.get("LoadState", ""),
                }
            )
        except Exception as exc:
            logger.warning("Could not get state for %s: %s", unit, exc)
    return states


def _get_metrics(compartment_id: str, uid: int) -> dict:
    """Collect CPU, memory, disk metrics for own UID."""
    cpu_percent = 0.0
    mem_bytes = 0
    proc_count = 0

    for proc in psutil.process_iter(["uids", "cpu_percent", "memory_info"]):
        try:
            info = proc.info
            if info["uids"] and info["uids"].real == uid:
                cpu_percent += info["cpu_percent"] or 0.0
                if info["memory_info"]:
                    mem_bytes += info["memory_info"].rss
                proc_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    disk_bytes = 0
    vol_path = os.path.join(_VOLUMES_BASE, compartment_id)
    if os.path.isdir(vol_path):
        for dirpath, _dirnames, filenames in os.walk(vol_path):
            for f in filenames:
                with contextlib.suppress(OSError):
                    disk_bytes += os.path.getsize(os.path.join(dirpath, f))

    return {
        "cpu_percent": round(cpu_percent, 2),
        "mem_bytes": mem_bytes,
        "proc_count": proc_count,
        "disk_bytes": disk_bytes,
    }


def _get_processes(uid: int) -> list[dict]:
    """Get running processes for own UID."""
    procs = []
    for proc in psutil.process_iter(["pid", "uids", "name", "cmdline"]):
        try:
            info = proc.info
            if info["uids"] and info["uids"].real == uid:
                procs.append(
                    {
                        "pid": info["pid"],
                        "name": info["name"] or "",
                        "cmdline": " ".join(info["cmdline"] or []) or (info["name"] or ""),
                    }
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return procs


def _get_container_ips() -> dict[str, str]:
    """Get {ip: container_name} map using podman inspect."""
    ip_map: dict[str, str] = {}
    try:
        result = subprocess.run(
            ["podman", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ip_map
        containers = json.loads(result.stdout)
        names = [c.get("Names", [c.get("Id", "")])[0] for c in containers]
        if not names:
            return ip_map
        inspect = subprocess.run(
            ["podman", "container", "inspect"] + names,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if inspect.returncode != 0:
            return ip_map
        for c in json.loads(inspect.stdout or "[]"):
            name = c.get("Name", "").lstrip("/")
            networks = c.get("NetworkSettings", {}).get("Networks", {})
            for net_info in networks.values():
                ip = net_info.get("IPAddress", "")
                if ip:
                    ip_map[ip] = name
    except Exception as exc:
        logger.warning("Could not get container IPs: %s", exc)
    return ip_map


def _get_container_pids() -> dict[str, int]:
    """Get {container_name: pid} map for running containers via podman inspect."""
    pid_map: dict[str, int] = {}
    try:
        result = subprocess.run(
            ["podman", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return pid_map
        containers = json.loads(result.stdout)
        names = [c.get("Names", [c.get("Id", "")])[0] for c in containers]
        if not names:
            return pid_map
        inspect = subprocess.run(
            ["podman", "container", "inspect"] + names,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if inspect.returncode != 0:
            return pid_map
        for c in json.loads(inspect.stdout or "[]"):
            name = c.get("Name", "").lstrip("/")
            pid = c.get("State", {}).get("Pid", 0)
            if name and pid and pid > 0:
                pid_map[name] = pid
    except Exception as exc:
        logger.warning("Could not get container PIDs: %s", exc)
    return pid_map


def _get_connections() -> list[dict]:
    """Collect active TCP connections for all running containers.

    Reads ``/proc/<pid>/net/tcp`` for each container's init process. The file
    is readable by the owning UID (the qm-* user running this agent) and
    reflects the container's network namespace — no sudo needed.

    Also builds an IP→container map to classify connections as inbound/outbound
    and to label each connection with its container name.
    """
    pid_map = _get_container_pids()
    if not pid_map:
        logger.info("No container PIDs found — skipping connection scan")
    else:
        logger.info("Scanning connections for containers: %s", list(pid_map.keys()))

    connections: list[dict] = []
    seen: set[tuple] = set()  # deduplicate

    for container_name, pid in pid_map.items():
        # Collect listening ports across both IPv4 and IPv6 for direction classification
        all_listen_ports: set[int] = set()
        all_established: list[tuple[str, int, str, int]] = []
        for tcp_path in (f"/proc/{pid}/net/tcp", f"/proc/{pid}/net/tcp6"):
            established, listen_ports = parse_proc_net_tcp(tcp_path, include_time_wait=True)
            all_listen_ports.update(listen_ports)
            all_established.extend(established)

        logger.info(
            "%s (pid=%d): %d established/tw, %d listen ports",
            container_name,
            pid,
            len(all_established),
            len(all_listen_ports),
        )

        for _local_ip, local_port, remote_ip, remote_port in all_established:
            if remote_ip.startswith("127.") or remote_ip == "0.0.0.0":
                continue

            # Direction: if local port is a listening port → inbound
            direction = "inbound" if local_port in all_listen_ports else "outbound"

            dedup_key = (container_name, "tcp", remote_ip, remote_port, direction)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            connections.append(
                {
                    "container_name": container_name,
                    "proto": "tcp",
                    "dst_ip": remote_ip,
                    "dst_port": remote_port,
                    "direction": direction,
                }
            )

    return connections


def _check_image_updates() -> list[dict]:
    """Run ``podman auto-update --dry-run --format=json`` to detect pending updates."""
    try:
        result = subprocess.run(
            ["podman", "auto-update", "--dry-run", "--format=json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Image update dry-run failed: %s", exc)
        return []


def main():
    parser = argparse.ArgumentParser(description="quadletman per-user monitoring agent")
    parser.add_argument("--api-socket", required=True, help="Path to the agent API Unix socket")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    sock_path = args.api_socket
    compartment_id = _get_compartment_id()
    uid = _get_uid()

    logger.info("Agent starting for compartment %s (uid=%d)", compartment_id, uid)

    # Track last known states for transition detection
    last_states: dict[str, str] = {}

    last_state_check = 0.0
    last_metrics_check = 0.0
    last_process_check = 0.0
    last_connection_check = 0.0
    last_image_update_check = 0.0

    while True:
        now = time.monotonic()

        # --- State monitoring ---
        if now - last_state_check >= _STATE_INTERVAL:
            last_state_check = now
            try:
                units = _get_container_units()
                states = _get_unit_states(units)

                # Detect transitions and report
                transitions = []
                for s in states:
                    key = s["container"]
                    new_state = s["active_state"]
                    old_state = last_states.get(key)
                    if old_state is not None and old_state != new_state:
                        transitions.append(
                            {
                                "container": key,
                                "previous_state": old_state,
                                "state": new_state,
                            }
                        )
                    last_states[key] = new_state

                if transitions:
                    _post_to_api(
                        sock_path,
                        "/agent/state",
                        {
                            "compartment_id": compartment_id,
                            "transitions": transitions,
                        },
                    )
            except Exception as exc:
                logger.warning("State check failed: %s", exc)

        # --- Metrics ---
        if now - last_metrics_check >= _METRICS_INTERVAL:
            last_metrics_check = now
            try:
                m = _get_metrics(compartment_id, uid)
                _post_to_api(
                    sock_path,
                    "/agent/metrics",
                    {
                        "compartment_id": compartment_id,
                        **m,
                    },
                )
            except Exception as exc:
                logger.warning("Metrics collection failed: %s", exc)

        # --- Process monitoring ---
        if now - last_process_check >= _PROCESS_INTERVAL:
            last_process_check = now
            try:
                procs = _get_processes(uid)
                _post_to_api(
                    sock_path,
                    "/agent/processes",
                    {
                        "compartment_id": compartment_id,
                        "processes": procs,
                    },
                )
            except Exception as exc:
                logger.warning("Process monitoring failed: %s", exc)

        # --- Connection monitoring ---
        if now - last_connection_check >= _CONNECTION_INTERVAL:
            last_connection_check = now
            try:
                conns = _get_connections()
                logger.info("Connection check: %d active connections", len(conns))
                if conns:
                    _post_to_api(
                        sock_path,
                        "/agent/connections",
                        {
                            "compartment_id": compartment_id,
                            "connections": conns,
                        },
                    )
            except Exception as exc:
                logger.warning("Connection monitoring failed: %s", exc)

        # --- Image update monitoring ---
        if now - last_image_update_check >= _IMAGE_UPDATE_INTERVAL:
            last_image_update_check = now
            try:
                updates = _check_image_updates()
                pending = [u for u in updates if u.get("Updated") == "pending"]
                if pending:
                    _post_to_api(
                        sock_path,
                        "/agent/image-updates",
                        {
                            "compartment_id": compartment_id,
                            "updates": pending,
                        },
                    )
            except Exception as exc:
                logger.warning("Image update check failed: %s", exc)

        time.sleep(5)  # main loop tick


if __name__ == "__main__":
    main()

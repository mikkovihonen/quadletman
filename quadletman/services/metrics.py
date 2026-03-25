"""Per-service resource usage metrics using psutil and disk walks."""

import json
import logging
import os
import subprocess

import psutil

from ..models import sanitized
from ..models.sanitized import SafeIpAddress, SafeMultilineStr, SafeSlug, SafeStr
from ..utils import dir_size, dir_size_excluding

logger = logging.getLogger(__name__)

_VOLUMES_BASE = "/var/lib/quadletman/volumes"


@sanitized.enforce
def get_processes(uid: int) -> list[dict]:
    """Return process list for a service user UID."""
    procs = []
    for proc in psutil.process_iter(
        ["pid", "uids", "name", "cmdline", "cpu_percent", "memory_info", "status"]
    ):
        try:
            info = proc.info
            if info["uids"] and info["uids"].real == uid:
                raw_name = info["name"] or ""
                raw_cmdline = " ".join(info["cmdline"] or []) or raw_name
                raw_status = info["status"] or ""
                procs.append(
                    {
                        "pid": info["pid"],
                        "name": SafeStr.of(raw_name, "psutil:name"),
                        "cmdline": SafeMultilineStr.of(raw_cmdline, "psutil:cmdline"),
                        "cpu_percent": round(info["cpu_percent"] or 0.0, 1),
                        "mem_bytes": info["memory_info"].rss if info["memory_info"] else 0,
                        "status": SafeStr.of(raw_status, "psutil:status"),
                    }
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return sorted(procs, key=lambda p: p["pid"])


@sanitized.enforce
def _podman_cmd(service_id: SafeSlug) -> list[str]:
    from .user_manager import _username, get_uid

    username = _username(service_id)
    uid = get_uid(service_id)
    return [
        "sudo",
        "-u",
        username,
        "env",
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
        "podman",
    ]


@sanitized.enforce
def get_disk_breakdown(service_id: SafeSlug) -> dict:
    """Return disk usage broken down by images, container overlays, managed volumes, and service config."""
    images: list[dict] = []
    overlays: list[dict] = []
    volumes_bytes = dir_size(os.path.join(_VOLUMES_BASE, service_id))

    base = _podman_cmd(service_id)

    # --- Images ---
    try:
        result = subprocess.run(
            base + ["images", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="/",
        )
        if result.returncode == 0:
            for img in json.loads(result.stdout or "[]"):
                name = (img.get("Names") or [img.get("Id", "")[:12]])[0]
                size = img.get("Size", 0)
                images.append({"name": name, "bytes": size})
    except Exception as exc:
        logger.warning("Could not get image sizes for %s: %s", service_id, exc)

    # --- Container overlays (writable layers) ---
    try:
        result = subprocess.run(
            base + ["ps", "-a", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="/",
        )
        if result.returncode == 0:
            containers = json.loads(result.stdout or "[]")
            names = [c.get("Names", [c.get("Id", "")])[0] for c in containers]
            if names:
                inspect = subprocess.run(
                    base + ["container", "inspect", "--size"] + names,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    cwd="/",
                )
                if inspect.returncode == 0:
                    for c in json.loads(inspect.stdout or "[]"):
                        rw = c.get("SizeRw") or 0
                        name = c.get("Name", "").lstrip("/")
                        if rw > 0:
                            overlays.append({"name": name, "bytes": rw})
    except Exception as exc:
        logger.warning("Could not get overlay sizes for %s: %s", service_id, exc)

    # --- Volumes per named volume ---
    vol_base = os.path.join(_VOLUMES_BASE, service_id)
    volume_details: list[dict] = []
    try:
        for entry in os.scandir(vol_base):
            if entry.is_dir(follow_symlinks=False):
                volume_details.append({"name": entry.name, "bytes": dir_size(entry.path)})
    except OSError:
        pass

    # --- Service config (home dir excluding container storage) ---
    config_bytes = 0
    try:
        from .user_manager import get_home

        home = get_home(service_id)
        storage_dir = os.path.join(home, ".local", "share", "containers", "storage")
        config_bytes = dir_size_excluding(home, storage_dir)
    except Exception as exc:
        logger.warning("Could not get config size for %s: %s", service_id, exc)

    return {
        "images": sorted(images, key=lambda x: x["bytes"], reverse=True),
        "overlays": sorted(overlays, key=lambda x: x["bytes"], reverse=True),
        "volumes": sorted(volume_details, key=lambda x: x["bytes"], reverse=True),
        "volumes_total": volumes_bytes,
        "config_bytes": config_bytes,
    }


@sanitized.enforce
def get_container_ips(service_id: SafeSlug) -> dict[str, str]:
    """Return a mapping of {ip: container_name} for all running containers in a compartment.

    Uses `podman inspect` on all running containers to extract their bridge network IPs.
    Returns an empty dict if podman is unavailable or no containers are running.
    """
    base = _podman_cmd(service_id)
    ip_map: dict[str, str] = {}
    try:
        result = subprocess.run(
            base + ["ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="/",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ip_map
        containers = json.loads(result.stdout)
        names = [c.get("Names", [c.get("Id", "")])[0] for c in containers]
        if not names:
            return ip_map
        inspect = subprocess.run(
            base + ["container", "inspect"] + names,
            capture_output=True,
            text=True,
            timeout=15,
            cwd="/",
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
        logger.debug("Could not get container IPs for %s: %s", service_id, exc)
    return ip_map


# ---------------------------------------------------------------------------
# /proc/net/tcp parsing — used by both root-mode (this module) and non-root
# mode (agent.py imports these functions)
# ---------------------------------------------------------------------------


def parse_hex_addr(hex_addr: str) -> tuple[str, int]:
    """Parse a hex-encoded address from ``/proc/net/tcp``.

    Format: ``0100007F:0050`` → ``('127.0.0.1', 80)``.
    IP is 32-bit little-endian hex, port is 16-bit hex.
    """
    ip_hex, port_hex = hex_addr.split(":")
    port = int(port_hex, 16)
    ip_int = int(ip_hex, 16)
    ip = f"{ip_int & 0xFF}.{(ip_int >> 8) & 0xFF}.{(ip_int >> 16) & 0xFF}.{(ip_int >> 24) & 0xFF}"
    return ip, port


def parse_proc_net_tcp(
    path: str, *, include_time_wait: bool = True
) -> tuple[list[tuple[str, int, str, int]], set[int]]:
    """Parse ``/proc/<pid>/net/tcp`` and return connections + listening ports.

    Returns ``(connections, listen_ports)`` where:
    - ``connections`` is a list of ``(local_ip, local_port, remote_ip, remote_port)``
      tuples for ESTABLISHED (state ``01``) and TIME_WAIT (state ``06``) connections.
    - ``listen_ports`` is a set of local port numbers in LISTEN state (state ``0A``),
      used to classify direction: if a connection's local port is in
      ``listen_ports``, it's inbound (a client connected to us); otherwise outbound.

    TIME_WAIT connections linger ~60s after close, making short-lived connections
    (e.g. curl) visible to the polling-based monitor.
    """
    accepted_states = {1}  # ESTABLISHED
    if include_time_wait:
        accepted_states.add(6)  # TIME_WAIT
    connections = []
    listen_ports: set[int] = set()
    try:
        with open(path) as f:
            next(f)  # skip header line
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                state = int(parts[3], 16)
                if state in accepted_states:
                    local_ip, local_port = parse_hex_addr(parts[1])
                    remote_ip, remote_port = parse_hex_addr(parts[2])
                    connections.append((local_ip, local_port, remote_ip, remote_port))
                elif state == 0xA:  # LISTEN
                    _listen_ip, listen_port = parse_hex_addr(parts[1])
                    listen_ports.add(listen_port)
    except (FileNotFoundError, PermissionError, StopIteration):
        pass
    return connections, listen_ports


@sanitized.enforce
def _get_container_pids(service_id: SafeSlug) -> dict[str, int]:
    """Return ``{container_name: pid}`` for running containers in a compartment.

    Uses ``podman inspect`` via the compartment user (``sudo -u qm-{id}``).
    """
    base = _podman_cmd(service_id)
    pid_map: dict[str, int] = {}
    try:
        result = subprocess.run(
            base + ["ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="/",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return pid_map
        containers = json.loads(result.stdout)
        names = [c.get("Names", [c.get("Id", "")])[0] for c in containers]
        if not names:
            return pid_map
        inspect = subprocess.run(
            base + ["container", "inspect"] + names,
            capture_output=True,
            text=True,
            timeout=15,
            cwd="/",
        )
        if inspect.returncode != 0:
            return pid_map
        for c in json.loads(inspect.stdout or "[]"):
            name = c.get("Name", "").lstrip("/")
            pid = c.get("State", {}).get("Pid", 0)
            if name and pid and pid > 0:
                pid_map[name] = pid
    except Exception as exc:
        logger.debug("Could not get container PIDs for %s: %s", service_id, exc)
    return pid_map


@sanitized.enforce
def get_connections(service_id: SafeSlug) -> list[dict]:
    """Return active TCP connections for all running containers in a compartment.

    Reads ``/proc/<pid>/net/tcp`` for each container's init process to discover
    ESTABLISHED connections (and optionally TIME_WAIT when
    ``QUADLETMAN_CAPTURE_TIME_WAIT=true``).  This works for rootless Podman
    (pasta/slirp4netns) because it reads the kernel's TCP socket table directly
    from the container's network namespace.

    The root process can read ``/proc/<pid>/net/tcp`` for any process without
    restrictions.

    Returns a list of dicts: container_name, proto, dst_ip, dst_port, direction.
    """
    ip_map = get_container_ips(service_id)
    pid_map = _get_container_pids(service_id)
    if not pid_map:
        return []

    # Reverse IP map: {container_name: set_of_ips}
    container_ips: dict[str, set[str]] = {}
    for ip, name in ip_map.items():
        container_ips.setdefault(name, set()).add(ip)

    connections: list[dict] = []
    seen: set[tuple] = set()

    for container_name, pid in pid_map.items():
        # Collect listening ports across both IPv4 and IPv6 for direction classification
        all_listen_ports: set[int] = set()
        all_established: list[tuple[str, int, str, int]] = []
        for tcp_path in (f"/proc/{pid}/net/tcp", f"/proc/{pid}/net/tcp6"):
            established, listen_ports = parse_proc_net_tcp(tcp_path, include_time_wait=True)
            all_listen_ports.update(listen_ports)
            all_established.extend(established)

        for local_ip, local_port, remote_ip, remote_port in all_established:
            if remote_ip.startswith("127.") or remote_ip == "0.0.0.0":
                continue

            # Direction: if local port is a listening port → inbound (client connected to us)
            # Otherwise → outbound (we initiated the connection)
            direction = "inbound" if local_port in all_listen_ports else "outbound"

            dedup_key = (container_name, "tcp", remote_ip, remote_port, direction)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            connections.append(
                {
                    "container_name": ip_map.get(local_ip, container_name),
                    "proto": SafeStr.of("tcp", "proto"),
                    "dst_ip": SafeIpAddress.of(remote_ip, "proc:dst_ip"),
                    "dst_port": remote_port,
                    "direction": direction,
                }
            )

    return connections


@sanitized.enforce
def get_metrics(service_id: SafeSlug, uid: int) -> dict:
    """Return CPU%, memory bytes, process count, and disk bytes for a service user."""
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

    disk_bytes = dir_size(os.path.join(_VOLUMES_BASE, service_id))

    return {
        "service_id": service_id,
        "cpu_percent": round(cpu_percent, 2),
        "mem_bytes": mem_bytes,
        "proc_count": proc_count,
        "disk_bytes": disk_bytes,
    }

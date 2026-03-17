"""Per-service resource usage metrics using psutil and disk walks."""

import json
import logging
import os
import re
import subprocess
from contextlib import suppress

import psutil

logger = logging.getLogger(__name__)

_VOLUMES_BASE = "/var/lib/quadletman/volumes"


def _dir_size(path: str) -> int:
    """Return total byte size of all files under path."""
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_dir(follow_symlinks=False):
                total += _dir_size(entry.path)
            elif entry.is_file(follow_symlinks=False):
                with suppress(OSError):
                    total += entry.stat().st_size
    except OSError:
        pass
    return total


def get_processes(uid: int) -> list[dict]:
    """Return process list for a service user UID."""
    procs = []
    for proc in psutil.process_iter(
        ["pid", "uids", "name", "cmdline", "cpu_percent", "memory_info", "status"]
    ):
        try:
            info = proc.info
            if info["uids"] and info["uids"].real == uid:
                procs.append(
                    {
                        "pid": info["pid"],
                        "name": info["name"] or "",
                        "cmdline": " ".join(info["cmdline"] or []) or info["name"] or "",
                        "cpu_percent": round(info["cpu_percent"] or 0.0, 1),
                        "mem_bytes": info["memory_info"].rss if info["memory_info"] else 0,
                        "status": info["status"] or "",
                    }
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return sorted(procs, key=lambda p: p["pid"])


def _podman_cmd(service_id: str) -> list[str]:
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


def _dir_size_excluding(path: str, exclude: str) -> int:
    """Return total byte size of all files under path, skipping the exclude subtree."""
    total = 0
    try:
        for entry in os.scandir(path):
            full = entry.path
            if os.path.abspath(full) == os.path.abspath(exclude):
                continue
            if entry.is_dir(follow_symlinks=False):
                total += _dir_size_excluding(full, exclude)
            elif entry.is_file(follow_symlinks=False):
                with suppress(OSError):
                    total += entry.stat().st_size
    except OSError:
        pass
    return total


def get_disk_breakdown(service_id: str) -> dict:
    """Return disk usage broken down by images, container overlays, managed volumes, and service config."""
    images: list[dict] = []
    overlays: list[dict] = []
    volumes_bytes = _dir_size(os.path.join(_VOLUMES_BASE, service_id))

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
                volume_details.append({"name": entry.name, "bytes": _dir_size(entry.path)})
    except OSError:
        pass

    # --- Service config (home dir excluding container storage) ---
    config_bytes = 0
    try:
        from .user_manager import get_home

        home = get_home(service_id)
        storage_dir = os.path.join(home, ".local", "share", "containers", "storage")
        config_bytes = _dir_size_excluding(home, storage_dir)
    except Exception as exc:
        logger.warning("Could not get config size for %s: %s", service_id, exc)

    return {
        "images": sorted(images, key=lambda x: x["bytes"], reverse=True),
        "overlays": sorted(overlays, key=lambda x: x["bytes"], reverse=True),
        "volumes": sorted(volume_details, key=lambda x: x["bytes"], reverse=True),
        "volumes_total": volumes_bytes,
        "config_bytes": config_bytes,
    }


def get_container_ips(service_id: str) -> dict[str, str]:
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


# Matches a single conntrack entry line, capturing proto, src, dst, dport.
# conntrack -L output format (first tuple is the original direction):
#   tcp  6 431999 ESTABLISHED src=10.88.0.5 dst=1.2.3.4 sport=54321 dport=443 ...
_CONNTRACK_RE = re.compile(
    r"^(?P<proto>\w+)\s+\d+.*?\bsrc=(?P<src>\S+)\s+dst=(?P<dst>\S+)\s+sport=\d+\s+dport=(?P<dport>\d+)"
)


def get_connections(service_id: str) -> list[dict]:
    """Return outbound connections for all running containers in a compartment.

    Builds an IP→container_name map from podman inspect, then reads the host conntrack
    table and filters entries whose source IP belongs to a container in this compartment.
    Returns a list of dicts with keys: container_name, proto, dst_ip, dst_port.
    conntrack must be installed on the host; missing or failed calls are silently ignored.
    """
    ip_map = get_container_ips(service_id)
    if not ip_map:
        return []

    connections: list[dict] = []
    try:
        result = subprocess.run(
            ["conntrack", "-L"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="/",
        )
        # conntrack writes entries to stdout; summary line goes to stderr — ignore stderr
        for line in result.stdout.splitlines():
            m = _CONNTRACK_RE.match(line.strip())
            if not m:
                continue
            src = m.group("src")
            container_name = ip_map.get(src)
            if container_name is None:
                continue
            connections.append(
                {
                    "container_name": container_name,
                    "proto": m.group("proto"),
                    "dst_ip": m.group("dst"),
                    "dst_port": int(m.group("dport")),
                }
            )
    except FileNotFoundError:
        logger.debug("conntrack not found on this host — connection monitor disabled")
    except Exception as exc:
        logger.debug("Could not read conntrack for %s: %s", service_id, exc)
    return connections


def get_metrics(service_id: str, uid: int) -> dict:
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

    disk_bytes = _dir_size(os.path.join(_VOLUMES_BASE, service_id))

    return {
        "service_id": service_id,
        "cpu_percent": round(cpu_percent, 2),
        "mem_bytes": mem_bytes,
        "proc_count": proc_count,
        "disk_bytes": disk_bytes,
    }

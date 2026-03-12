"""systemd --user operations executed as the service account."""

import logging
import os
import subprocess
from asyncio import subprocess as aio_subprocess

from .user_manager import _username, get_home, get_uid

logger = logging.getLogger(__name__)


def _base_cmd(service_id: str) -> list[str]:
    """Build sudo prefix to run a command as the service user with correct env."""
    username = _username(service_id)
    uid = get_uid(service_id)
    return [
        "sudo",
        "-u",
        username,
        "env",
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
    ]


def _run(service_id: str, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    cmd = _base_cmd(service_id) + list(args)
    return subprocess.run(cmd, cwd="/", capture_output=True, text=True, check=check)


def daemon_reload(service_id: str) -> None:
    result = _run(service_id, "systemctl", "--user", "daemon-reload")
    if result.returncode != 0:
        raise RuntimeError(f"daemon-reload failed for {service_id}: {result.stderr}")
    logger.info("daemon-reload completed for service %s", service_id)


def start_unit(service_id: str, unit: str) -> None:
    _run(service_id, "systemctl", "--user", "reset-failed", unit)
    result = _run(service_id, "systemctl", "--user", "start", unit)
    if result.returncode != 0:
        detail = result.stderr.strip()
        journal = get_journal_lines(service_id, unit, lines=20).strip()
        if journal:
            detail = f"{detail}\n{journal}" if detail else journal
        raise RuntimeError(detail)


def stop_unit(service_id: str, unit: str) -> None:
    result = _run(service_id, "systemctl", "--user", "stop", unit)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to stop {unit} for {service_id}: {result.stderr}")
    # Clear any failed state so the unit is clean for the next start
    _run(service_id, "systemctl", "--user", "reset-failed", unit)


def restart_unit(service_id: str, unit: str) -> None:
    result = _run(service_id, "systemctl", "--user", "restart", unit)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to restart {unit} for {service_id}: {result.stderr}")


def enable_unit(service_id: str, container_name: str) -> None:
    """Unmask a quadlet container unit to restore autostart.

    Removes the /dev/null mask symlink directly — systemctl unmask requires
    the generated unit to already be in the search path which is not reliable.
    """
    home = get_home(service_id)
    mask_path = os.path.join(home, ".config", "systemd", "user", f"{container_name}.service")
    if os.path.islink(mask_path) and os.readlink(mask_path) == "/dev/null":
        os.unlink(mask_path)


def disable_unit(service_id: str, container_name: str) -> None:
    """Mask a quadlet container unit to prevent autostart.

    Creates ~/.config/systemd/user/{name}.service -> /dev/null directly rather
    than using systemctl mask, which requires the generated unit to already be
    in the systemd search path.
    """
    home = get_home(service_id)
    systemd_user_dir = os.path.join(home, ".config", "systemd", "user")
    os.makedirs(systemd_user_dir, exist_ok=True)
    mask_path = os.path.join(systemd_user_dir, f"{container_name}.service")
    if os.path.islink(mask_path):
        os.unlink(mask_path)
    os.symlink("/dev/null", mask_path)


def get_unit_status(service_id: str, unit: str) -> dict[str, str]:
    """Return dict of systemd unit properties."""
    result = _run(
        service_id,
        "systemctl",
        "--user",
        "show",
        unit,
        "--property=ActiveState,SubState,LoadState,UnitFileState,MainPID",
    )
    props: dict[str, str] = {}
    for line in result.stdout.splitlines():
        k, _, v = line.partition("=")
        if k:
            props[k] = v
    return props


def _is_unit_enabled(service_id: str, unit: str) -> bool:
    """Check if a quadlet --user unit is enabled (i.e. not masked).

    Quadlet units with WantedBy=default.target are auto-started by the generator,
    so 'enabled' is the default state. The only way to suppress autostart is
    masking, which creates a symlink to /dev/null at
    ~/.config/systemd/user/{unit}. If that symlink exists, the unit is disabled.
    """
    home = get_home(service_id)
    mask_path = os.path.join(home, ".config", "systemd", "user", unit)
    if os.path.islink(mask_path):
        return os.readlink(mask_path) != "/dev/null"
    # Also check the container file exists (unit is deployed)
    container_name = unit.removesuffix(".service")
    container_file = os.path.join(
        home, ".config", "containers", "systemd", f"{container_name}.container"
    )
    return os.path.exists(container_file)


def get_service_status(service_id: str, container_names: list[str]) -> list[dict]:
    """Return status for all containers in a service."""
    statuses = []
    for name in container_names:
        unit = f"{name}.service"
        props = get_unit_status(service_id, unit)
        status_result = _run(service_id, "systemctl", "--user", "status", "--no-pager", unit)
        unit_file_state = "enabled" if _is_unit_enabled(service_id, unit) else "disabled"
        statuses.append(
            {
                "container": name,
                "unit": unit,
                "active_state": props.get("ActiveState", "unknown"),
                "sub_state": props.get("SubState", ""),
                "load_state": props.get("LoadState", "not-found"),
                "unit_file_state": unit_file_state,
                "main_pid": props.get("MainPID", ""),
                "status_text": status_result.stdout.strip(),
            }
        )
    return statuses


def get_journal_lines(service_id: str, unit: str, lines: int = 200) -> str:
    """Return journald log lines for a unit as a string.

    Runs journalctl as root (the calling process) using UID + user-unit filters
    so that the unprivileged qm-* user's journal is accessible.
    """
    uid = get_uid(service_id)
    result = subprocess.run(
        [
            "journalctl",
            f"_UID={uid}",
            f"_SYSTEMD_USER_UNIT={unit}",
            f"-n{lines}",
            "--no-pager",
            "--output=short-iso",
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout or result.stderr


async def stream_journal(service_id: str, unit: str):
    """Async generator yielding journal lines as SSE data.

    Runs journalctl as root with UID + user-unit filters.
    """
    uid = get_uid(service_id)
    proc = await aio_subprocess.create_subprocess_exec(
        "journalctl",
        f"_UID={uid}",
        f"_SYSTEMD_USER_UNIT={unit}",
        "--no-pager",
        "--output=short-iso",
        "-f",
        "-n", "50",
        stdout=aio_subprocess.PIPE,
        stderr=aio_subprocess.STDOUT,
    )
    try:
        async for line in proc.stdout:
            yield line.decode(errors="replace").rstrip()
    finally:
        proc.kill()
        await proc.wait()

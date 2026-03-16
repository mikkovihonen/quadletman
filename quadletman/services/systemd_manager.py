"""systemd --user operations executed as the service account."""

import logging
import os
import subprocess
from asyncio import subprocess as aio_subprocess

from . import host
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
    return host.run(cmd, cwd="/", capture_output=True, text=True, check=check)


def exec_pty_cmd(service_id: str, container_name: str, exec_user: str | None = None) -> list[str]:
    """Return argv for an interactive podman exec into container_name.

    exec_user is passed as --user (e.g. "root" or "1000"); defaults to root if None.
    """
    cmd = _base_cmd(service_id) + ["podman", "exec", "-it"]
    if exec_user is not None:
        cmd += ["--user", exec_user]
    return cmd + [container_name, "/bin/sh"]


def list_images(service_id: str) -> list[str]:
    """Return a sorted list of image references available to the compartment user.

    Runs ``podman images`` as the compartment user and returns fully-qualified
    ``repository:tag`` strings.  Returns an empty list if podman is unavailable
    or the user does not exist yet.
    """
    result = _run(service_id, "podman", "images", "--format", "{{.Repository}}:{{.Tag}}")
    if result.returncode != 0:
        return []
    images = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line and "<none>" not in line:
            images.append(line)
    return sorted(set(images))


@host.audit("DAEMON_RELOAD", lambda sid, *_: sid)
def daemon_reload(service_id: str) -> None:
    result = _run(service_id, "systemctl", "--user", "daemon-reload")
    if result.returncode != 0:
        raise RuntimeError(f"daemon-reload failed for {service_id}: {result.stderr}")
    logger.info("daemon-reload completed for service %s", service_id)


@host.audit("UNIT_START", lambda sid, unit, *_: f"{sid}/{unit}")
def start_unit(service_id: str, unit: str) -> None:
    _run(service_id, "systemctl", "--user", "reset-failed", unit)
    result = _run(service_id, "systemctl", "--user", "start", unit)
    if result.returncode != 0:
        detail = result.stderr.strip()
        journal = get_journal_lines(service_id, unit, lines=20).strip()
        if journal:
            detail = f"{detail}\n{journal}" if detail else journal
        raise RuntimeError(detail)


@host.audit("UNIT_STOP", lambda sid, unit, *_: f"{sid}/{unit}")
def stop_unit(service_id: str, unit: str) -> None:
    result = _run(service_id, "systemctl", "--user", "stop", unit)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to stop {unit} for {service_id}: {result.stderr}")
    # Clear any failed state so the unit is clean for the next start
    _run(service_id, "systemctl", "--user", "reset-failed", unit)


@host.audit("UNIT_RESTART", lambda sid, unit, *_: f"{sid}/{unit}")
def restart_unit(service_id: str, unit: str) -> None:
    result = _run(service_id, "systemctl", "--user", "restart", unit)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to restart {unit} for {service_id}: {result.stderr}")


@host.audit("UNIT_ENABLE", lambda sid, name, *_: f"{sid}/{name}")
def enable_unit(service_id: str, container_name: str) -> None:
    """Unmask a quadlet container unit to restore autostart.

    Removes the /dev/null mask symlink directly — systemctl unmask requires
    the generated unit to already be in the search path which is not reliable.
    """
    home = get_home(service_id)
    mask_path = os.path.join(home, ".config", "systemd", "user", f"{container_name}.service")
    if os.path.islink(mask_path) and os.readlink(mask_path) == "/dev/null":
        host.unlink(mask_path)


@host.audit("UNIT_DISABLE", lambda sid, name, *_: f"{sid}/{name}")
def disable_unit(service_id: str, container_name: str) -> None:
    """Mask a quadlet container unit to prevent autostart.

    Creates ~/.config/systemd/user/{name}.service -> /dev/null directly rather
    than using systemctl mask, which requires the generated unit to already be
    in the systemd search path.
    """
    home = get_home(service_id)
    systemd_user_dir = os.path.join(home, ".config", "systemd", "user")
    host.makedirs(systemd_user_dir, exist_ok=True)
    mask_path = os.path.join(systemd_user_dir, f"{container_name}.service")
    if os.path.islink(mask_path):
        host.unlink(mask_path)
    host.symlink("/dev/null", mask_path)


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


async def stream_journal_xe(service_id: str):
    """Async generator yielding recent system journal lines for a compartment user.

    Equivalent to 'journalctl -xe' scoped to the compartment's UID — useful for
    diagnosing systemd dependency failures and other startup errors.
    """
    uid = get_uid(service_id)
    proc = await aio_subprocess.create_subprocess_exec(
        "journalctl",
        f"_UID={uid}",
        "--no-pager",
        "--output=short-iso",
        "-x",
        "-n",
        "200",
        stdout=aio_subprocess.PIPE,
        stderr=aio_subprocess.STDOUT,
    )
    try:
        async for line in proc.stdout:
            yield line.decode(errors="replace").rstrip()
    finally:
        proc.kill()
        await proc.wait()


async def stream_podman_logs(service_id: str, container_name: str):
    """Async generator yielding lines from 'podman logs -f' as SSE data.

    Runs as the compartment user. Use for json-file and k8s-file log drivers
    where journald has no entries.
    """
    cmd = _base_cmd(service_id) + [
        "podman",
        "logs",
        "--follow",
        "--tail",
        "50",
        "--timestamps",
        container_name,
    ]
    proc = await aio_subprocess.create_subprocess_exec(
        *cmd,
        cwd="/",
        stdout=aio_subprocess.PIPE,
        stderr=aio_subprocess.STDOUT,
    )
    try:
        async for line in proc.stdout:
            yield line.decode(errors="replace").rstrip()
    finally:
        proc.kill()
        await proc.wait()


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
        "-n",
        "50",
        stdout=aio_subprocess.PIPE,
        stderr=aio_subprocess.STDOUT,
    )
    try:
        async for line in proc.stdout:
            yield line.decode(errors="replace").rstrip()
    finally:
        proc.kill()
        await proc.wait()

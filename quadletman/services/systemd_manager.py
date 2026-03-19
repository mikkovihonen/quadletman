"""systemd --user operations executed as the service account."""

import logging
import os
import subprocess
import time
from asyncio import subprocess as aio_subprocess

from quadletman.models import sanitized
from quadletman.models.sanitized import SafeAbsPath, SafeSlug, SafeStr, SafeUnitName

from . import host
from .user_manager import _username, get_home, get_uid

logger = logging.getLogger(__name__)

# TTL cache for unit status queries — avoids hammering systemctl when the
# dashboard or status-dot endpoints are polled in rapid succession.
_UNIT_STATUS_TTL = 5.0  # seconds
_unit_status_cache: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}
_unit_text_cache: dict[tuple[str, str], tuple[float, str]] = {}


@sanitized.enforce
def _cached_unit_props(service_id: SafeSlug, unit: SafeUnitName) -> dict[str, str]:
    key = (service_id, unit)
    now = time.monotonic()
    entry = _unit_status_cache.get(key)
    if entry is not None and now - entry[0] < _UNIT_STATUS_TTL:
        return entry[1]
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
    _unit_status_cache[key] = (now, props)
    return props


@sanitized.enforce
def _cached_unit_text(service_id: SafeSlug, unit: SafeUnitName) -> str:
    key = (service_id, unit)
    now = time.monotonic()
    entry = _unit_text_cache.get(key)
    if entry is not None and now - entry[0] < _UNIT_STATUS_TTL:
        return entry[1]
    result = _run(service_id, "systemctl", "--user", "status", "--no-pager", unit)
    text = result.stdout.strip()
    _unit_text_cache[key] = (now, text)
    return text


@sanitized.enforce
def invalidate_unit_cache(service_id: SafeSlug, unit: SafeUnitName) -> None:
    """Remove cached status for a unit — call after start/stop/restart."""
    _unit_status_cache.pop((service_id, unit), None)
    _unit_text_cache.pop((service_id, unit), None)


@sanitized.enforce
def _base_cmd(service_id: SafeSlug) -> list[str]:
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


@sanitized.enforce
def _run(service_id: SafeSlug, *args, check: bool = False) -> subprocess.CompletedProcess:
    cmd = _base_cmd(service_id) + list(args)
    return host.run(cmd, cwd="/", capture_output=True, text=True, check=check)


@sanitized.enforce
def exec_pty_cmd(
    service_id: SafeSlug, container_name: SafeStr, exec_user: SafeStr | None = None
) -> list[str]:
    """Return argv for an interactive podman exec into container_name.

    exec_user is passed as --user (e.g. "root" or "1000"); defaults to root if None.
    """
    cmd = _base_cmd(service_id) + ["podman", "exec", "-it"]
    if exec_user is not None:
        cmd += ["--user", exec_user]
    return cmd + [container_name, "/bin/sh"]


@sanitized.enforce
def list_images(service_id: SafeSlug) -> list[str]:
    """Return a sorted list of image references available to the compartment user."""
    result = _run(service_id, "podman", "images", "--format", "{{.Repository}}:{{.Tag}}")
    if result.returncode != 0:
        return []
    images = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line and "<none>" not in line:
            images.append(line)
    return sorted(set(images))


@sanitized.enforce
def list_images_detail(service_id: SafeSlug) -> list[dict]:
    """Return image details (id, repository, tag, size, created) for the compartment user."""
    import json as _json

    result = _run(
        service_id,
        "podman",
        "images",
        "--format",
        "json",
    )
    if result.returncode != 0:
        return []
    try:
        raw = _json.loads(result.stdout or "[]")
    except Exception:
        return []
    out = []
    for img in raw:
        names = img.get("Names") or img.get("names") or []
        repo_tags = img.get("RepoTags") or []
        all_names = list(set(names + repo_tags))
        # Skip untagged dangling images
        visible = [n for n in all_names if "<none>" not in n]
        out.append(
            {
                "id": (img.get("Id") or img.get("id") or "")[:12],
                "names": visible if visible else all_names[:1],
                "size": img.get("Size") or img.get("size") or 0,
                "created": img.get("Created") or img.get("created") or "",
                "dangling": not visible,
            }
        )
    return out


@host.audit("PRUNE_IMAGES", lambda sid, *_: sid)
@sanitized.enforce
def prune_images(service_id: SafeSlug) -> dict:
    """Remove unused (dangling) images for the compartment user.

    Returns a dict with 'reclaimed' bytes and 'count' of images removed.
    """
    result = _run(service_id, "podman", "image", "prune", "--force")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "podman image prune failed")
    # Parse output lines like "Deleted: sha256:abc123…" and space info
    lines = result.stdout.splitlines()
    count = sum(1 for ln in lines if ln.startswith("Deleted:") or ln.startswith("deleted:"))
    space_str = ""
    for ln in lines:
        if "reclaim" in ln.lower() or "freed" in ln.lower():
            space_str = ln.strip()
            break
    return {"count": count, "space": space_str, "output": result.stdout.strip()}


@host.audit("PULL_IMAGE", lambda sid, image, *_: f"{sid}/{image}")
@sanitized.enforce
def pull_image(service_id: SafeSlug, image: SafeStr) -> str:
    """Pull (or re-pull) a container image as the compartment user. Returns stdout."""
    result = _run(service_id, "podman", "pull", image)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"podman pull {image} failed")
    return result.stdout.strip()


@sanitized.enforce
def get_timer_status(service_id: SafeSlug, timer_name: SafeStr) -> dict:
    """Return last-run info for a systemd timer unit.

    Queries systemctl show for the timer unit and extracts LastTriggerUSec,
    LastTriggerUSecMonotonic, and NextElapseUSecRealtime so the UI can display
    'last run' and 'next run' times without shelling out per request.
    """
    unit = f"{timer_name}.timer"
    result = _run(
        service_id,
        "systemctl",
        "--user",
        "show",
        unit,
        "--property=LastTriggerUSec,LastTriggerUSecMonotonic,"
        "NextElapseUSecRealtime,ActiveState,SubState,Result",
    )
    info: dict[str, str] = {}
    if result.returncode != 0:
        return info
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        info[key.strip()] = value.strip()
    return {
        "timer": timer_name,
        "active_state": info.get("ActiveState", ""),
        "sub_state": info.get("SubState", ""),
        "last_trigger": info.get("LastTriggerUSec", ""),
        "next_elapse": info.get("NextElapseUSecRealtime", ""),
        "result": info.get("Result", ""),
    }


@host.audit("DAEMON_RELOAD", lambda sid, *_: sid)
@sanitized.enforce
def daemon_reload(service_id: SafeSlug) -> None:
    result = _run(service_id, "systemctl", "--user", "daemon-reload")
    if result.returncode != 0:
        raise RuntimeError(f"daemon-reload failed for {service_id}: {result.stderr}")
    logger.info("daemon-reload completed for service %s", service_id)


@host.audit("UNIT_START", lambda sid, unit, *_: f"{sid}/{unit}")
@sanitized.enforce
def start_unit(service_id: SafeSlug, unit: SafeUnitName) -> None:
    invalidate_unit_cache(service_id, unit)
    _run(service_id, "systemctl", "--user", "reset-failed", unit)
    result = _run(service_id, "systemctl", "--user", "start", unit)
    if result.returncode != 0:
        detail = result.stderr.strip()
        journal = get_journal_lines(service_id, unit, lines=20).strip()
        if journal:
            detail = f"{detail}\n{journal}" if detail else journal
        raise RuntimeError(detail)


@host.audit("UNIT_STOP", lambda sid, unit, *_: f"{sid}/{unit}")
@sanitized.enforce
def stop_unit(service_id: SafeSlug, unit: SafeUnitName) -> None:
    invalidate_unit_cache(service_id, unit)
    result = _run(service_id, "systemctl", "--user", "stop", unit)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to stop {unit} for {service_id}: {result.stderr}")
    # Clear any failed state so the unit is clean for the next start
    _run(service_id, "systemctl", "--user", "reset-failed", unit)


@host.audit("UNIT_RESTART", lambda sid, unit, *_: f"{sid}/{unit}")
@sanitized.enforce
def restart_unit(service_id: SafeSlug, unit: SafeUnitName) -> None:
    invalidate_unit_cache(service_id, unit)
    result = _run(service_id, "systemctl", "--user", "restart", unit)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to restart {unit} for {service_id}: {result.stderr}")


@host.audit("UNIT_ENABLE", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def enable_unit(service_id: SafeSlug, container_name: SafeUnitName) -> None:
    """Unmask a quadlet container unit to restore autostart.

    Removes the /dev/null mask symlink directly — systemctl unmask requires
    the generated unit to already be in the search path which is not reliable.
    """
    home = get_home(service_id)
    mask_path = os.path.join(home, ".config", "systemd", "user", f"{container_name}.service")
    if os.path.islink(mask_path) and os.readlink(mask_path) == "/dev/null":
        host.unlink(SafeAbsPath.of(mask_path, "mask_path"))


@host.audit("UNIT_DISABLE", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def disable_unit(service_id: SafeSlug, container_name: SafeUnitName) -> None:
    """Mask a quadlet container unit to prevent autostart.

    Creates ~/.config/systemd/user/{name}.service -> /dev/null directly rather
    than using systemctl mask, which requires the generated unit to already be
    in the systemd search path.
    """
    home = get_home(service_id)
    systemd_user_dir = os.path.join(home, ".config", "systemd", "user")
    host.makedirs(SafeAbsPath.of(systemd_user_dir, "systemd_user_dir"), exist_ok=True)
    mask_path = os.path.join(systemd_user_dir, f"{container_name}.service")
    if os.path.islink(mask_path):
        host.unlink(SafeAbsPath.of(mask_path, "mask_path"))
    host.symlink(
        SafeAbsPath.trusted("/dev/null", "hardcoded"), SafeAbsPath.of(mask_path, "mask_path")
    )


@sanitized.enforce
def get_unit_status(service_id: SafeSlug, unit: SafeUnitName) -> dict[str, str]:
    """Return dict of systemd unit properties (TTL-cached)."""
    return _cached_unit_props(service_id, unit)


@sanitized.enforce
def _is_unit_enabled(service_id: SafeSlug, unit: SafeUnitName) -> bool:
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


@sanitized.enforce
def get_service_status(service_id: SafeSlug, container_names: list[SafeStr]) -> list[dict]:
    """Return status for all containers in a service."""
    statuses = []
    for name in container_names:
        unit = SafeUnitName.of(f"{name}.service", "unit_name")
        props = _cached_unit_props(service_id, unit)
        status_text = _cached_unit_text(service_id, unit)
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
                "status_text": status_text,
            }
        )
    return statuses


@sanitized.enforce
def inspect_container(service_id: SafeSlug, container_name: SafeStr) -> dict:
    """Return parsed podman inspect output for a running container.

    The container name in Podman is prefixed with the service_id (e.g. myapp-web).
    Returns an empty dict if the container doesn't exist or inspect fails.
    """
    import json as _json

    full_name = f"{service_id}-{container_name}"
    cmd = _base_cmd(service_id) + ["podman", "inspect", full_name]
    result = subprocess.run(cmd, cwd="/", capture_output=True, text=True)
    if result.returncode != 0:
        return {}
    try:
        items = _json.loads(result.stdout or "[]")
        return items[0] if items else {}
    except (_json.JSONDecodeError, IndexError):
        return {}


@sanitized.enforce
def get_journal_lines(service_id: SafeSlug, unit: SafeUnitName, lines: int = 200) -> str:
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


@sanitized.enforce
async def stream_journal_xe(service_id: SafeSlug):
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


@sanitized.enforce
async def stream_podman_logs(service_id: SafeSlug, container_name: SafeStr):
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


@sanitized.enforce
async def stream_journal(service_id: SafeSlug, unit: SafeUnitName):
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

"""systemd --user operations executed as the service account."""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from asyncio import subprocess as aio_subprocess

from quadletman.config.settings import settings
from quadletman.models import sanitized
from quadletman.models.sanitized import (
    SafeAbsPath,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeUnitName,
)

from . import host
from .quadlet_writer import _AGENT_UNIT_TEMPLATE
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
def shell_pty_cmd(service_id: SafeSlug) -> list[str]:
    """Return argv for an interactive shell as the compartment user.

    The qm-* users have /bin/false as their login shell, so we explicitly
    invoke /bin/bash via sudo.
    """
    username = _username(service_id)
    uid = get_uid(service_id)
    return [
        "sudo",
        "-u",
        username,
        "env",
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
        f"HOME=/home/{username}",
        "TERM=xterm-256color",
        "/bin/bash",
    ]


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


@host.audit("SYSTEM_PRUNE", lambda sid, *_: sid)
@sanitized.enforce
def system_prune(service_id: SafeSlug) -> str:
    """Remove all unused containers, images, networks, and build cache."""
    result = host.run(
        [*_base_cmd(service_id), "podman", "system", "prune", "-f"],
        capture_output=True,
        text=True,
    )
    return result.stdout


@sanitized.enforce
def container_top(service_id: SafeSlug, container_name: SafeResourceName) -> list[dict[str, str]]:
    """Return running processes inside a container via ``podman top``.

    ``podman top`` outputs a header line followed by one line per process
    using ps-style columns.  This function parses the tabular output into
    a list of dicts keyed by column header.
    """
    full_name = f"{service_id}-{container_name}"
    result = subprocess.run(
        [*_base_cmd(service_id), "podman", "top", full_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    lines = result.stdout.strip().splitlines()
    if len(lines) < 2:
        return []
    headers = lines[0].split()
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        cols = line.split(None, len(headers) - 1)
        rows.append(dict(zip(headers, cols, strict=False)))
    return rows


@host.audit("NETWORK_RELOAD", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def network_reload(service_id: SafeSlug, container_name: SafeResourceName) -> None:
    """Reload network configuration for a running container without restart."""
    full_name = f"{service_id}-{container_name}"
    host.run(
        [*_base_cmd(service_id), "podman", "network", "reload", full_name],
        check=True,
    )


@sanitized.enforce
def system_df(service_id: SafeSlug) -> dict:
    """Return disk usage breakdown via ``podman system df --format=json``."""
    result = subprocess.run(
        [*_base_cmd(service_id), "podman", "system", "df", "--format=json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return {}


@sanitized.enforce
def generate_kube(service_id: SafeSlug, container_name: SafeResourceName) -> str:
    """Export a container or pod to Kubernetes YAML via ``podman generate kube``."""
    full_name = f"{service_id}-{container_name}"
    result = subprocess.run(
        [*_base_cmd(service_id), "podman", "generate", "kube", full_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


@sanitized.enforce
def healthcheck_run(service_id: SafeSlug, container_name: SafeResourceName) -> dict:
    """Run a health check on a container and return the result."""
    full_name = f"{service_id}-{container_name}"
    result = subprocess.run(
        [*_base_cmd(service_id), "podman", "healthcheck", "run", full_name],
        capture_output=True,
        text=True,
    )
    return {"healthy": result.returncode == 0, "output": result.stdout.strip()}


@host.audit("AUTO_UPDATE", lambda sid, *_: sid)
@sanitized.enforce
def auto_update(service_id: SafeSlug) -> str:
    """Run ``podman auto-update`` to pull newer images and restart containers."""
    result = host.run(
        [*_base_cmd(service_id), "podman", "auto-update", "--format=json"],
        capture_output=True,
        text=True,
    )
    return result.stdout


@sanitized.enforce
def auto_update_dry_run(service_id: SafeSlug) -> list[dict]:
    """Run ``podman auto-update --dry-run --format=json`` to detect pending image updates.

    Returns a list of dicts with keys: Unit, Container, Image, Policy, Updated.
    Only containers with ``AutoUpdate=registry`` appear.  Returns an empty list
    on error or when no updates are pending.
    """
    result = subprocess.run(
        [*_base_cmd(service_id), "podman", "auto-update", "--dry-run", "--format=json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


@sanitized.enforce
def volume_export(service_id: SafeSlug, volume_name: SafeResourceName) -> bytes:
    """Export a Podman-managed volume as a tar archive."""
    full_name = f"{service_id}-{volume_name}"
    result = subprocess.run(
        [*_base_cmd(service_id), "podman", "volume", "export", full_name],
        capture_output=True,
    )
    if result.returncode != 0:
        return b""
    return result.stdout


@host.audit("VOLUME_IMPORT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def volume_import(service_id: SafeSlug, volume_name: SafeResourceName, tar_data: bytes) -> None:
    """Import a tar archive into a Podman-managed volume."""
    full_name = f"{service_id}-{volume_name}"
    host.run(
        [*_base_cmd(service_id), "podman", "volume", "import", full_name, "-"],
        input=tar_data,
        check=True,
    )


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

    Uses unconditional unlink (no check-then-act) to avoid TOCTOU races.
    """
    home = get_home(service_id)
    mask_path = os.path.join(home, ".config", "systemd", "user", f"{container_name}.service")
    safe_mask = SafeAbsPath.of(mask_path, "mask_path")
    # Unconditional unlink — avoids TOCTOU between islink() and unlink().
    # If the path does not exist or is not a symlink to /dev/null, the unlink
    # either no-ops (FileNotFoundError) or removes whatever is there, which is
    # the correct behaviour for "ensure not masked".
    try:
        # Only remove if it is actually a /dev/null mask — read target atomically
        target = os.readlink(mask_path)
        if target == "/dev/null":
            host.unlink(safe_mask)
    except OSError:
        pass  # Not a symlink or doesn't exist — already unmasked


@host.audit("UNIT_DISABLE", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def disable_unit(service_id: SafeSlug, container_name: SafeUnitName) -> None:
    """Mask a quadlet container unit to prevent autostart.

    Creates ~/.config/systemd/user/{name}.service -> /dev/null directly rather
    than using systemctl mask, which requires the generated unit to already be
    in the systemd search path.

    Uses a temporary symlink + atomic rename to avoid TOCTOU races on the
    mask path.
    """
    home = get_home(service_id)
    systemd_user_dir = os.path.join(home, ".config", "systemd", "user")
    host.makedirs(SafeAbsPath.of(systemd_user_dir, "systemd_user_dir"), exist_ok=True)
    mask_path = os.path.join(systemd_user_dir, f"{container_name}.service")

    # Create a temporary symlink in the same directory, then atomically rename
    # it over the target path.  os.rename() on the same filesystem is atomic
    # on Linux, eliminating the TOCTOU window between unlink and symlink.
    tmp_fd, tmp_path = tempfile.mkstemp(dir=systemd_user_dir, prefix=".mask-")
    os.close(tmp_fd)
    os.unlink(tmp_path)  # mkstemp creates a regular file; we need a symlink
    os.symlink("/dev/null", tmp_path)
    os.rename(tmp_path, mask_path)


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
    owner = _username(service_id)
    mask_path = SafeAbsPath.of(os.path.join(home, ".config", "systemd", "user", unit), "mask_path")
    if host.path_islink(mask_path, owner=owner):
        return host.readlink(mask_path, owner=owner) != "/dev/null"
    # Also check the container file exists (unit is deployed)
    container_name = unit.removesuffix(".service")
    container_file = SafeAbsPath.of(
        os.path.join(home, ".config", "containers", "systemd", f"{container_name}.container"),
        "container_file",
    )
    return host.path_exists(container_file, owner=owner)


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


_AUTO_UPDATE_TIMER = SafeUnitName.trusted("podman-auto-update.timer", "auto_update_timer")


@sanitized.enforce
def get_auto_update_timer_enabled(service_id: SafeSlug) -> bool:
    """Check whether the podman-auto-update.timer is active for a compartment user."""
    try:
        props = _cached_unit_props(service_id, _AUTO_UPDATE_TIMER)
        return props.get("ActiveState", "") == "active"
    except (KeyError, subprocess.CalledProcessError, OSError):
        return False


@host.audit("AUTO_UPDATE_ENABLE", lambda sid, *_: sid)
@sanitized.enforce
def enable_auto_update_timer(service_id: SafeSlug) -> None:
    """Enable and start the podman-auto-update.timer for a compartment user.

    Creates the timers.target.wants symlink manually because
    ``systemctl --user enable`` fails if the wants directory doesn't exist
    under the user's home (common on freshly created rootless users).

    All operations run as the compartment user (via ``_run``), not via
    admin escalation, since the files are in the user's home directory.
    """
    home = get_home(service_id)
    owner = _username(service_id)
    wants_dir = f"{home}/.config/systemd/user/timers.target.wants"
    # Create wants dir and symlink as the compartment user
    host.run_as_user(owner, ["mkdir", "-p", wants_dir])
    # Find the system unit file to symlink to
    unit_path = f"/usr/lib/systemd/user/{_AUTO_UPDATE_TIMER}"
    if not os.path.exists(unit_path):
        unit_path = f"/lib/systemd/user/{_AUTO_UPDATE_TIMER}"
    if os.path.exists(unit_path):
        link = f"{wants_dir}/{_AUTO_UPDATE_TIMER}"
        host.run_as_user(owner, ["ln", "-sf", unit_path, link])
    _run(service_id, "systemctl", "--user", "daemon-reload")
    _run(service_id, "systemctl", "--user", "start", _AUTO_UPDATE_TIMER)
    invalidate_unit_cache(service_id, _AUTO_UPDATE_TIMER)
    logger.info("Enabled podman-auto-update.timer for %s", service_id)


@host.audit("AUTO_UPDATE_DISABLE", lambda sid, *_: sid)
@sanitized.enforce
def disable_auto_update_timer(service_id: SafeSlug) -> None:
    """Disable and stop the podman-auto-update.timer for a compartment user."""
    home = get_home(service_id)
    owner = _username(service_id)
    link = f"{home}/.config/systemd/user/timers.target.wants/{_AUTO_UPDATE_TIMER}"
    _run(service_id, "systemctl", "--user", "stop", _AUTO_UPDATE_TIMER)
    host.run_as_user(owner, ["rm", "-f", link])
    _run(service_id, "systemctl", "--user", "daemon-reload")
    invalidate_unit_cache(service_id, _AUTO_UPDATE_TIMER)
    logger.info("Disabled podman-auto-update.timer for %s", service_id)


@sanitized.enforce
def get_agent_status(service_id: SafeSlug) -> str:
    """Return the ActiveState of the monitoring agent unit for a compartment.

    Returns 'active', 'inactive', 'failed', 'not-found', etc.
    In root mode (no agents), returns 'not-applicable'.
    Returns 'unknown' if the compartment user does not exist.
    """
    if os.getuid() == 0:
        return "not-applicable"
    try:
        unit = SafeUnitName.of("quadletman-agent.service", "agent_unit")
        props = _cached_unit_props(service_id, unit)
        return props.get("ActiveState", "unknown")
    except (KeyError, subprocess.CalledProcessError, OSError):
        return "unknown"


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
def read_container_tcp(service_id: SafeSlug, container_name: SafeStr) -> str:
    """Return raw /proc/<pid>/net/tcp content for a running container.

    Reads from the container's network namespace by looking up the container
    PID via ``podman inspect`` and reading ``/proc/<pid>/net/tcp``.
    Returns empty string if the container is not running or the file is unreadable.
    """
    data = inspect_container(service_id, container_name)
    pid = data.get("State", {}).get("Pid", 0)
    if not pid or pid <= 0:
        return ""
    lines = []
    for tcp_file in (f"/proc/{pid}/net/tcp", f"/proc/{pid}/net/tcp6"):
        try:
            with open(tcp_file) as f:
                content = f.read()
            if content.strip():
                lines.append(f"# {tcp_file}\n{content}")
        except (FileNotFoundError, PermissionError):
            pass
    return "\n".join(lines)


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


# ---------------------------------------------------------------------------
# Agent unit file management (runs as qm-* user — no admin creds needed)
# ---------------------------------------------------------------------------


@host.audit("ENSURE_AGENT", lambda sid, *_: sid)
@sanitized.enforce
def ensure_agent_unit(service_id: SafeSlug) -> None:
    """Ensure the monitoring agent unit file exists for a compartment.

    Unlike ``quadlet_writer.deploy_agent_service`` (which uses ``host.makedirs``
    / ``host.write_text`` requiring admin credentials), this writes the file as
    the qm-* user via ``sudo -u qm-*`` — no admin session required.  Safe to
    call from the restart-agent route where admin credentials may not be
    available.
    """
    if os.getuid() == 0:
        return  # Root mode — no agents

    agent_bin = shutil.which("quadletman-agent")
    if not agent_bin:
        logger.warning(
            "quadletman-agent not found in PATH — cannot restore agent for %s",
            service_id,
        )
        return

    extra_env = ""
    pythonpath = os.environ.get("PYTHONPATH", "")
    if pythonpath:
        extra_env = f"Environment=PYTHONPATH={pythonpath}\n"

    content = _AGENT_UNIT_TEMPLATE.format(
        compartment_id=service_id,
        agent_bin=agent_bin,
        agent_socket=settings.agent_socket,
        extra_env=extra_env,
    )

    home = get_home(service_id)
    unit_dir = f"{home}/.config/systemd/user"
    unit_path = f"{unit_dir}/quadletman-agent.service"

    # mkdir + write as the qm-* user (NOPASSWD sudo — no admin creds needed).
    # Use run_as_user (plain sudo -u) instead of _run/_base_cmd which adds
    # XDG_RUNTIME_DIR/DBUS env vars only needed for systemd/podman commands.
    username = _username(service_id)
    host.run_as_user(username, ["mkdir", "-p", unit_dir])
    host.run_as_user(username, ["tee", unit_path], input=content, check=True)

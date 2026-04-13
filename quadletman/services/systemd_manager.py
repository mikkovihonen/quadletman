"""systemd --user operations executed as the service account."""

import json
import logging
import os
import pwd
import shutil
import subprocess
import sys
import time
from asyncio import subprocess as aio_subprocess
from contextlib import suppress

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
from .user_manager import _helper_username, _username, get_helper_uid, get_home, get_uid

logger = logging.getLogger(__name__)

# TTL cache for unit status queries — avoids hammering systemctl when the
# dashboard or status-dot endpoints are polled in rapid succession.
_UNIT_STATUS_TTL = float(settings.status_cache_ttl)
_MAX_CACHE_SIZE = settings.status_cache_max_size
_unit_status_cache: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}
_unit_text_cache: dict[tuple[str, str], tuple[float, str]] = {}

_AUTO_UPDATE_TIMER = SafeUnitName.trusted("podman-auto-update.timer", "auto_update_timer")


_SHOW_PROPERTIES = "ActiveState,SubState,LoadState,UnitFileState,MainPID"


@sanitized.enforce
def _cached_unit_props(service_id: SafeSlug, unit: SafeUnitName) -> dict[str, str]:
    key = (service_id, unit)
    now = time.monotonic()
    entry = _unit_status_cache.get(key)
    if entry is not None and now - entry[0] < _UNIT_STATUS_TTL:
        return entry[1]
    result = _run(
        service_id,
        "/usr/bin/systemctl",
        "--user",
        "show",
        unit,
        f"--property={_SHOW_PROPERTIES}",
    )
    props: dict[str, str] = {}
    for line in result.stdout.splitlines():
        k, _, v = line.partition("=")
        if k:
            props[k] = v
    if len(_unit_status_cache) >= _MAX_CACHE_SIZE:
        _unit_status_cache.clear()
    _unit_status_cache[key] = (now, props)
    return props


@sanitized.enforce
def _batch_unit_props(service_id: SafeSlug, units: list[SafeUnitName]) -> dict[str, dict[str, str]]:
    """Fetch properties for multiple units in a single subprocess call.

    Returns a dict mapping unit name → properties dict.  Results are stored in
    the per-unit cache so subsequent single-unit lookups are cache hits.

    ``systemctl show unit1 unit2 ...`` outputs property blocks separated by a
    blank line, one block per unit in argument order.
    """
    now = time.monotonic()
    result_map: dict[str, dict[str, str]] = {}
    uncached: list[SafeUnitName] = []

    for unit in units:
        entry = _unit_status_cache.get((service_id, unit))
        if entry is not None and now - entry[0] < _UNIT_STATUS_TTL:
            result_map[unit] = entry[1]
        else:
            uncached.append(unit)

    if uncached:
        result = _run(
            service_id,
            "/usr/bin/systemctl",
            "--user",
            "show",
            *uncached,
            f"--property={_SHOW_PROPERTIES}",
        )
        # Parse blocks separated by blank lines — one block per unit.
        blocks = result.stdout.split("\n\n")
        for unit, block in zip(uncached, blocks, strict=False):
            props: dict[str, str] = {}
            for line in block.splitlines():
                k, _, v = line.partition("=")
                if k:
                    props[k] = v
            if len(_unit_status_cache) >= _MAX_CACHE_SIZE:
                _unit_status_cache.clear()
            _unit_status_cache[(service_id, unit)] = (now, props)
            result_map[unit] = props

    return result_map


@sanitized.enforce
def _cached_unit_text(service_id: SafeSlug, unit: SafeUnitName) -> str:
    key = (service_id, unit)
    now = time.monotonic()
    entry = _unit_text_cache.get(key)
    if entry is not None and now - entry[0] < _UNIT_STATUS_TTL:
        return entry[1]
    result = _run(service_id, "/usr/bin/systemctl", "--user", "status", "--no-pager", unit)
    text = result.stdout.strip()
    if len(_unit_text_cache) >= _MAX_CACHE_SIZE:
        _unit_text_cache.clear()
    _unit_text_cache[key] = (now, text)
    return text


@sanitized.enforce
def _batch_unit_text(service_id: SafeSlug, units: list[SafeUnitName]) -> dict[str, str]:
    """Fetch status text for multiple units in a single subprocess call.

    Returns a dict mapping unit name → status text.  Results are stored in
    the per-unit cache.

    ``systemctl status unit1 unit2 ...`` outputs status blocks separated by
    blank lines.  Each block starts with ``● unit-name`` (or ``○ unit-name``).
    We split on the unit marker to associate each block with its unit.
    """
    now = time.monotonic()
    result_map: dict[str, str] = {}
    uncached: list[SafeUnitName] = []

    for unit in units:
        entry = _unit_text_cache.get((service_id, unit))
        if entry is not None and now - entry[0] < _UNIT_STATUS_TTL:
            result_map[unit] = entry[1]
        else:
            uncached.append(unit)

    if uncached:
        result = _run(
            service_id,
            "/usr/bin/systemctl",
            "--user",
            "status",
            "--no-pager",
            *uncached,
        )
        # Split output into per-unit blocks.  Each block starts with a line
        # beginning with ● or ○ followed by the unit name.  We match against
        # the expected unit names to assign blocks.
        unit_set = {str(u): u for u in uncached}
        current_unit: SafeUnitName | None = None
        current_lines: list[str] = []

        def _flush():
            nonlocal current_unit, current_lines
            if current_unit is not None:
                text = "\n".join(current_lines).strip()
                if len(_unit_text_cache) >= _MAX_CACHE_SIZE:
                    _unit_text_cache.clear()
                _unit_text_cache[(service_id, current_unit)] = (now, text)
                result_map[current_unit] = text
            current_lines = []

        for line in result.stdout.splitlines():
            stripped = line.lstrip()
            # Detect unit block start: "● unit.service - description" or "○ ..."
            if stripped and stripped[0] in ("●", "○", "*"):
                parts = stripped.split(None, 2)
                if len(parts) >= 2 and parts[1] in unit_set:
                    _flush()
                    current_unit = unit_set[parts[1]]
            current_lines.append(line)
        _flush()

        # Any uncached unit not found in the output gets an empty string.
        for unit in uncached:
            result_map.setdefault(unit, "")

    return result_map


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
        "/usr/bin/env",
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
    ]


@sanitized.enforce
def _run(
    service_id: SafeSlug, *args, check: bool = False, timeout: int | None = None
) -> subprocess.CompletedProcess:
    if timeout is None:
        timeout = settings.subprocess_timeout
    cmd = _base_cmd(service_id) + list(args)
    return host.run(
        cmd, admin=True, cwd="/", capture_output=True, text=True, check=check, timeout=timeout
    )


@sanitized.enforce
def exec_pty_cmd(
    service_id: SafeSlug, container_name: SafeStr, exec_user: SafeStr | None = None
) -> list[str]:
    """Return argv for an interactive podman exec into container_name.

    exec_user is passed as --user (e.g. "root" or "1000"); defaults to root if None.
    """
    cmd = _base_cmd(service_id) + ["/usr/bin/podman", "exec", "-it"]
    if exec_user is not None:
        cmd += ["--user", exec_user]
    return cmd + [container_name, "/bin/sh"]


@sanitized.enforce
def shell_pty_cmd(service_id: SafeSlug, shell_user: SafeStr | None = None) -> list[str]:
    """Return argv for an interactive shell as the compartment or helper user.

    shell_user: None or "root" → compartment root user (qm-{id}).
                string of digits → helper user (qm-{id}-N) for container UID N.
    The qm-* users have /bin/false as their login shell, so we explicitly
    invoke /bin/bash via sudo.
    """
    # Resolve which user to run the shell as.
    root_username = _username(service_id)
    root_uid = get_uid(service_id)

    if shell_user and str(shell_user) not in ("", "root"):
        container_uid = int(shell_user)
        helper_name = _helper_username(service_id, container_uid)
        if get_helper_uid(service_id, container_uid) is None:
            raise ValueError(f"Helper user {helper_name} does not exist")
        run_as = str(helper_name)
    else:
        run_as = str(root_username)

    return [
        "sudo",
        "-u",
        run_as,
        "/usr/bin/env",
        f"XDG_RUNTIME_DIR=/run/user/{root_uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{root_uid}/bus",
        f"HOME=/home/{root_username}",
        "TERM=xterm-256color",
        "/bin/bash",
    ]


@sanitized.enforce
def list_images(service_id: SafeSlug) -> list[str]:
    """Return a sorted list of image references available to the compartment user."""
    result = _run(service_id, "/usr/bin/podman", "images", "--format", "{{.Repository}}:{{.Tag}}")
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
    result = _run(
        service_id,
        "/usr/bin/podman",
        "images",
        "--format",
        "json",
    )
    if result.returncode != 0:
        return []
    try:
        raw = json.loads(result.stdout or "[]")
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
    result = _run(service_id, "/usr/bin/podman", "image", "prune", "--force")
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
    result = _run(service_id, "/usr/bin/podman", "pull", image, timeout=settings.image_pull_timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"podman pull {image} failed")
    return result.stdout.strip()


@host.audit("SYSTEM_PRUNE", lambda sid, *_: sid)
@sanitized.enforce
def system_prune(service_id: SafeSlug) -> str:
    """Remove all unused containers, images, networks, and build cache."""
    result = host.run(
        [*_base_cmd(service_id), "/usr/bin/podman", "system", "prune", "-f"],
        admin=True,
        capture_output=True,
        text=True,
        timeout=120,
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
    result = host.run(
        [*_base_cmd(service_id), "/usr/bin/podman", "top", full_name],
        admin=True,
        capture_output=True,
        text=True,
        timeout=15,
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
        [*_base_cmd(service_id), "/usr/bin/podman", "network", "reload", full_name],
        admin=True,
        check=True,
        timeout=30,
    )


@sanitized.enforce
def system_df(service_id: SafeSlug) -> dict:
    """Return disk usage breakdown via ``podman system df --format=json``."""
    result = host.run(
        [*_base_cmd(service_id), "/usr/bin/podman", "system", "df", "--format=json"],
        admin=True,
        capture_output=True,
        text=True,
        timeout=30,
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
    result = host.run(
        [*_base_cmd(service_id), "/usr/bin/podman", "generate", "kube", full_name],
        admin=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


@sanitized.enforce
def healthcheck_run(service_id: SafeSlug, container_name: SafeResourceName) -> dict:
    """Run a health check on a container and return the result."""
    full_name = f"{service_id}-{container_name}"
    result = host.run(
        [*_base_cmd(service_id), "/usr/bin/podman", "healthcheck", "run", full_name],
        admin=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return {"healthy": result.returncode == 0, "output": result.stdout.strip()}


@host.audit("AUTO_UPDATE", lambda sid, *_: sid)
@sanitized.enforce
def auto_update(service_id: SafeSlug) -> str:
    """Run ``podman auto-update`` to pull newer images and restart containers."""
    result = host.run(
        [*_base_cmd(service_id), "/usr/bin/podman", "auto-update", "--format=json"],
        admin=True,
        capture_output=True,
        text=True,
        timeout=settings.image_pull_timeout,
    )
    return result.stdout


@sanitized.enforce
def auto_update_dry_run(service_id: SafeSlug) -> list[dict]:
    """Run ``podman auto-update --dry-run --format=json`` to detect pending image updates.

    Returns a list of dicts with keys: Unit, Container, Image, Policy, Updated.
    Only containers with ``AutoUpdate=registry`` appear.  Returns an empty list
    on error or when no updates are pending.
    """
    result = host.run(
        [*_base_cmd(service_id), "/usr/bin/podman", "auto-update", "--dry-run", "--format=json"],
        admin=True,
        capture_output=True,
        text=True,
        timeout=settings.image_pull_timeout,
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
    result = host.run(
        [*_base_cmd(service_id), "/usr/bin/podman", "volume", "export", full_name],
        admin=True,
        capture_output=True,
        timeout=120,
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
        [*_base_cmd(service_id), "/usr/bin/podman", "volume", "import", full_name, "-"],
        admin=True,
        input=tar_data,
        check=True,
        timeout=120,
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
        "/usr/bin/systemctl",
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
    result = _run(service_id, "/usr/bin/systemctl", "--user", "daemon-reload")
    if result.returncode != 0:
        raise RuntimeError(f"daemon-reload failed for {service_id}: {result.stderr}")
    logger.info("daemon-reload completed for service %s", service_id)


@host.audit("UNIT_START", lambda sid, unit, *_, **__: f"{sid}/{unit}")
@sanitized.enforce
def start_unit(service_id: SafeSlug, unit: SafeUnitName, timeout: int | None = None) -> None:
    invalidate_unit_cache(service_id, unit)
    _run(service_id, "/usr/bin/systemctl", "--user", "reset-failed", unit)
    result = _run(service_id, "/usr/bin/systemctl", "--user", "start", unit, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.strip()
        journal = get_journal_lines(service_id, unit, lines=20).strip()
        if journal:
            detail = f"{detail}\n{journal}" if detail else journal
        raise RuntimeError(detail)


@host.audit("UNIT_STOP", lambda sid, unit, *_, **__: f"{sid}/{unit}")
@sanitized.enforce
def stop_unit(service_id: SafeSlug, unit: SafeUnitName, timeout: int | None = None) -> None:
    invalidate_unit_cache(service_id, unit)
    result = _run(service_id, "/usr/bin/systemctl", "--user", "stop", unit, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to stop {unit} for {service_id}: {result.stderr}")
    # Clear any failed state so the unit is clean for the next start
    _run(service_id, "/usr/bin/systemctl", "--user", "reset-failed", unit)


@host.audit("UNIT_RESTART", lambda sid, unit, *_, **__: f"{sid}/{unit}")
@sanitized.enforce
def restart_unit(service_id: SafeSlug, unit: SafeUnitName, timeout: int | None = None) -> None:
    invalidate_unit_cache(service_id, unit)
    result = _run(service_id, "/usr/bin/systemctl", "--user", "restart", unit, timeout=timeout)
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
    # Create each directory level with correct ownership via admin sudo.
    username = _username(service_id)
    for subpath in [".config", ".config/systemd", ".config/systemd/user"]:
        d = SafeAbsPath.of(os.path.join(home, subpath), "systemd_dir_part")
        host.run(
            ["install", "-d", "-o", username, "-g", username, "-m", "0700", str(d)],
            admin=True,
            check=True,
            capture_output=True,
            text=True,
        )
    mask_path = os.path.join(systemd_user_dir, f"{container_name}.service")

    # Create a temporary symlink in the same directory, then atomically rename
    # it over the target path.  os.rename() on the same filesystem is atomic
    # on Linux, eliminating the TOCTOU window between unlink and symlink.
    tmp_name = f".mask-{os.getpid()}-{container_name}"
    tmp_path = SafeAbsPath.of(os.path.join(systemd_user_dir, tmp_name), "mask_tmp")
    safe_mask = SafeAbsPath.of(mask_path, "mask_path")
    host.symlink(SafeAbsPath.of("/dev/null", "devnull"), tmp_path)
    host.rename(tmp_path, safe_mask)


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
    """Return status for all containers in a service.

    Uses batched ``systemctl show`` and ``systemctl status`` calls — one
    subprocess per type for the entire compartment instead of one per container.
    """
    if not container_names:
        return []

    units = [SafeUnitName.of(f"{n}.service", "unit_name") for n in container_names]
    all_props = _batch_unit_props(service_id, units)
    all_text = _batch_unit_text(service_id, units)

    statuses = []
    for name, unit in zip(container_names, units, strict=True):
        props = all_props.get(unit, {})
        # UnitFileState from systemctl show is authoritative — no filesystem
        # checks needed.  Quadlet-generated units report "enabled" (linked);
        # masked units report "masked".
        raw_ufs = props.get("UnitFileState", "")
        unit_file_state = "disabled" if raw_ufs == "masked" else "enabled"
        statuses.append(
            {
                "container": name,
                "unit": unit,
                "active_state": props.get("ActiveState", "unknown"),
                "sub_state": props.get("SubState", ""),
                "load_state": props.get("LoadState", "not-found"),
                "unit_file_state": unit_file_state,
                "main_pid": props.get("MainPID", ""),
                "status_text": all_text.get(unit, ""),
            }
        )
    return statuses


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
    # Create each directory level with correct ownership via admin sudo.
    for subpath in [
        ".config",
        ".config/systemd",
        ".config/systemd/user",
        ".config/systemd/user/timers.target.wants",
    ]:
        d = SafeAbsPath.of(os.path.join(home, subpath), "wants_dir_part")
        host.run(
            ["install", "-d", "-o", owner, "-g", owner, "-m", "0700", str(d)],
            admin=True,
            check=True,
            capture_output=True,
            text=True,
        )
    # Find the system unit file to symlink to
    unit_path = f"/usr/lib/systemd/user/{_AUTO_UPDATE_TIMER}"
    if not os.path.exists(unit_path):
        unit_path = f"/lib/systemd/user/{_AUTO_UPDATE_TIMER}"
    if os.path.exists(unit_path):
        link = SafeAbsPath.of(f"{wants_dir}/{_AUTO_UPDATE_TIMER}", "timer_link")
        host.symlink(SafeAbsPath.of(unit_path, "timer_unit"), link)
    _run(service_id, "/usr/bin/systemctl", "--user", "daemon-reload")
    _run(service_id, "/usr/bin/systemctl", "--user", "start", _AUTO_UPDATE_TIMER)
    invalidate_unit_cache(service_id, _AUTO_UPDATE_TIMER)
    logger.info("Enabled podman-auto-update.timer for %s", service_id)


@host.audit("AUTO_UPDATE_DISABLE", lambda sid, *_: sid)
@sanitized.enforce
def disable_auto_update_timer(service_id: SafeSlug) -> None:
    """Disable and stop the podman-auto-update.timer for a compartment user."""
    home = get_home(service_id)
    link = SafeAbsPath.of(
        f"{home}/.config/systemd/user/timers.target.wants/{_AUTO_UPDATE_TIMER}", "timer_link"
    )
    _run(service_id, "/usr/bin/systemctl", "--user", "stop", _AUTO_UPDATE_TIMER)
    with suppress(FileNotFoundError):
        host.unlink(link)
    _run(service_id, "/usr/bin/systemctl", "--user", "daemon-reload")
    invalidate_unit_cache(service_id, _AUTO_UPDATE_TIMER)
    logger.info("Disabled podman-auto-update.timer for %s", service_id)


@sanitized.enforce
def get_agent_status(service_id: SafeSlug) -> str:
    """Return the ActiveState of the monitoring agent unit for a compartment.

    Returns 'active', 'inactive', 'failed', 'not-found', etc.
    Returns 'unknown' if the compartment user does not exist.
    """
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
    full_name = f"{service_id}-{container_name}"
    cmd = _base_cmd(service_id) + ["/usr/bin/podman", "inspect", full_name]
    result = host.run(cmd, admin=True, cwd="/", capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        return {}
    try:
        items = json.loads(result.stdout or "[]")
        return items[0] if items else {}
    except (json.JSONDecodeError, IndexError):
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
        timeout=15,  # read-only; slightly longer for inspect output
    )
    return result.stdout or result.stderr


async def is_app_service_active() -> bool:
    """Check whether quadletman.service exists as a systemd unit."""
    check = await aio_subprocess.create_subprocess_exec(
        "/usr/bin/systemctl",
        "cat",
        "quadletman.service",
        stdout=aio_subprocess.DEVNULL,
        stderr=aio_subprocess.DEVNULL,
    )
    return await check.wait() == 0


@sanitized.enforce
async def stream_app_journal():
    """Async generator yielding live journal lines for the quadletman service."""
    proc = await aio_subprocess.create_subprocess_exec(
        "journalctl",
        "-u",
        "quadletman",
        "--no-pager",
        "--output=short-iso",
        "-f",
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
        "/usr/bin/podman",
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

    Called from the restart-agent route (authenticated session required).
    Creates ~/.config/systemd/user/ with correct ownership via admin sudo.
    """
    agent_bin = shutil.which("quadletman-agent") or os.path.join(
        os.path.dirname(sys.executable), "quadletman-agent"
    )
    if not os.path.isfile(agent_bin):
        logger.warning(
            "quadletman-agent not found — cannot restore agent for %s",
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

    username = _username(service_id)
    pw = pwd.getpwnam(username)
    for subpath in [".config", ".config/systemd", ".config/systemd/user"]:
        d = SafeAbsPath.of(os.path.join(home, subpath), "systemd_dir_part")
        host.run(
            ["install", "-d", "-o", username, "-g", username, "-m", "0700", str(d)],
            admin=True,
            check=True,
            capture_output=True,
            text=True,
        )
    host.write_text(SafeAbsPath.of(unit_path, "agent_unit_path"), content, pw.pw_uid, pw.pw_gid)

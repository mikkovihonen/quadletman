"""Linux user management for quadletman service accounts."""

import fcntl
import grp
import json
import logging
import os
import pwd
import shutil
import subprocess
import time
from contextlib import suppress

from ..config import settings
from ..models import sanitized
from ..models.sanitized import (
    SafeAbsPath,
    SafeMultilineStr,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    log_safe,
)
from ..podman import get_features, get_log_drivers, get_network_drivers, get_volume_drivers
from ..utils import cmd_token
from . import host

logger = logging.getLogger(__name__)

_SUBID_RANGE_SIZE = 65536

_FUSE_OVERLAYFS_CANDIDATES = [
    "/usr/bin/fuse-overlayfs",
    "/usr/local/bin/fuse-overlayfs",
    "/bin/fuse-overlayfs",
]


_SUDO, _U, _ENV = cmd_token("sudo"), cmd_token("-u"), cmd_token("env")
_INSTALL = cmd_token("install")
_CHOWN, _R = cmd_token("chown"), cmd_token("-R")


@sanitized.enforce
def _find_fuse_overlayfs() -> str | None:
    """Return the path to fuse-overlayfs if installed, else None."""
    for candidate in _FUSE_OVERLAYFS_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    # Also try PATH
    found = shutil.which("fuse-overlayfs")
    return found or None


@sanitized.enforce
def _username(service_id: SafeSlug) -> SafeStr:
    return SafeStr.trusted(f"{settings.service_user_prefix}{service_id}", "prefix+slug")


@sanitized.enforce
def _groupname(service_id: SafeSlug) -> SafeStr:
    """Shared group for service user and all helper users."""
    return SafeStr.trusted(f"{settings.service_user_prefix}{service_id}", "prefix+slug")


@sanitized.enforce
def _helper_username(service_id: SafeSlug, container_uid: int) -> SafeStr:
    return SafeStr.trusted(
        f"{settings.service_user_prefix}{service_id}-{container_uid}", "prefix+slug+int"
    )


@sanitized.enforce
def user_exists(service_id: SafeSlug) -> bool:
    try:
        pwd.getpwnam(_username(service_id))
        return True
    except KeyError:
        return False


@sanitized.enforce
def get_uid(service_id: SafeSlug) -> int:
    return pwd.getpwnam(_username(service_id)).pw_uid


@sanitized.enforce
def get_home(service_id: SafeSlug) -> str:
    return pwd.getpwnam(_username(service_id)).pw_dir


@sanitized.enforce
def get_compartment_podman_info(service_id: SafeSlug) -> dict:
    """Return 'podman info' as the compartment user (qm-{id}), not root.

    This reflects the compartment's own storage, image cache, and runtime paths.
    Returns an empty dict if the user does not exist or podman fails.
    """
    try:
        username = _username(service_id)
        uid = get_uid(service_id)
        home = get_home(service_id)
        result = subprocess.run(
            [
                "sudo",
                "-u",
                username,
                "env",
                f"HOME={home}",
                f"XDG_RUNTIME_DIR=/run/user/{uid}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                "podman",
                "info",
                "--format",
                "json",
            ],
            cwd="/",
            capture_output=True,
            text=True,
            timeout=10,  # read-only; short timeout for podman info query
        )
        info = json.loads(result.stdout.strip())
        if not isinstance(info, dict):
            raise ValueError("unexpected format")
        return info
    except Exception as exc:
        logger.warning("Could not query podman info for %s: %s", log_safe(service_id), exc)
        return {}


@sanitized.enforce
def get_compartment_log_drivers(service_id: SafeSlug) -> list[str]:
    """Return available log driver names from the compartment user's podman info.

    Falls back to root podman log drivers if unavailable.
    """
    info = get_compartment_podman_info(service_id)
    plugins = info.get("plugins", {}) if info else {}
    raw = plugins.get("log") or []
    if isinstance(raw, list) and raw:
        return sorted(raw)
    return get_log_drivers()


@sanitized.enforce
def get_compartment_drivers(service_id: SafeSlug) -> tuple[list[str], list[str]]:
    """Return (net_drivers, vol_drivers) from the compartment user's podman info.

    Falls back to root podman drivers if the compartment user does not exist or
    podman info cannot be obtained.
    """
    info = get_compartment_podman_info(service_id)
    plugins = info.get("plugins", {}) if info else {}

    # Network drivers — always ensure "bridge" is first
    raw_net = plugins.get("network") or []
    if isinstance(raw_net, list) and raw_net:
        net = [d for d in raw_net if d != "bridge"]
        net_drivers: list[str] = ["bridge"] + sorted(net)
    else:
        net_drivers = get_network_drivers()

    # Volume drivers — always ensure "local" is first
    raw_vol = plugins.get("volume") or []
    if isinstance(raw_vol, list) and raw_vol:
        vol = [d for d in raw_vol if d != "local"]
        vol_drivers: list[str] = ["local"] + sorted(vol)
    else:
        vol_drivers = get_volume_drivers()

    return net_drivers, vol_drivers


@sanitized.enforce
def get_user_info(service_id: SafeSlug) -> dict:
    """Return uid, gid, subuid_start, subgid_start for the service user, or None values if unavailable."""
    try:
        pw = pwd.getpwnam(_username(service_id))
        uid, gid = pw.pw_uid, pw.pw_gid
    except KeyError:
        return {"uid": None, "gid": None, "subuid_start": None, "subgid_start": None}
    return {
        "uid": uid,
        "gid": gid,
        "subuid_start": get_subid_start(service_id, SafeStr.trusted("uid", "hardcoded")),
        "subgid_start": get_subid_start(service_id, SafeStr.trusted("gid", "hardcoded")),
    }


@host.audit("USER_CREATE", lambda sid, *_: sid)
@sanitized.enforce
def create_service_user(service_id: SafeSlug) -> int:
    """Create qm-{service_id} system user. Returns uid. Idempotent."""
    username = _username(service_id)
    if user_exists(service_id):
        logger.info("User %s already exists, skipping creation", username)
        return get_uid(service_id)

    # Create shared group first (same name as user) then add user to it
    groupname = _groupname(service_id)
    _ensure_group(groupname)
    host.run(
        [
            cmd_token("useradd"),
            cmd_token("--system"),
            cmd_token("--create-home"),
            cmd_token("--shell"),
            cmd_token("/bin/false"),
            cmd_token("--gid"),
            groupname,
            cmd_token("--comment"),
            cmd_token(f"quadletman service {service_id}"),
            username,
        ],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )
    uid = get_uid(service_id)
    logger.info("Created user %s (uid=%d)", username, uid)
    _setup_subuid_subgid(username)
    # Add the new user to the app process's group so the agent can connect
    # to the agent API Unix socket (0o660, owned by the app user's group).
    if os.getuid() != 0:
        app_group = grp.getgrgid(os.getgid()).gr_name
        host.run(
            [cmd_token("usermod"), cmd_token("-aG"), cmd_token(app_group), username],
            admin=True,
            capture_output=True,
            text=True,
        )
        logger.info("Added %s to group %s for agent socket access", username, app_group)
    return uid


@host.audit("GROUP_ENSURE", lambda gn, *_: gn)
@sanitized.enforce
def _ensure_group(groupname: SafeStr) -> int:
    """Create group if it does not exist. Returns gid."""
    try:
        return grp.getgrnam(groupname).gr_gid
    except KeyError:
        pass
    host.run(
        [cmd_token("groupadd"), cmd_token("--system"), groupname],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )
    gid = grp.getgrnam(groupname).gr_gid
    logger.info("Created group %s (gid=%d)", groupname, gid)
    return gid


@sanitized.enforce
def get_service_gid(service_id: SafeSlug) -> int:
    """Return the GID of the shared service group."""
    return grp.getgrnam(_groupname(service_id)).gr_gid


@host.audit("HELPER_USER_CREATE", lambda sid, uid, *_: f"{sid}+{uid}")
@sanitized.enforce
def create_helper_user(service_id: SafeSlug, container_uid: int) -> int:
    """Create qm-{service_id}-{container_uid} system user with UID = subuid_start + container_uid.

    The host UID is anchored inside the service user's subUID range so that
    Podman's newuidmap accepts the UIDMap entry.  Returns the host UID. Idempotent.
    """
    helper = _helper_username(service_id, container_uid)
    groupname = _groupname(service_id)
    try:
        return pwd.getpwnam(helper).pw_uid
    except KeyError:
        pass

    subuid_start = get_subid_start(service_id, SafeStr.trusted("uid", "hardcoded"))
    if subuid_start is None:
        raise RuntimeError(
            f"Cannot create helper user for {service_id}: no subUID range allocated yet"
        )
    host_uid = subuid_start + container_uid

    host.run(
        [
            cmd_token("useradd"),
            cmd_token("--uid"),
            cmd_token(str(host_uid)),
            cmd_token("--no-create-home"),
            cmd_token("--shell"),
            cmd_token("/bin/false"),
            cmd_token("--gid"),
            groupname,
            cmd_token("--comment"),
            cmd_token(f"quadletman helper uid={container_uid} for {service_id}"),
            helper,
        ],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info(
        "Created helper user %s (host_uid=%d = subuid_start+%d)",
        helper,
        host_uid,
        container_uid,
    )
    return host_uid


@sanitized.enforce
def get_helper_uid(service_id: SafeSlug, container_uid: int) -> int | None:
    """Return the host UID for the given container UID helper user, or None."""
    try:
        return pwd.getpwnam(_helper_username(service_id, container_uid)).pw_uid
    except KeyError:
        return None


@sanitized.enforce
def list_helper_users(service_id: SafeSlug) -> list[dict]:
    """Return info about all helper users for this service.

    Each entry: {username, container_uid, host_uid}
    """
    base_prefix = f"{settings.service_user_prefix}{service_id}-"
    result = []
    for pw in pwd.getpwall():
        if pw.pw_name.startswith(base_prefix):
            try:
                container_uid = int(pw.pw_name[len(base_prefix) :])
            except ValueError:
                continue
            result.append(
                {
                    "username": pw.pw_name,
                    "container_uid": container_uid,
                    "host_uid": pw.pw_uid,
                }
            )
    return sorted(result, key=lambda x: x["container_uid"])


@sanitized.enforce
def sync_helper_users(service_id: SafeSlug, container_uids: list[int]) -> None:
    """Ensure helper users exist for all given container UIDs (skip 0 — that's the service user).
    Delete helper users for UIDs no longer in the list."""
    wanted = {uid for uid in container_uids if uid != 0}

    # Create missing helpers
    for uid in wanted:
        create_helper_user(service_id, uid)

    # Delete helpers no longer needed
    base_prefix = f"{settings.service_user_prefix}{service_id}-"
    for pw in pwd.getpwall():
        if pw.pw_name.startswith(base_prefix):
            try:
                existing_uid = int(pw.pw_name[len(base_prefix) :])
            except ValueError:
                continue
            if existing_uid not in wanted:
                _delete_helper_user(SafeStr.of(pw.pw_name, "pw:pw_name"))


@host.audit("HELPER_USER_DELETE", lambda u, *_: u)
@sanitized.enforce
def _delete_helper_user(username: SafeStr) -> None:
    host.run(
        [cmd_token("userdel"), username],
        admin=True,
        check=False,
        capture_output=True,
        text=True,
    )
    logger.info("Deleted helper user %s", username)


@host.audit("HELPER_USERS_DELETE_ALL", lambda sid, *_: sid)
@sanitized.enforce
def delete_all_helper_users(service_id: SafeSlug) -> None:
    """Delete all qm-{service_id}-N helper users."""
    base_prefix = f"{settings.service_user_prefix}{service_id}-"
    for pw in pwd.getpwall():
        if pw.pw_name.startswith(base_prefix):
            try:
                int(pw.pw_name[len(base_prefix) :])
            except ValueError:
                continue
            _delete_helper_user(SafeStr.of(pw.pw_name, "pw:pw_name"))


@host.audit("GROUP_DELETE", lambda sid, *_: sid)
@sanitized.enforce
def delete_service_group(service_id: SafeSlug) -> None:
    """Delete the shared service group. Call after all users are removed."""
    groupname = _groupname(service_id)
    try:
        grp.getgrnam(groupname)
    except KeyError:
        return
    host.run(
        [cmd_token("groupdel"), groupname],
        admin=True,
        check=False,
        capture_output=True,
        text=True,
    )
    logger.info("Deleted group %s", groupname)


@sanitized.enforce
def _next_subid_start(path: SafeAbsPath) -> int:
    """Return the first unoccupied subID start after all existing ranges in path."""
    highest_end = 100000  # minimum start
    try:
        with open(path) as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) == 3:
                    try:
                        start, count = int(parts[1]), int(parts[2])
                        highest_end = max(highest_end, start + count)
                    except ValueError:
                        pass
    except FileNotFoundError:
        pass
    return highest_end


@sanitized.enforce
def _setup_subuid_subgid(username: SafeStr) -> None:
    """Add subuid/subgid ranges for rootless Podman user namespace mapping.

    Each file is handled independently and a non-overlapping range is allocated
    by scanning existing entries.  Skips if an entry already exists.

    A lock file is used to prevent two concurrent service creations from
    allocating overlapping subUID/subGID ranges.
    """
    lock_dir = os.path.dirname(str(settings.db_path))
    lock_path = os.path.join(lock_dir, ".subid_lock")
    host.makedirs(SafeAbsPath.of(lock_dir, "lock_dir"), exist_ok=True)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            for path, usermod_flag in (
                ("/etc/subuid", cmd_token("--add-subuids")),
                ("/etc/subgid", cmd_token("--add-subgids")),
            ):
                try:
                    with open(path) as _f:
                        existing = _f.read()
                except FileNotFoundError:
                    existing = ""
                if f"{username}:" in existing:
                    continue
                start = _next_subid_start(SafeAbsPath.trusted(path, "hardcoded"))
                end = start + _SUBID_RANGE_SIZE - 1
                result = host.run(
                    [cmd_token("usermod"), usermod_flag, cmd_token(f"{start}-{end}"), username],
                    admin=True,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    logger.info(
                        "Configured %s for %s via usermod (range %d-%d)", path, username, start, end
                    )
                    continue
                # usermod flag may not be available on all distros — write directly
                host.append_text(
                    SafeAbsPath.trusted(path, "hardcoded"),
                    f"{username}:{start}:{_SUBID_RANGE_SIZE}\n",
                )
                logger.info(
                    "Appended %s entry for %s (range %d+%d)",
                    path,
                    username,
                    start,
                    _SUBID_RANGE_SIZE,
                )
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


@sanitized.enforce
def get_subid_start(
    service_id: SafeSlug, kind: SafeStr = SafeStr.trusted("uid", "default")
) -> int | None:
    """Return the first subUID (kind='uid') or subGID (kind='gid') for the service user, or None."""
    username = _username(service_id)
    path = "/etc/subuid" if kind == "uid" else "/etc/subgid"
    try:
        with open(path) as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) == 3 and parts[0] == username:
                    return int(parts[1])
    except Exception:
        pass
    return None


@sanitized.enforce
def _remove_subuid_subgid(username: SafeStr) -> None:
    """Remove subuid/subgid entries for the given username."""
    for path in ("/etc/subuid", "/etc/subgid"):
        try:
            with open(path) as f:
                lines = f.readlines()
        except FileNotFoundError:
            continue
        filtered = [line for line in lines if not line.startswith(f"{username}:")]
        if len(filtered) == len(lines):
            continue
        host.write_lines(SafeAbsPath.trusted(path, "hardcoded"), filtered)
        logger.info("Removed %s entry for %s", path, username)


@host.audit("USER_DELETE", lambda sid, *_: sid)
@sanitized.enforce
def delete_service_user(service_id: SafeSlug) -> None:
    """Delete qm-{service_id} user, their home directory, and subuid/subgid entries."""
    username = _username(service_id)
    if not user_exists(service_id):
        return
    try:
        home = get_home(service_id)
    except KeyError:
        home = None
    uid = None
    with suppress(KeyError):
        uid = get_uid(service_id)

    # 1. Stop all systemd --user services
    if uid is not None:
        host.run(
            [
                _SUDO,
                _U,
                username,
                _ENV,
                cmd_token(f"XDG_RUNTIME_DIR=/run/user/{uid}"),
                cmd_token(f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"),
                cmd_token("systemctl"),
                cmd_token("--user"),
                cmd_token("stop"),
                cmd_token("--all"),
            ],
            admin=True,
            cwd="/",
            check=False,
            capture_output=True,
        )
        logger.info("Stopped all systemd --user units for %s", username)

    # 2. Disable linger so the user session won't be restarted
    host.run(
        [cmd_token("loginctl"), cmd_token("disable-linger"), username],
        admin=True,
        check=False,
        capture_output=True,
    )
    logger.info("Disabled linger for %s", username)

    # 3. Terminate the login session
    host.run(
        [cmd_token("loginctl"), cmd_token("terminate-user"), username],
        admin=True,
        check=False,
        capture_output=True,
    )

    # 4. Force-kill any remaining processes owned by this user
    if uid is not None:
        host.run(
            [cmd_token("pkill"), cmd_token("-9"), _U, cmd_token(str(uid))],
            admin=True,
            check=False,
            capture_output=True,
        )
        logger.info("Force-killed remaining processes for uid %d (%s)", uid, username)

    _remove_subuid_subgid(username)
    result = host.run(
        [cmd_token("userdel"), cmd_token("--remove"), username],
        admin=True,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning(
            "userdel %s exited %d: %s", username, result.returncode, result.stderr.strip()
        )

    # 5. Explicitly remove home dir in case userdel left it behind
    if home and os.path.isdir(home):
        host.rmtree(SafeAbsPath.of(home, "home"), ignore_errors=True)
        logger.info("Removed home directory %s", home)
    logger.info("Deleted user %s", username)

    # 6. Delete helper users and shared group
    delete_all_helper_users(service_id)
    delete_service_group(service_id)


@host.audit("CHOWN", lambda sid, path, *_: f"{sid} {path}")
@sanitized.enforce
def chown_to_service_user(service_id: SafeSlug, path: SafeAbsPath) -> None:
    """Recursively chown path to the service user."""
    username = _username(service_id)
    host.run(
        [_CHOWN, _R, cmd_token(f"{username}:{username}"), path],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )


@host.audit("WRITE_CONTAINERFILE", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def write_managed_containerfile(
    service_id: SafeSlug, container_name: SafeResourceName, content: SafeMultilineStr
) -> str:
    """Write Containerfile content to the service user's home directory.

    Returns the build context directory path.
    """
    username = _username(service_id)
    pw = pwd.getpwnam(username)
    builds_dir = os.path.join(pw.pw_dir, "builds", container_name)
    host.run(
        [
            _INSTALL,
            cmd_token("-d"),
            cmd_token("-o"),
            username,
            cmd_token("-g"),
            username,
            cmd_token("-m"),
            cmd_token("0700"),
            SafeAbsPath.of(builds_dir, "builds_dir"),
        ],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )
    cf_path = os.path.join(builds_dir, "Containerfile")
    host.write_text(SafeAbsPath.of(cf_path, "cf_path"), content, pw.pw_uid, pw.pw_gid)
    logger.info("Wrote managed Containerfile for %s/%s", service_id, container_name)
    return builds_dir


@host.audit("WRITE_ENVFILE", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def write_envfile(
    service_id: SafeSlug, container_name: SafeResourceName, content: SafeMultilineStr
) -> str:
    """Write an environment file for a container to the service user's env directory.

    Creates ``/home/qm-{id}/env/{container_name}.env`` with correct ownership.
    Returns the destination file path.
    """
    username = _username(service_id)
    pw = pwd.getpwnam(username)
    env_dir = os.path.join(pw.pw_dir, "env")
    host.makedirs(SafeAbsPath.of(env_dir, "env_dir"), mode=0o755, exist_ok=True)
    dest = os.path.join(env_dir, f"{container_name}.env")
    host.write_text(SafeAbsPath.of(dest, "envfile_dest"), content, pw.pw_uid, pw.pw_gid)
    logger.info("Wrote envfile for %s/%s", service_id, container_name)
    return dest


@host.audit("DELETE_ENVFILE", lambda sid, path, *_: f"{sid}:{path}")
@sanitized.enforce
def delete_envfile(service_id: SafeSlug, path: SafeAbsPath) -> None:
    """Delete an environment file from the service user's home directory.

    Validates that the path is within the service user's home before deleting.
    No-op if the file does not exist.
    """
    home = get_home(service_id)
    real_home = os.path.realpath(home)
    real_path = os.path.realpath(path)
    if real_path != real_home and not real_path.startswith(real_home + os.sep):
        raise ValueError("Path is outside the service user home directory")
    if os.path.isfile(real_path):
        host.unlink(SafeAbsPath.of(real_path, "envfile_path"))


@host.audit("WRITE_CONFIG_FILE", lambda sid, rt, rn, fn, *_: f"{sid}/{rt}/{rn}/{fn}")
@sanitized.enforce
def write_config_file(
    service_id: SafeSlug,
    resource_type: SafeStr,
    resource_name: SafeResourceName,
    field_name: SafeStr,
    content: SafeMultilineStr,
    ext: SafeStr = SafeStr.trusted("", "default"),
) -> str:
    """Write a config file to the service user's conf directory.

    Creates ``/home/qm-{id}/conf/{resource_type}/{resource_name}/{field_name}{ext}``
    with correct ownership.  Returns the destination file path.
    """
    username = _username(service_id)
    pw = pwd.getpwnam(username)
    conf_dir = os.path.join(pw.pw_dir, "conf", resource_type, resource_name)
    host.makedirs(SafeAbsPath.of(conf_dir, "conf_dir"), mode=0o755, exist_ok=True)
    dest = os.path.join(conf_dir, f"{field_name}{ext}")
    host.write_text(SafeAbsPath.of(dest, "config_dest"), content, pw.pw_uid, pw.pw_gid)
    logger.info(
        "Wrote config file for %s/%s/%s/%s", service_id, resource_type, resource_name, field_name
    )
    return dest


@host.audit("DELETE_CONFIG_FILE", lambda sid, path, *_: f"{sid}:{path}")
@sanitized.enforce
def delete_config_file(service_id: SafeSlug, path: SafeAbsPath) -> None:
    """Delete a config file from the service user's home directory.

    Validates that the path is within the service user's home before deleting.
    No-op if the file does not exist.
    """
    home = get_home(service_id)
    real_home = os.path.realpath(home)
    real_path = os.path.realpath(path)
    if real_path != real_home and not real_path.startswith(real_home + os.sep):
        raise ValueError("Path is outside the service user home directory")
    if os.path.isfile(real_path):
        host.unlink(SafeAbsPath.of(real_path, "config_path"))


@host.audit("CLEANUP_RESOURCE_CONFIGS", lambda sid, rt, rn, *_: f"{sid}/{rt}/{rn}")
@sanitized.enforce
def cleanup_resource_config_dir(
    service_id: SafeSlug, resource_type: SafeStr, resource_name: SafeResourceName
) -> None:
    """Remove /home/qm-{id}/conf/{resource_type}/{resource_name}/ on resource deletion.

    No-op if the service user does not exist or the directory is absent.
    """
    try:
        home = get_home(service_id)
    except KeyError:
        return  # Service user does not exist — nothing to clean up
    conf_dir = os.path.join(home, "conf", resource_type, resource_name)
    if os.path.isdir(conf_dir):
        host.rmtree(SafeAbsPath.of(conf_dir, "resource_conf_dir"), ignore_errors=True)
        logger.info("Cleaned up config dir %s", conf_dir)


@host.audit("ENSURE_QUADLET_DIR", lambda sid, *_: sid)
@sanitized.enforce
def ensure_quadlet_dir(service_id: SafeSlug) -> str:
    """Create ~/.config/containers/systemd for the service user. Returns path."""
    username = _username(service_id)
    pw = pwd.getpwnam(username)
    quadlet_dir = os.path.join(pw.pw_dir, ".config", "containers", "systemd")
    host.run(
        [
            _INSTALL,
            cmd_token("-d"),
            cmd_token("-o"),
            username,
            cmd_token("-g"),
            username,
            cmd_token("-m"),
            cmd_token("0700"),
            SafeAbsPath.of(quadlet_dir, "quadlet_dir"),
        ],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )
    return quadlet_dir


@host.audit("WRITE_STORAGE_CONF", lambda sid, *_: sid)
@sanitized.enforce
def write_storage_conf(service_id: SafeSlug) -> None:
    """Write ~/.config/containers/storage.conf for the service user.

    Forces Podman to store container images and layers in the user's home
    directory rather than /run/user/{uid} (tmpfs), which does not support
    UID-remapping overlay mounts.
    """
    username = _username(service_id)
    pw = pwd.getpwnam(username)
    home = pw.pw_dir
    config_dir = os.path.join(home, ".config", "containers")
    host.run(
        [
            _INSTALL,
            cmd_token("-d"),
            cmd_token("-o"),
            username,
            cmd_token("-g"),
            username,
            cmd_token("-m"),
            cmd_token("0700"),
            SafeAbsPath.of(config_dir, "config_dir"),
        ],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )
    graph_root = os.path.join(home, ".local", "share", "containers", "storage")
    uid = pw.pw_uid
    # runRoot is runtime state only — /run/user/{uid} (tmpfs) is fine for it;
    # only graphRoot (image layers) must be on a real filesystem.
    run_root = f"/run/user/{uid}/containers"
    storage_conf_path = os.path.join(config_dir, "storage.conf")

    # Detect fuse-overlayfs — required for rootless overlay on kernels/filesystems
    # that do not support unprivileged idmap mounts (e.g. WSL2).
    fuse_overlayfs = _find_fuse_overlayfs()
    overlay_section = ""
    if fuse_overlayfs:
        overlay_section = (
            f'\n[storage.options.overlay]\nmount_program = "{fuse_overlayfs}"\n'
            'ignore_chown_errors = "true"\n'
        )
        logger.info("fuse-overlayfs found at %s; adding to storage.conf", fuse_overlayfs)
    else:
        # WSL2 / kernels without unprivileged idmap: silently ignore chown errors
        overlay_section = '\n[storage.options.overlay]\nignore_chown_errors = "true"\n'
        logger.info("fuse-overlayfs not found; setting ignore_chown_errors=true")

    content = (
        "[storage]\n"
        'driver = "overlay"\n'
        f'graphRoot = "{graph_root}"\n'
        f'runRoot = "{run_root}"\n' + overlay_section
    )
    host.write_text(
        SafeAbsPath.of(storage_conf_path, "storage_conf_path"), content, pw.pw_uid, pw.pw_gid
    )
    logger.info("Wrote storage.conf for %s (graphRoot=%s)", username, graph_root)


@host.audit("WRITE_CONTAINERS_CONF", lambda sid, *_: sid)
@sanitized.enforce
def write_containers_conf(service_id: SafeSlug) -> None:
    """Write ~/.config/containers/containers.conf for the service user.

    Sets default_rootless_network_cmd = "pasta" when Podman >= 4.1 (which
    introduced pasta support), as slirp4netns is deprecated and will be
    removed in a future Podman version. pasta is the default from 5.3+.
    """
    username = _username(service_id)
    pw = pwd.getpwnam(username)
    home = pw.pw_dir
    config_dir = os.path.join(home, ".config", "containers")
    host.run(
        [
            _INSTALL,
            cmd_token("-d"),
            cmd_token("-o"),
            username,
            cmd_token("-g"),
            username,
            cmd_token("-m"),
            cmd_token("0700"),
            SafeAbsPath.of(config_dir, "config_dir"),
        ],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )
    conf_path = os.path.join(config_dir, "containers.conf")

    features = get_features()
    if features.pasta:
        content = '[network]\ndefault_rootless_network_cmd = "pasta"\n'
        logger.info("Podman >= 4.1; setting default_rootless_network_cmd=pasta for %s", username)
    else:
        # Unknown or old version: omit setting and let Podman use its built-in default.
        content = "# default_rootless_network_cmd omitted — Podman will use its built-in default\n"
        logger.info(
            "Podman version %s; omitting default_rootless_network_cmd for %s",
            features.version_str,
            username,
        )

    host.write_text(SafeAbsPath.of(conf_path, "conf_path"), content, pw.pw_uid, pw.pw_gid)
    logger.info("Wrote containers.conf for %s", username)


@sanitized.enforce
def read_containers_conf(service_id: SafeSlug) -> str | None:
    """Read the containers.conf for the service user, or None if not present."""
    home = get_home(service_id)
    path = SafeAbsPath.of(
        os.path.join(home, ".config", "containers", "containers.conf"), "containers_conf"
    )
    return host.read_text(path, owner=_username(service_id))


@sanitized.enforce
def read_storage_conf(service_id: SafeSlug) -> str | None:
    """Read the storage.conf for the service user, or None if not present."""
    home = get_home(service_id)
    path = SafeAbsPath.of(
        os.path.join(home, ".config", "containers", "storage.conf"), "storage_conf"
    )
    return host.read_text(path, owner=_username(service_id))


@host.audit("PODMAN_RESET", lambda sid, *_: sid)
@sanitized.enforce
def podman_reset(service_id: SafeSlug) -> None:
    """Run `podman system reset --force` as the service user.

    Wipes all containers, images and storage so that the next pull starts
    fresh with the current storage.conf (driver + fuse-overlayfs).  Safe to
    call during initial service setup because there is nothing to preserve yet.
    """
    username = _username(service_id)
    uid = get_uid(service_id)
    home = get_home(service_id)
    result = host.run(
        [
            _SUDO,
            _U,
            username,
            _ENV,
            cmd_token(f"HOME={home}"),
            cmd_token(f"XDG_RUNTIME_DIR=/run/user/{uid}"),
            cmd_token(f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"),
            cmd_token("podman"),
            cmd_token("system"),
            cmd_token("reset"),
            cmd_token("--force"),
        ],
        admin=True,
        cwd="/",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("podman system reset failed for %s: %s", username, result.stderr.strip())
    else:
        logger.info("podman system reset completed for %s", username)


@host.audit("PODMAN_MIGRATE", lambda sid, *_: sid)
@sanitized.enforce
def podman_migrate(service_id: SafeSlug) -> None:
    """Run `podman system migrate` as the service user.

    Must be called after enable_linger() so that /run/user/{uid} exists.
    This initialises Podman's overlay storage with the correct subUID/subGID ranges.
    HOME must be set explicitly — without it sudo drops HOME and Podman falls back
    to /tmp/containers-user-{uid}/ which may not support UID remapping.
    """
    username = _username(service_id)
    uid = get_uid(service_id)
    home = get_home(service_id)
    result = host.run(
        [
            _SUDO,
            _U,
            username,
            _ENV,
            cmd_token(f"HOME={home}"),
            cmd_token(f"XDG_RUNTIME_DIR=/run/user/{uid}"),
            cmd_token(f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"),
            cmd_token("podman"),
            cmd_token("system"),
            cmd_token("migrate"),
        ],
        admin=True,
        cwd="/",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("podman system migrate failed for %s: %s", username, result.stderr.strip())
    else:
        logger.info("podman system migrate completed for %s", username)


@host.audit("LINGER_ENABLE", lambda sid, *_: sid)
@sanitized.enforce
def enable_linger(service_id: SafeSlug) -> None:
    username = _username(service_id)
    host.run(
        [cmd_token("loginctl"), cmd_token("enable-linger"), username],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info("Enabled linger for %s", username)
    _wait_for_runtime_dir(service_id)


@host.audit("LINGER_DISABLE", lambda sid, *_: sid)
@sanitized.enforce
def disable_linger(service_id: SafeSlug) -> None:
    username = _username(service_id)
    host.run(
        [cmd_token("loginctl"), cmd_token("disable-linger"), username],
        admin=True,
        check=False,
        capture_output=True,
        text=True,
    )
    logger.info("Disabled linger for %s", username)


@sanitized.enforce
def linger_enabled(service_id: SafeSlug) -> bool:
    username = _username(service_id)
    return os.path.exists(f"/var/lib/systemd/linger/{username}")


@sanitized.enforce
def _auth_file(service_id: SafeSlug) -> str:
    """Return the persistent auth.json path for the service user."""
    home = get_home(service_id)
    return os.path.join(home, ".config", "containers", "auth.json")


@host.audit("REGISTRY_LOGIN", lambda sid, reg, *_: f"{sid} {reg}")
@sanitized.enforce
def registry_login(
    service_id: SafeSlug, registry: SafeStr, username: SafeStr, password: SafeStr
) -> None:
    """Run `podman login` as the service user. Password is passed via stdin only.

    Uses --authfile to write to the persistent location instead of XDG_RUNTIME_DIR
    (tmpfs) which would be lost on reboot.
    """
    comp_username = _username(service_id)
    home = get_home(service_id)
    authfile = _auth_file(service_id)
    result = host.run(
        [
            _SUDO,
            _U,
            comp_username,
            _ENV,
            cmd_token(f"HOME={home}"),
            cmd_token("podman"),
            cmd_token("login"),
            cmd_token("--authfile"),
            SafeAbsPath.of(authfile, "authfile"),
            cmd_token("--username"),
            username,
            cmd_token("--password-stdin"),
            registry,
        ],
        admin=True,
        input=password,
        cwd="/",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    logger.info("Registry login to %s succeeded for service %s", registry, service_id)


@host.audit("REGISTRY_LOGOUT", lambda sid, reg, *_: f"{sid} {reg}")
@sanitized.enforce
def registry_logout(service_id: SafeSlug, registry: SafeStr) -> None:
    """Run `podman logout` as the service user."""
    comp_username = _username(service_id)
    home = get_home(service_id)
    authfile = _auth_file(service_id)
    result = host.run(
        [
            _SUDO,
            _U,
            comp_username,
            _ENV,
            cmd_token(f"HOME={home}"),
            cmd_token("podman"),
            cmd_token("logout"),
            cmd_token("--authfile"),
            SafeAbsPath.of(authfile, "authfile"),
            registry,
        ],
        admin=True,
        cwd="/",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    logger.info("Registry logout from %s succeeded for service %s", registry, service_id)


@sanitized.enforce
def list_registry_logins(service_id: SafeSlug) -> list[str]:
    """Return list of registries the service user is currently logged into."""
    home = get_home(service_id)
    auth_path = SafeAbsPath.of(
        os.path.join(home, ".config", "containers", "auth.json"), "auth_json"
    )
    content = host.read_text(auth_path, owner=_username(service_id))
    if content is None:
        return []
    try:
        data = json.loads(content)
        return list(data.get("auths", {}).keys())
    except (json.JSONDecodeError, KeyError):
        return []


@sanitized.enforce
def _wait_for_runtime_dir(service_id: SafeSlug, timeout: float = 10.0) -> None:
    """Wait for /run/user/{uid} to appear after enabling linger."""
    uid = get_uid(service_id)
    runtime_dir = f"/run/user/{uid}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.isdir(runtime_dir):
            return
        time.sleep(0.5)
    logger.warning("Runtime dir %s did not appear within %ss", runtime_dir, timeout)

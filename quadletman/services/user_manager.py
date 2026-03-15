"""Linux user management for quadletman service accounts."""

import fcntl
import grp
import logging
import os
import pwd
import subprocess
import time
from contextlib import suppress

from ..config import settings

logger = logging.getLogger(__name__)

_FUSE_OVERLAYFS_CANDIDATES = [
    "/usr/bin/fuse-overlayfs",
    "/usr/local/bin/fuse-overlayfs",
    "/bin/fuse-overlayfs",
]


def _find_fuse_overlayfs() -> str | None:
    """Return the path to fuse-overlayfs if installed, else None."""
    import shutil

    for candidate in _FUSE_OVERLAYFS_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    # Also try PATH
    found = shutil.which("fuse-overlayfs")
    return found or None


def _username(service_id: str) -> str:
    return f"{settings.service_user_prefix}{service_id}"


def _groupname(service_id: str) -> str:
    """Shared group for service user and all helper users."""
    return f"{settings.service_user_prefix}{service_id}"


def _helper_username(service_id: str, container_uid: int) -> str:
    return f"{settings.service_user_prefix}{service_id}-{container_uid}"


def user_exists(service_id: str) -> bool:
    try:
        pwd.getpwnam(_username(service_id))
        return True
    except KeyError:
        return False


def get_uid(service_id: str) -> int:
    return pwd.getpwnam(_username(service_id)).pw_uid


def get_home(service_id: str) -> str:
    return pwd.getpwnam(_username(service_id)).pw_dir


def get_compartment_podman_info(service_id: str) -> dict:
    """Return 'podman info' as the compartment user (qm-{id}), not root.

    This reflects the compartment's own storage, image cache, and runtime paths.
    Returns an empty dict if the user does not exist or podman fails.
    """
    import json as _json

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
            timeout=10,
        )
        info = _json.loads(result.stdout.strip())
        if not isinstance(info, dict):
            raise ValueError("unexpected format")
        return info
    except Exception as exc:
        logger.warning("Could not query podman info for %s: %s", service_id, exc)
        return {}


def get_compartment_log_drivers(service_id: str) -> list[str]:
    """Return available log driver names from the compartment user's podman info.

    Falls back to root podman log drivers if unavailable.
    """
    from quadletman.podman_version import get_log_drivers

    info = get_compartment_podman_info(service_id)
    plugins = info.get("plugins", {}) if info else {}
    raw = plugins.get("log") or []
    if isinstance(raw, list) and raw:
        return sorted(raw)
    return get_log_drivers()


def get_compartment_drivers(service_id: str) -> tuple[list[str], list[str]]:
    """Return (net_drivers, vol_drivers) from the compartment user's podman info.

    Falls back to root podman drivers if the compartment user does not exist or
    podman info cannot be obtained.
    """
    from quadletman.podman_version import get_network_drivers, get_volume_drivers

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


def get_user_info(service_id: str) -> dict:
    """Return uid, gid, subuid_start, subgid_start for the service user, or None values if unavailable."""
    try:
        pw = pwd.getpwnam(_username(service_id))
        uid, gid = pw.pw_uid, pw.pw_gid
    except KeyError:
        return {"uid": None, "gid": None, "subuid_start": None, "subgid_start": None}
    return {
        "uid": uid,
        "gid": gid,
        "subuid_start": get_subid_start(service_id, "uid"),
        "subgid_start": get_subid_start(service_id, "gid"),
    }


def create_service_user(service_id: str) -> int:
    """Create qm-{service_id} system user. Returns uid. Idempotent."""
    username = _username(service_id)
    if user_exists(service_id):
        logger.info("User %s already exists, skipping creation", username)
        return get_uid(service_id)

    # Create shared group first (same name as user) then add user to it
    groupname = _groupname(service_id)
    _ensure_group(groupname)
    subprocess.run(
        [
            "useradd",
            "--system",
            "--create-home",
            "--shell",
            "/bin/false",
            "--gid",
            groupname,
            "--comment",
            f"quadletman service {service_id}",
            username,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    uid = get_uid(service_id)
    logger.info("Created user %s (uid=%d)", username, uid)
    _setup_subuid_subgid(username)
    return uid


def _ensure_group(groupname: str) -> int:
    """Create group if it does not exist. Returns gid."""
    try:
        return grp.getgrnam(groupname).gr_gid
    except KeyError:
        pass
    subprocess.run(
        ["groupadd", "--system", groupname],
        check=True,
        capture_output=True,
        text=True,
    )
    gid = grp.getgrnam(groupname).gr_gid
    logger.info("Created group %s (gid=%d)", groupname, gid)
    return gid


def get_service_gid(service_id: str) -> int:
    """Return the GID of the shared service group."""
    return grp.getgrnam(_groupname(service_id)).gr_gid


def create_helper_user(service_id: str, container_uid: int) -> int:
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

    subuid_start = get_subid_start(service_id, "uid")
    if subuid_start is None:
        raise RuntimeError(
            f"Cannot create helper user for {service_id}: no subUID range allocated yet"
        )
    host_uid = subuid_start + container_uid

    subprocess.run(
        [
            "useradd",
            "--uid",
            str(host_uid),
            "--no-create-home",
            "--shell",
            "/bin/false",
            "--gid",
            groupname,
            "--comment",
            f"quadletman helper uid={container_uid} for {service_id}",
            helper,
        ],
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


def get_helper_uid(service_id: str, container_uid: int) -> int | None:
    """Return the host UID for the given container UID helper user, or None."""
    try:
        return pwd.getpwnam(_helper_username(service_id, container_uid)).pw_uid
    except KeyError:
        return None


def list_helper_users(service_id: str) -> list[dict]:
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


def sync_helper_users(service_id: str, container_uids: list[int]) -> None:
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
                _delete_helper_user(pw.pw_name)


def _delete_helper_user(username: str) -> None:
    subprocess.run(
        ["userdel", username],
        check=False,
        capture_output=True,
        text=True,
    )
    logger.info("Deleted helper user %s", username)


def delete_all_helper_users(service_id: str) -> None:
    """Delete all qm-{service_id}-N helper users."""
    base_prefix = f"{settings.service_user_prefix}{service_id}-"
    for pw in pwd.getpwall():
        if pw.pw_name.startswith(base_prefix):
            try:
                int(pw.pw_name[len(base_prefix) :])
            except ValueError:
                continue
            _delete_helper_user(pw.pw_name)


def delete_service_group(service_id: str) -> None:
    """Delete the shared service group. Call after all users are removed."""
    groupname = _groupname(service_id)
    try:
        grp.getgrnam(groupname)
    except KeyError:
        return
    subprocess.run(
        ["groupdel", groupname],
        check=False,
        capture_output=True,
        text=True,
    )
    logger.info("Deleted group %s", groupname)


_SUBID_RANGE_SIZE = 65536


def _next_subid_start(path: str) -> int:
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


def _setup_subuid_subgid(username: str) -> None:
    """Add subuid/subgid ranges for rootless Podman user namespace mapping.

    Each file is handled independently and a non-overlapping range is allocated
    by scanning existing entries.  Skips if an entry already exists.

    A lock file is used to prevent two concurrent service creations from
    allocating overlapping subUID/subGID ranges.
    """
    lock_path = "/var/lib/quadletman/.subid_lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            for path, usermod_flag in (
                ("/etc/subuid", "--add-subuids"),
                ("/etc/subgid", "--add-subgids"),
            ):
                try:
                    with open(path) as _f:
                        existing = _f.read()
                except FileNotFoundError:
                    existing = ""
                if f"{username}:" in existing:
                    continue
                start = _next_subid_start(path)
                end = start + _SUBID_RANGE_SIZE - 1
                result = subprocess.run(
                    ["usermod", usermod_flag, f"{start}-{end}", username],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    logger.info(
                        "Configured %s for %s via usermod (range %d-%d)", path, username, start, end
                    )
                    continue
                # usermod flag may not be available on all distros — write directly
                with open(path, "a") as f:
                    f.write(f"{username}:{start}:{_SUBID_RANGE_SIZE}\n")
                logger.info(
                    "Appended %s entry for %s (range %d+%d)",
                    path,
                    username,
                    start,
                    _SUBID_RANGE_SIZE,
                )
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def get_subid_start(service_id: str, kind: str = "uid") -> int | None:
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


def _remove_subuid_subgid(username: str) -> None:
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
        with open(path, "w") as f:
            f.writelines(filtered)
        logger.info("Removed %s entry for %s", path, username)


def delete_service_user(service_id: str) -> None:
    """Delete qm-{service_id} user, their home directory, and subuid/subgid entries."""
    import shutil

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
        subprocess.run(
            [
                "sudo",
                "-u",
                username,
                "env",
                f"XDG_RUNTIME_DIR=/run/user/{uid}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                "systemctl",
                "--user",
                "stop",
                "--all",
            ],
            cwd="/",
            check=False,
            capture_output=True,
        )
        logger.info("Stopped all systemd --user units for %s", username)

    # 2. Disable linger so the user session won't be restarted
    subprocess.run(["loginctl", "disable-linger", username], check=False, capture_output=True)
    logger.info("Disabled linger for %s", username)

    # 3. Terminate the login session
    subprocess.run(["loginctl", "terminate-user", username], check=False, capture_output=True)

    # 4. Force-kill any remaining processes owned by this user
    if uid is not None:
        subprocess.run(["pkill", "-9", "-u", str(uid)], check=False, capture_output=True)
        logger.info("Force-killed remaining processes for uid %d (%s)", uid, username)

    _remove_subuid_subgid(username)
    result = subprocess.run(
        ["userdel", "--remove", username],
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
        shutil.rmtree(home, ignore_errors=True)
        logger.info("Removed home directory %s", home)
    logger.info("Deleted user %s", username)

    # 6. Delete helper users and shared group
    delete_all_helper_users(service_id)
    delete_service_group(service_id)


def chown_to_service_user(service_id: str, path: str) -> None:
    """Recursively chown path to the service user."""
    username = _username(service_id)
    subprocess.run(
        ["chown", "-R", f"{username}:{username}", path],
        check=True,
        capture_output=True,
        text=True,
    )


def write_managed_containerfile(service_id: str, container_name: str, content: str) -> str:
    """Write Containerfile content to the service user's home directory.

    Returns the build context directory path.
    """
    username = _username(service_id)
    pw = pwd.getpwnam(username)
    builds_dir = os.path.join(pw.pw_dir, "builds", container_name)
    subprocess.run(
        ["install", "-d", "-o", username, "-g", username, "-m", "0700", builds_dir],
        check=True,
        capture_output=True,
        text=True,
    )
    cf_path = os.path.join(builds_dir, "Containerfile")
    with open(cf_path, "w") as f:
        f.write(content)
    os.chown(cf_path, pw.pw_uid, pw.pw_gid)
    os.chmod(cf_path, 0o600)
    logger.info("Wrote managed Containerfile for %s/%s", service_id, container_name)
    return builds_dir


def ensure_quadlet_dir(service_id: str) -> str:
    """Create ~/.config/containers/systemd for the service user. Returns path."""
    username = _username(service_id)
    pw = pwd.getpwnam(username)
    quadlet_dir = os.path.join(pw.pw_dir, ".config", "containers", "systemd")
    subprocess.run(
        ["install", "-d", "-o", username, "-g", username, "-m", "0700", quadlet_dir],
        check=True,
        capture_output=True,
        text=True,
    )
    return quadlet_dir


def write_storage_conf(service_id: str) -> None:
    """Write ~/.config/containers/storage.conf for the service user.

    Forces Podman to store container images and layers in the user's home
    directory rather than /run/user/{uid} (tmpfs), which does not support
    UID-remapping overlay mounts.
    """
    username = _username(service_id)
    pw = pwd.getpwnam(username)
    home = pw.pw_dir
    config_dir = os.path.join(home, ".config", "containers")
    subprocess.run(
        ["install", "-d", "-o", username, "-g", username, "-m", "0700", config_dir],
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
    with open(storage_conf_path, "w") as f:
        f.write(content)
    os.chown(storage_conf_path, pw.pw_uid, pw.pw_gid)
    os.chmod(storage_conf_path, 0o600)
    logger.info("Wrote storage.conf for %s (graphRoot=%s)", username, graph_root)


def write_containers_conf(service_id: str) -> None:
    """Write ~/.config/containers/containers.conf for the service user.

    Sets default_rootless_network_cmd = "pasta" when Podman >= 4.1 (which
    introduced pasta support), as slirp4netns is deprecated and will be
    removed in a future Podman version. pasta is the default from 5.3+.
    """
    from ..podman_version import get_features

    username = _username(service_id)
    pw = pwd.getpwnam(username)
    home = pw.pw_dir
    config_dir = os.path.join(home, ".config", "containers")
    subprocess.run(
        ["install", "-d", "-o", username, "-g", username, "-m", "0700", config_dir],
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

    with open(conf_path, "w") as f:
        f.write(content)
    os.chown(conf_path, pw.pw_uid, pw.pw_gid)
    os.chmod(conf_path, 0o600)
    logger.info("Wrote containers.conf for %s", username)


def read_containers_conf(service_id: str) -> str | None:
    """Read the containers.conf for the service user, or None if not present."""
    try:
        home = get_home(service_id)
        path = os.path.join(home, ".config", "containers", "containers.conf")
        with open(path) as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return None


def read_storage_conf(service_id: str) -> str | None:
    """Read the storage.conf for the service user, or None if not present."""
    try:
        home = get_home(service_id)
        path = os.path.join(home, ".config", "containers", "storage.conf")
        with open(path) as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return None


def podman_reset(service_id: str) -> None:
    """Run `podman system reset --force` as the service user.

    Wipes all containers, images and storage so that the next pull starts
    fresh with the current storage.conf (driver + fuse-overlayfs).  Safe to
    call during initial service setup because there is nothing to preserve yet.
    """
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
            "system",
            "reset",
            "--force",
        ],
        cwd="/",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("podman system reset failed for %s: %s", username, result.stderr.strip())
    else:
        logger.info("podman system reset completed for %s", username)


def podman_migrate(service_id: str) -> None:
    """Run `podman system migrate` as the service user.

    Must be called after enable_linger() so that /run/user/{uid} exists.
    This initialises Podman's overlay storage with the correct subUID/subGID ranges.
    HOME must be set explicitly — without it sudo drops HOME and Podman falls back
    to /tmp/containers-user-{uid}/ which may not support UID remapping.
    """
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
            "system",
            "migrate",
        ],
        cwd="/",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("podman system migrate failed for %s: %s", username, result.stderr.strip())
    else:
        logger.info("podman system migrate completed for %s", username)


def enable_linger(service_id: str) -> None:
    username = _username(service_id)
    subprocess.run(
        ["loginctl", "enable-linger", username],
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info("Enabled linger for %s", username)
    _wait_for_runtime_dir(service_id)


def disable_linger(service_id: str) -> None:
    username = _username(service_id)
    subprocess.run(
        ["loginctl", "disable-linger", username],
        check=False,
        capture_output=True,
        text=True,
    )
    logger.info("Disabled linger for %s", username)


def linger_enabled(service_id: str) -> bool:
    username = _username(service_id)
    return os.path.exists(f"/var/lib/systemd/linger/{username}")


def _auth_file(service_id: str) -> str:
    """Return the persistent auth.json path for the service user."""
    home = get_home(service_id)
    return os.path.join(home, ".config", "containers", "auth.json")


def registry_login(service_id: str, registry: str, username: str, password: str) -> None:
    """Run `podman login` as the service user. Password is passed via stdin only.

    Uses --authfile to write to the persistent location instead of XDG_RUNTIME_DIR
    (tmpfs) which would be lost on reboot.
    """
    comp_username = _username(service_id)
    home = get_home(service_id)
    authfile = _auth_file(service_id)
    result = subprocess.run(
        [
            "sudo",
            "-u",
            comp_username,
            "env",
            f"HOME={home}",
            "podman",
            "login",
            "--authfile",
            authfile,
            "--username",
            username,
            "--password-stdin",
            registry,
        ],
        input=password,
        cwd="/",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    logger.info("Registry login to %s succeeded for service %s", registry, service_id)


def registry_logout(service_id: str, registry: str) -> None:
    """Run `podman logout` as the service user."""
    comp_username = _username(service_id)
    home = get_home(service_id)
    authfile = _auth_file(service_id)
    result = subprocess.run(
        [
            "sudo",
            "-u",
            comp_username,
            "env",
            f"HOME={home}",
            "podman",
            "logout",
            "--authfile",
            authfile,
            registry,
        ],
        cwd="/",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    logger.info("Registry logout from %s succeeded for service %s", registry, service_id)


def list_registry_logins(service_id: str) -> list[str]:
    """Return list of registries the service user is currently logged into."""
    import json

    home = get_home(service_id)
    auth_path = os.path.join(home, ".config", "containers", "auth.json")
    try:
        with open(auth_path) as f:
            data = json.load(f)
        return list(data.get("auths", {}).keys())
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return []


def _wait_for_runtime_dir(service_id: str, timeout: float = 10.0) -> None:
    """Wait for /run/user/{uid} to appear after enabling linger."""
    uid = get_uid(service_id)
    runtime_dir = f"/run/user/{uid}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.isdir(runtime_dir):
            return
        time.sleep(0.5)
    logger.warning("Runtime dir %s did not appear within %ss", runtime_dir, timeout)

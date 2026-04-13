"""Volume directory management for quadletman services."""

import logging
import os

from ..config import settings
from ..models import sanitized
from ..models.sanitized import (
    SafeAbsPath,
    SafeMultilineStr,
    SafeResourceName,
    SafeSELinuxContext,
    SafeSlug,
    log_safe,
    resolve_safe_path,
)
from ..utils import cmd_token
from . import host
from .selinux import apply_context, relabel, remove_context
from .user_manager import (
    _groupname,
    _helper_username,
    _username,
    create_helper_user,
    get_helper_uid,
    get_service_gid,
    get_uid,
)

logger = logging.getLogger(__name__)


@host.audit("VOLUME_CREATE", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def create_volume_dir(
    service_id: SafeSlug,
    volume_name: SafeResourceName,
    selinux_context: SafeSELinuxContext = SafeSELinuxContext.trusted(
        "container_file_t", "hardcoded default"
    ),
    owner_uid: int = 0,
) -> str:
    """Create volume directory, set ownership and SELinux context. Returns path.

    owner_uid: container UID that should own the directory.
      0 (default) → owned by the service user (qm-{service_id}), mode 770.
      N > 0        → owned by the helper user qm-{service_id}-N
                     (host UID = subuid_start + N), mode 770.
                     This allows container processes running as UID N to have
                     direct owner access without exposing the directory to all
                     host users (no world-readable bits needed).
    """
    path = resolve_safe_path(settings.volumes_base, f"{service_id}/{volume_name}")
    groupname = _groupname(service_id)

    if owner_uid == 0:
        owner = _username(service_id)
    else:
        # Resolve the helper user. Create it if it doesn't exist yet.
        create_helper_user(service_id, owner_uid)
        owner = _helper_username(service_id, owner_uid)

    host.makedirs(SafeAbsPath.of(path, "volume_path"), mode=0o770, exist_ok=True)

    host.run(
        [cmd_token("chown"), cmd_token("-R"), cmd_token(f"{owner}:{groupname}"), path],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )
    host.run(
        [cmd_token("chmod"), cmd_token("-R"), cmd_token("770"), path],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )

    apply_context(SafeAbsPath.of(path, "volume_path"), selinux_context)
    logger.info("Created volume dir %s (owner=%s)", path, owner)
    return path


@host.audit("VOLUME_CHOWN", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def chown_volume_dir(service_id: SafeSlug, volume_name: SafeResourceName, owner_uid: int) -> None:
    """Re-chown an existing volume directory to a new owner_uid."""
    path = resolve_safe_path(settings.volumes_base, f"{service_id}/{volume_name}")
    groupname = _groupname(service_id)

    if owner_uid == 0:
        owner = _username(service_id)
    else:
        create_helper_user(service_id, owner_uid)
        owner = _helper_username(service_id, owner_uid)

    host.run(
        [cmd_token("chown"), cmd_token("-R"), cmd_token(f"{owner}:{groupname}"), path],
        admin=True,
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info("Re-chowned volume dir %s to %s", log_safe(path), log_safe(owner))


@host.audit("VOLUME_DELETE", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def delete_volume_dir(service_id: SafeSlug, volume_name: SafeResourceName) -> None:
    path = resolve_safe_path(settings.volumes_base, f"{service_id}/{volume_name}")
    if os.path.isdir(path):
        remove_context(SafeAbsPath.of(path, "volume_path"))
        host.rmtree(SafeAbsPath.of(path, "volume_path"), ignore_errors=True)
        logger.info("Deleted volume dir %s", log_safe(path))


@host.audit("VOLUMES_DELETE_ALL", lambda sid, *_: sid)
@sanitized.enforce
def delete_all_service_volumes(service_id: SafeSlug) -> None:
    service_vol_dir = os.path.join(settings.volumes_base, service_id)
    if os.path.isdir(service_vol_dir):
        remove_context(SafeAbsPath.of(service_vol_dir, "service_vol_dir"))
        host.rmtree(SafeAbsPath.of(service_vol_dir, "service_vol_dir"), ignore_errors=True)
        logger.info("Deleted all volumes for service %s", service_id)


@host.audit("VOLUMES_BASE_ENSURE")
@sanitized.enforce
def ensure_volumes_base() -> None:
    host.makedirs(SafeAbsPath.of(settings.volumes_base, "volumes_base"), mode=0o755, exist_ok=True)


# ---------------------------------------------------------------------------
# Volume file browser operations
# ---------------------------------------------------------------------------


def _resolve_owner_uid_gid(service_id: SafeSlug, owner_uid: int) -> tuple[int, int]:
    """Return (host_uid, host_gid) for the volume owner.

    owner_uid == 0 → compartment root user (qm-{id}).
    owner_uid > 0  → helper user (qm-{id}-N).
    """
    gid = get_service_gid(service_id)
    if owner_uid == 0:
        return get_uid(service_id), gid
    host_uid = get_helper_uid(service_id, owner_uid)
    if host_uid is None:
        # Helper user does not exist yet — fall back to compartment root.
        logger.warning(
            "Helper user for UID %d in %s not found, falling back to compartment root",
            owner_uid,
            service_id,
        )
        return get_uid(service_id), gid
    return host_uid, gid


@host.audit("VOL_FILE_SAVE", lambda sid, path, *_, **__: f"{sid}:{path}")
@sanitized.enforce
def save_file(
    service_id: SafeSlug,
    path: SafeAbsPath,
    content: SafeMultilineStr,
    owner_uid: int = 0,
) -> None:
    """Write text content to a file inside a volume directory.

    Creates parent directories if needed.  Sets ownership to the volume owner
    and applies SELinux relabelling.
    """
    parent = SafeAbsPath.of(os.path.dirname(path), "vol_file_parent")
    host.makedirs(parent, exist_ok=True)
    uid, gid = _resolve_owner_uid_gid(service_id, owner_uid)
    host.write_text(path, content, uid, gid, mode=0o640)
    relabel(path)


@host.audit("VOL_FILE_UPLOAD", lambda sid, path, *_, **__: f"{sid}:{path}")
@sanitized.enforce
def upload_file(service_id: SafeSlug, path: SafeAbsPath, data: bytes, owner_uid: int = 0) -> None:
    """Write binary upload data to a file inside a volume directory.

    Sets ownership to the volume owner and applies SELinux relabelling.
    """
    uid, gid = _resolve_owner_uid_gid(service_id, owner_uid)
    host.write_bytes(path, data, uid, gid, mode=0o640)
    relabel(path)


@host.audit("VOL_FILE_DELETE", lambda sid, path, *_: f"{sid}:{path}")
@sanitized.enforce
def delete_entry(service_id: SafeSlug, path: SafeAbsPath) -> None:
    """Delete a file or directory inside a volume."""
    if os.path.isdir(path):
        host.rmtree(path, ignore_errors=False)
    else:
        host.unlink(path)


@host.audit("VOL_MKDIR", lambda sid, path, *_, **__: f"{sid}:{path}")
@sanitized.enforce
def mkdir_entry(service_id: SafeSlug, path: SafeAbsPath, owner_uid: int = 0) -> None:
    """Create a directory inside a volume, chown to volume owner, relabel."""
    host.makedirs(path, exist_ok=True)
    uid, gid = _resolve_owner_uid_gid(service_id, owner_uid)
    host.chown(path, uid, gid)
    relabel(path)


@host.audit("VOL_CHMOD", lambda sid, path, *_: f"{sid}:{path}")
@sanitized.enforce
def chmod_entry(service_id: SafeSlug, path: SafeAbsPath, mode: int) -> None:
    """Change permissions of a file or directory inside a volume."""
    host.chmod(path, mode)

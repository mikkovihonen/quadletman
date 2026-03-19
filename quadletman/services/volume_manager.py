"""Volume directory management for quadletman services."""

import logging
import os

from ..config import settings
from ..models import sanitized
from ..models.sanitized import SafeAbsPath, SafeResourceName, SafeSELinuxContext, SafeSlug, log_safe
from . import host
from .selinux import apply_context, remove_context
from .user_manager import _groupname, _helper_username, _username

logger = logging.getLogger(__name__)


@sanitized.enforce
def volume_path(service_id: SafeSlug, volume_name: SafeResourceName) -> str:
    return os.path.join(settings.volumes_base, service_id, volume_name)


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
    path = volume_path(service_id, volume_name)
    groupname = _groupname(service_id)

    if owner_uid == 0:
        owner = _username(service_id)
    else:
        # Resolve the helper user. Create it if it doesn't exist yet.
        from .user_manager import create_helper_user

        create_helper_user(service_id, owner_uid)
        owner = _helper_username(service_id, owner_uid)

    host.makedirs(SafeAbsPath.of(path, "volume_path"), mode=0o770, exist_ok=True)

    host.run(
        ["chown", "-R", f"{owner}:{groupname}", path],
        check=True,
        capture_output=True,
        text=True,
    )
    host.run(
        ["chmod", "-R", "770", path],
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
    path = volume_path(service_id, volume_name)
    groupname = _groupname(service_id)

    if owner_uid == 0:
        owner = _username(service_id)
    else:
        from .user_manager import create_helper_user

        create_helper_user(service_id, owner_uid)
        owner = _helper_username(service_id, owner_uid)

    host.run(
        ["chown", "-R", f"{owner}:{groupname}", path],
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info("Re-chowned volume dir %s to %s", log_safe(path), log_safe(owner))


@host.audit("VOLUME_DELETE", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def delete_volume_dir(service_id: SafeSlug, volume_name: SafeResourceName) -> None:
    path = volume_path(service_id, volume_name)
    if os.path.isdir(path):
        remove_context(SafeAbsPath.of(path, "volume_path"))
        host.rmtree(SafeAbsPath.of(path, "volume_path"), ignore_errors=True)
        logger.info("Deleted volume dir %s", path)


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

"""Volume directory management for quadletman services."""

import logging
import os

from ..config import settings
from . import host
from .selinux import apply_context, remove_context
from .user_manager import _groupname, _helper_username, _username

logger = logging.getLogger(__name__)


def volume_path(service_id: str, volume_name: str) -> str:
    return os.path.join(settings.volumes_base, service_id, volume_name)


@host.audit("VOLUME_CREATE", lambda sid, name, *_: f"{sid}/{name}")
def create_volume_dir(
    service_id: str,
    volume_name: str,
    selinux_context: str = "container_file_t",
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

    host.makedirs(path, mode=0o770, exist_ok=True)

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

    apply_context(path, selinux_context)
    logger.info("Created volume dir %s (owner=%s)", path, owner)
    return path


@host.audit("VOLUME_CHOWN", lambda sid, name, *_: f"{sid}/{name}")
def chown_volume_dir(service_id: str, volume_name: str, owner_uid: int) -> None:
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
    logger.info("Re-chowned volume dir %s to %s", path, owner)


@host.audit("VOLUME_DELETE", lambda sid, name, *_: f"{sid}/{name}")
def delete_volume_dir(service_id: str, volume_name: str) -> None:
    path = volume_path(service_id, volume_name)
    if os.path.isdir(path):
        remove_context(path)
        host.rmtree(path, ignore_errors=True)
        logger.info("Deleted volume dir %s", path)


@host.audit("VOLUMES_DELETE_ALL", lambda sid, *_: sid)
def delete_all_service_volumes(service_id: str) -> None:
    service_vol_dir = os.path.join(settings.volumes_base, service_id)
    if os.path.isdir(service_vol_dir):
        remove_context(service_vol_dir)
        host.rmtree(service_vol_dir, ignore_errors=True)
        logger.info("Deleted all volumes for service %s", service_id)


@host.audit("VOLUMES_BASE_ENSURE")
def ensure_volumes_base() -> None:
    host.makedirs(settings.volumes_base, mode=0o755, exist_ok=True)

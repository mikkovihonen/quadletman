"""Podman secret management for compartment users."""

import json
import logging
import subprocess

from . import host
from .user_manager import _username, get_uid

logger = logging.getLogger(__name__)


def _base_cmd(service_id: str) -> list[str]:
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


def list_podman_secrets(service_id: str) -> list[str]:
    """Return secret names currently stored in the compartment user's podman store.

    Returns an empty list if the compartment user doesn't exist or podman fails.
    """
    cmd = _base_cmd(service_id) + ["podman", "secret", "ls", "--format", "json"]
    result = subprocess.run(cmd, cwd="/", capture_output=True, text=True)
    if result.returncode != 0:
        return []
    try:
        items = json.loads(result.stdout or "[]")
        return [item["Name"] for item in items if item.get("Name")]
    except (json.JSONDecodeError, KeyError):
        return []


@host.audit("SECRET_CREATE", lambda sid, name, *_: f"{sid}/{name}")
def create_podman_secret(service_id: str, name: str, content: str) -> None:
    """Create a podman secret for the compartment user, piping content via stdin."""
    cmd = _base_cmd(service_id) + ["podman", "secret", "create", name, "-"]
    result = host.run(cmd, cwd="/", capture_output=True, text=True, input=content)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create secret '{name}' for {service_id}: {result.stderr.strip()}"
        )
    logger.info("Created podman secret %s for service %s", name, service_id)


@host.audit("SECRET_DELETE", lambda sid, name, *_: f"{sid}/{name}")
def delete_podman_secret(service_id: str, name: str) -> None:
    """Remove a podman secret from the compartment user's store."""
    cmd = _base_cmd(service_id) + ["podman", "secret", "rm", name]
    result = host.run(cmd, cwd="/", capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to delete secret '{name}' for {service_id}: {result.stderr.strip()}"
        )
    logger.info("Deleted podman secret %s for service %s", name, service_id)

"""Podman secret management for compartment users."""

import json
import logging
import subprocess

from ..models import sanitized
from ..models.sanitized import SafeMultilineStr, SafeSecretName, SafeSlug
from . import host
from .user_manager import _username, get_uid

logger = logging.getLogger(__name__)


@sanitized.enforce
def _base_cmd(service_id: SafeSlug) -> list[str]:
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
def list_podman_secrets(service_id: SafeSlug) -> list[SafeSecretName]:
    """Return secret names currently stored in the compartment user's podman store.

    Returns an empty list if the compartment user doesn't exist or podman fails.
    Names that do not conform to the secret name pattern are logged and skipped.
    """
    cmd = _base_cmd(service_id) + ["podman", "secret", "ls", "--format", "json"]
    result = subprocess.run(cmd, cwd="/", capture_output=True, text=True)
    if result.returncode != 0:
        return []
    try:
        items = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    names: list[SafeSecretName] = []
    for item in items:
        raw = item.get("Name")
        if not raw:
            continue
        try:
            names.append(SafeSecretName.of(raw, "podman:secret_name"))
        except ValueError:
            logger.warning("podman secret ls returned invalid secret name %r — skipping", raw)
    return names


@sanitized.enforce
def secret_exists(service_id: SafeSlug, name: SafeSecretName) -> bool:
    """Check whether a named secret exists in the compartment user's podman store."""
    cmd = _base_cmd(service_id) + ["podman", "secret", "exists", name]
    result = subprocess.run(cmd, cwd="/", capture_output=True, text=True)
    return result.returncode == 0


@host.audit("SECRET_CREATE", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def create_podman_secret(
    service_id: SafeSlug, name: SafeSecretName, content: SafeMultilineStr
) -> None:
    """Create a podman secret for the compartment user, piping content via stdin."""
    cmd = _base_cmd(service_id) + ["podman", "secret", "create", name, "-"]
    result = host.run(cmd, cwd="/", capture_output=True, text=True, input=content)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create secret '{name}' for {service_id}: {result.stderr.strip()}"
        )
    logger.info("Created podman secret %s for service %s", name, service_id)


@host.audit("SECRET_OVERWRITE", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def overwrite_podman_secret(
    service_id: SafeSlug, name: SafeSecretName, content: SafeMultilineStr
) -> None:
    """Replace a podman secret by deleting and recreating it with new content.

    Podman has no native update command, so this is a delete + create cycle.
    """
    if secret_exists(service_id, name):
        rm_cmd = _base_cmd(service_id) + ["podman", "secret", "rm", name]
        host.run(rm_cmd, cwd="/", capture_output=True, text=True)
    create_cmd = _base_cmd(service_id) + ["podman", "secret", "create", name, "-"]
    result = host.run(create_cmd, cwd="/", capture_output=True, text=True, input=content)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to overwrite secret '{name}' for {service_id}: {result.stderr.strip()}"
        )
    logger.info("Overwrote podman secret %s for service %s", name, service_id)


@host.audit("SECRET_DELETE", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def delete_podman_secret(service_id: SafeSlug, name: SafeSecretName) -> None:
    """Remove a podman secret from the compartment user's store."""
    cmd = _base_cmd(service_id) + ["podman", "secret", "rm", name]
    result = host.run(cmd, cwd="/", capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to delete secret '{name}' for {service_id}: {result.stderr.strip()}"
        )
    logger.info("Deleted podman secret %s for service %s", name, service_id)

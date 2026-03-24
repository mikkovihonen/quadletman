"""SELinux context management. All operations are no-ops when SELinux is unavailable."""

import logging
import subprocess

from ..models import sanitized
from ..models.sanitized import SafeAbsPath, SafeSELinuxContext
from ..utils import cmd_token
from . import host

logger = logging.getLogger(__name__)


@sanitized.enforce
def is_selinux_active() -> bool:
    try:
        result = subprocess.run(
            ["getenforce"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() not in ("Disabled", "")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@host.audit(
    "SELINUX_APPLY_CONTEXT",
    lambda path, ctx=SafeSELinuxContext.trusted("container_file_t", "default"), *_: (
        f"{path} ({ctx})"
    ),
)
@sanitized.enforce
def apply_context(
    path: SafeAbsPath,
    context_type: SafeSELinuxContext = SafeSELinuxContext.trusted("container_file_t", "default"),
) -> None:
    """Apply SELinux type context to path recursively. No-op if SELinux unavailable."""
    if not is_selinux_active():
        logger.debug("SELinux not active, skipping context for %s", path)
        return

    # Add persistent fcontext rule
    for action in (cmd_token("-a"), cmd_token("-m")):
        result = host.run(
            [
                cmd_token("semanage"),
                cmd_token("fcontext"),
                action,
                cmd_token("-t"),
                context_type,
                cmd_token(f"{path}(/.*)?"),
            ],
            admin=True,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            break

    # Apply immediately with chcon
    host.run(
        [cmd_token("chcon"), cmd_token("-R"), cmd_token("-t"), context_type, path],
        admin=True,
        capture_output=True,
        text=True,
    )
    # Also run restorecon to apply the semanage policy
    host.run(
        [cmd_token("restorecon"), cmd_token("-R"), path],
        admin=True,
        capture_output=True,
        text=True,
    )
    logger.info("Applied SELinux context %s to %s", context_type, path)


@host.audit("SELINUX_RELABEL", lambda path, *_: path)
@sanitized.enforce
def relabel(path: SafeAbsPath) -> None:
    """Run restorecon on a single path (non-recursive). No-op if SELinux unavailable.

    Use this after writing individual files into a volume directory so that the
    persistent fcontext rule (set at volume creation time) is applied to each new
    file without re-running the slow recursive chcon on the whole tree.
    """
    if not is_selinux_active():
        return
    host.run([cmd_token("restorecon"), path], admin=True, capture_output=True, text=True)


@sanitized.enforce
def get_file_context_type(path: SafeAbsPath) -> str | None:
    """Return the SELinux type for a file (e.g. 'container_file_t').

    Uses `stat --printf=%C` which prints the full security context; we extract
    just the type component.  Returns None when SELinux is unavailable or the
    filesystem has no label for this path.
    """
    try:
        result = subprocess.run(
            ["stat", "--printf=%C", path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        raw = result.stdout.strip()
        if result.returncode != 0 or not raw or raw in ("?", "(null)"):
            return None
        parts = raw.split(":")
        return parts[2] if len(parts) >= 3 else raw
    except Exception:
        return None


@host.audit("SELINUX_REMOVE_CONTEXT", lambda path, *_: path)
@sanitized.enforce
def remove_context(path: SafeAbsPath) -> None:
    """Remove persistent fcontext rule for path. No-op if SELinux unavailable."""
    if not is_selinux_active():
        return
    host.run(
        [cmd_token("semanage"), cmd_token("fcontext"), cmd_token("-d"), cmd_token(f"{path}(/.*)?")],
        admin=True,
        capture_output=True,
        text=True,
    )

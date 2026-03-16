"""SELinux context management. All operations are no-ops when SELinux is unavailable."""

import logging
import subprocess

from . import host

logger = logging.getLogger(__name__)


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


@host.audit("SELINUX_APPLY_CONTEXT", lambda path, ctx="container_file_t", *_: f"{path} ({ctx})")
def apply_context(path: str, context_type: str = "container_file_t") -> None:
    """Apply SELinux type context to path recursively. No-op if SELinux unavailable."""
    if not is_selinux_active():
        logger.debug("SELinux not active, skipping context for %s", path)
        return

    # Add persistent fcontext rule
    for action in ("-a", "-m"):
        result = host.run(
            ["semanage", "fcontext", action, "-t", context_type, f"{path}(/.*)?"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            break

    # Apply immediately with chcon
    host.run(
        ["chcon", "-R", "-t", context_type, path],
        capture_output=True,
        text=True,
    )
    # Also run restorecon to apply the semanage policy
    host.run(
        ["restorecon", "-R", path],
        capture_output=True,
        text=True,
    )
    logger.info("Applied SELinux context %s to %s", context_type, path)


@host.audit("SELINUX_RELABEL", lambda path, *_: path)
def relabel(path: str) -> None:
    """Run restorecon on a single path (non-recursive). No-op if SELinux unavailable.

    Use this after writing individual files into a volume directory so that the
    persistent fcontext rule (set at volume creation time) is applied to each new
    file without re-running the slow recursive chcon on the whole tree.
    """
    if not is_selinux_active():
        return
    host.run(["restorecon", path], capture_output=True, text=True)


def get_file_context_type(path: str) -> str | None:
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
def remove_context(path: str) -> None:
    """Remove persistent fcontext rule for path. No-op if SELinux unavailable."""
    if not is_selinux_active():
        return
    host.run(
        ["semanage", "fcontext", "-d", f"{path}(/.*)?"],
        capture_output=True,
        text=True,
    )

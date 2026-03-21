"""Pure utility functions shared across the project.

This module may only import from ``models.sanitized`` (no circular-import
risk).  It must not import from ``config/``, ``routers/``, ``services/``,
or ``db/``.  Functions here are safe to use everywhere.
"""

import os
from contextlib import suppress

from .models.sanitized import SafeStr


def fmt_bytes(b: int) -> str:
    """Format a byte count as a human-readable string (binary / 1024-based)."""
    if b >= 1_073_741_824:
        return f"{b / 1_073_741_824:.1f} GB"
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


def cmd_token(v: str) -> SafeStr:
    """Wrap a hardcoded command-line token as ``SafeStr``.

    Use this for literal strings that are part of subprocess commands
    (e.g. ``cmd_token("useradd")``, ``cmd_token("--system")``).
    """
    return SafeStr.of(v, "cmd")


def dir_size(path: str) -> int:
    """Return total byte size of all files under *path* (recursive)."""
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_dir(follow_symlinks=False):
                total += dir_size(entry.path)
            elif entry.is_file(follow_symlinks=False):
                with suppress(OSError):
                    total += entry.stat().st_size
    except OSError:
        pass
    return total


def dir_size_excluding(path: str, exclude: str) -> int:
    """Return total byte size of all files under *path*, skipping the *exclude* subtree."""
    total = 0
    try:
        for entry in os.scandir(path):
            full = entry.path
            if os.path.abspath(full) == os.path.abspath(exclude):
                continue
            if entry.is_dir(follow_symlinks=False):
                total += dir_size_excluding(full, exclude)
            elif entry.is_file(follow_symlinks=False):
                with suppress(OSError):
                    total += entry.stat().st_size
    except OSError:
        pass
    return total

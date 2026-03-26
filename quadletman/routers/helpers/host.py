"""Host log helpers."""

import subprocess
from pathlib import Path

_AUDIT_LOG_PATH = Path("/var/log/quadletman/host.log")


def read_audit_lines(limit: int) -> list[str]:
    """Read the last N lines from the host audit log file."""
    if not _AUDIT_LOG_PATH.is_file():
        return []
    with open(_AUDIT_LOG_PATH) as f:
        lines = f.readlines()
    return [line.rstrip() for line in lines[-limit:]]


def read_journalctl_lines(limit: int) -> list[str]:
    """Read recent journalctl lines for the quadletman unit."""
    cmd = [
        "journalctl",
        "-u",
        "quadletman",
        f"-n{limit}",
        "--no-pager",
        "--output=short-iso",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10
        )  # read-only; short timeout for journalctl query
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return [f"[error: {exc}]"]
    if result.returncode != 0:
        return [f"[journalctl exited {result.returncode}: {result.stderr.strip()}]"]
    return [line for line in result.stdout.splitlines() if not line.startswith("-- ")]

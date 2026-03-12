"""Podman version detection and feature flag resolution."""

import functools
import logging
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"podman version\s+(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class PodmanFeatures:
    version: tuple[int, int, int] | None
    version_str: str
    # Feature flags
    quadlet: bool      # >= 4.4.0 — basic Quadlet support
    build_units: bool  # >= 4.5.0 — .build quadlet units
    apparmor: bool     # >= 5.8.0 — AppArmor= key in [Container]
    bundle: bool       # >= 5.8.0 — multi-unit .quadlets bundle format
    pasta: bool        # >= 4.1.0 — pasta available; default from 5.3+


def _parse_version(output: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.search(output)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


@functools.lru_cache(maxsize=1)
def get_features() -> PodmanFeatures:
    """Detect the installed Podman version and return a feature-flag object.

    Result is cached for the lifetime of the process. Returns unknown/all-False
    if Podman is not installed or its version cannot be parsed.
    """
    version: tuple[int, int, int] | None = None
    try:
        result = subprocess.run(
            ["podman", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version = _parse_version(result.stdout)
    except Exception as exc:
        logger.warning("Could not detect Podman version: %s", exc)

    if version is None:
        version_str = "unknown"
    else:
        version_str = f"{version[0]}.{version[1]}.{version[2]}"
        logger.info("Detected Podman %s", version_str)

    return PodmanFeatures(
        version=version,
        version_str=version_str,
        quadlet=version is not None and version >= (4, 4, 0),
        build_units=version is not None and version >= (4, 5, 0),
        apparmor=version is not None and version >= (5, 8, 0),
        bundle=version is not None and version >= (5, 8, 0),
        pasta=version is not None and version >= (4, 1, 0),
    )

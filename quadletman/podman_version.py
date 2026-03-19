"""Podman version detection and feature flag resolution."""

import functools
import json
import logging
import re
import subprocess
from dataclasses import dataclass

from .models.sanitized import SafeStr

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"podman version\s+(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class PodmanFeatures:
    version: tuple[int, int, int] | None
    version_str: SafeStr
    # Feature flags
    quadlet: bool  # >= 4.4.0 — basic Quadlet support
    build_units: bool  # >= 4.5.0 — .build quadlet units
    image_pull_policy: bool  # >= 5.0.0 — PullPolicy= key in .image quadlet units
    apparmor: bool  # >= 5.8.0 — AppArmor= key in [Container]
    bundle: bool  # >= 5.8.0 — multi-unit .quadlets bundle format
    pasta: bool  # >= 4.1.0 — pasta available; default from 5.3+
    vol_driver_image: bool  # >= 5.0.0 — image driver for quadlet .volume units


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
        version_str = SafeStr.of("unknown", "get_features")
    else:
        version_str = SafeStr.of(f"{version[0]}.{version[1]}.{version[2]}", "get_features")
        logger.info("Detected Podman %s", version_str)

    return PodmanFeatures(
        version=version,
        version_str=version_str,
        quadlet=version is not None and version >= (4, 4, 0),
        build_units=version is not None and version >= (4, 5, 0),
        image_pull_policy=version is not None and version >= (5, 0, 0),
        apparmor=version is not None and version >= (5, 8, 0),
        bundle=version is not None and version >= (5, 8, 0),
        pasta=version is not None and version >= (4, 1, 0),
        vol_driver_image=version is not None and version >= (5, 0, 0),
    )


@functools.lru_cache(maxsize=1)
def get_podman_info() -> dict:
    """Return the full 'podman info' dict, cached for the process lifetime.

    Falls back to an empty dict if Podman is unavailable or output cannot be parsed.
    """
    try:
        result = subprocess.run(
            ["podman", "info", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        info = json.loads(result.stdout.strip())
        if not isinstance(info, dict):
            raise ValueError("unexpected format")
        return info
    except Exception as exc:
        logger.warning("Could not query podman info: %s", exc)
        return {}


@functools.lru_cache(maxsize=1)
def get_network_drivers() -> list[SafeStr]:
    """Return available Podman network plugin names, always including 'bridge'.

    Queries 'podman info' once and caches for the process lifetime.
    Falls back to ['bridge'] if Podman is unavailable or the output cannot be parsed.
    """
    try:
        result = subprocess.run(
            ["podman", "info", "--format", "{{json .Plugins.Network}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        drivers: list[str] = json.loads(result.stdout.strip())
        if not isinstance(drivers, list):
            raise ValueError("unexpected format")
        drivers = [d for d in drivers if d != "bridge"]
        return [SafeStr.of("bridge", "get_network_drivers")] + [
            SafeStr.of(d, "get_network_drivers") for d in sorted(drivers)
        ]
    except Exception as exc:
        logger.warning("Could not query Podman network drivers: %s", exc)
        return [SafeStr.of("bridge", "get_network_drivers")]


@functools.lru_cache(maxsize=1)
def get_log_drivers() -> list[SafeStr]:
    """Return available Podman log driver names.

    Queries 'podman info' once and caches for the process lifetime.
    Falls back to a sensible default list if Podman is unavailable or output cannot be parsed.
    """
    try:
        result = subprocess.run(
            ["podman", "info", "--format", "{{json .Plugins.Log}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        drivers: list[str] = json.loads(result.stdout.strip())
        if not isinstance(drivers, list):
            raise ValueError("unexpected format")
        return [SafeStr.of(d, "get_log_drivers") for d in sorted(drivers)]
    except Exception as exc:
        logger.warning("Could not query Podman log drivers: %s", exc)
        return [
            SafeStr.of(d, "get_log_drivers")
            for d in ["journald", "json-file", "k8s-file", "none", "passthrough"]
        ]


@functools.lru_cache(maxsize=1)
def get_volume_drivers() -> list[SafeStr]:
    """Return available Podman volume plugin names, always including 'local'.

    Queries 'podman info' once and caches for the process lifetime.
    Falls back to ['local'] if Podman is unavailable or the output cannot be parsed.
    """
    try:
        result = subprocess.run(
            ["podman", "info", "--format", "{{json .Plugins.Volume}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        drivers: list[str] = json.loads(result.stdout.strip())
        if not isinstance(drivers, list):
            raise ValueError("unexpected format")
        # Normalise: ensure 'local' is always present and listed first
        drivers = [d for d in drivers if d != "local"]
        return [SafeStr.of("local", "get_volume_drivers")] + [
            SafeStr.of(d, "get_volume_drivers") for d in sorted(drivers)
        ]
    except Exception as exc:
        logger.warning("Could not query Podman volume drivers: %s", exc)
        return [SafeStr.of("local", "get_volume_drivers")]

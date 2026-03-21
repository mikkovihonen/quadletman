"""Podman version detection and feature flag resolution.

Feature flags are derived from :class:`~quadletman.models.version_span.VersionSpan`
constants.  Property-level flags (tied to specific model fields) are accessed
via pre-computed availability dicts — see
:func:`~quadletman.models.version_span.field_availability`.
"""

import functools
import json
import logging
import re
import subprocess
from dataclasses import dataclass

from .models.sanitized import SafeStr
from .models.version_span import (
    ARTIFACT_UNITS,
    BUILD_UNITS,
    BUNDLE,
    IMAGE_UNITS,
    PASTA,
    POD_UNITS,
    QUADLET,
    QUADLET_CLI,
    PodmanVersion,
    VersionSpan,
    field_tooltip,
    is_field_available,
    is_field_deprecated,
    is_value_available,
)

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"podman version\s+(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class PodmanFeatures:
    """Detected Podman version with VersionSpan-aware availability checks.

    Feature-level boolean flags (``quadlet``, ``build_units``, ``bundle``,
    ``pasta``) are computed from :mod:`~quadletman.models.version_span`
    constants at construction time.  For field-level checks use the
    ``available`` / ``value_ok`` / ``tooltip`` methods, or pre-computed
    dicts from :func:`~quadletman.models.version_span.field_availability`.
    """

    version: PodmanVersion | None
    version_str: SafeStr
    # Feature-level flags — derived from VersionSpan constants
    pasta: bool  # >= 4.1.0 — pasta available; default from 5.3+
    quadlet: bool  # >= 4.4.0 — basic Quadlet support
    image_units: bool  # >= 4.8.0 — .image unit files
    pod_units: bool  # >= 5.0.0 — .pod unit files
    build_units: bool  # >= 5.2.0 — .build quadlet units
    quadlet_cli: bool  # >= 5.6.0 — podman quadlet install/list/rm/print CLI
    artifact_units: bool  # >= 5.7.0 — .artifact unit files
    bundle: bool  # >= 5.8.0 — multi-unit .quadlets bundle format

    def available(self, span: VersionSpan) -> bool:
        """Check if a feature described by *span* is available."""
        return is_field_available(span, self.version)

    def deprecated(self, span: VersionSpan) -> bool:
        """Check if a feature described by *span* is deprecated."""
        return is_field_deprecated(span, self.version)

    def value_ok(self, span: VersionSpan, value: str) -> bool:
        """Check if a specific *value* is available for a version-gated field."""
        return is_value_available(span, value, self.version)

    def tooltip(self, span: VersionSpan) -> str:
        """Human-readable tooltip for a version-gated feature/field."""
        return field_tooltip(span, self.version)


def _parse_version(output: str) -> PodmanVersion | None:
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
    version: PodmanVersion | None = None
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
        pasta=is_field_available(PASTA, version),
        quadlet=is_field_available(QUADLET, version),
        image_units=is_field_available(IMAGE_UNITS, version),
        pod_units=is_field_available(POD_UNITS, version),
        build_units=is_field_available(BUILD_UNITS, version),
        quadlet_cli=is_field_available(QUADLET_CLI, version),
        artifact_units=is_field_available(ARTIFACT_UNITS, version),
        bundle=is_field_available(BUNDLE, version),
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

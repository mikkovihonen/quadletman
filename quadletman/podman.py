"""Podman version detection and feature flag resolution.

Feature flags are derived from :class:`~quadletman.models.version_span.VersionSpan`
constants.  Property-level flags (tied to specific model fields) are accessed
via pre-computed availability dicts — see
:func:`~quadletman.models.version_span.field_availability`.
"""

import functools
import json
import logging
import os
import pwd
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass

from .config.settings import settings
from .models.sanitized import SafeStr
from .models.version_span import (
    ARTIFACT_UNITS,
    AUTO_UPDATE_DRY_RUN,
    BUILD_UNITS,
    BUNDLE,
    IMAGE_UNITS,
    PASTA,
    POD_UNITS,
    QUADLET,
    QUADLET_CLI,
    SLIRP4NETNS,
    PodmanVersion,
    VersionSpan,
    field_tooltip,
    is_field_available,
    is_field_deprecated,
    is_value_available,
)

logger = logging.getLogger(__name__)

_PODMAN_INFO_RETRY_INTERVAL = float(settings.podman_info_retry_interval)
_VERSION_RE = re.compile(r"podman version\s+(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)
_podman_info_cache: dict | None = None
_podman_info_last_attempt: float = 0.0


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
    slirp4netns: bool  # < 6.0.0 — deprecated 5.7, removed 6.0
    pasta: bool  # >= 4.1.0 — pasta available; default from 5.3+
    quadlet: bool  # >= 4.4.0 — basic Quadlet support
    image_units: bool  # >= 4.8.0 — .image unit files
    pod_units: bool  # >= 5.0.0 — .pod unit files
    build_units: bool  # >= 5.2.0 — .build quadlet units
    quadlet_cli: bool  # >= 5.6.0 — podman quadlet install/list/rm/print CLI
    artifact_units: bool  # >= 5.7.0 — .artifact unit files
    bundle: bool  # >= 5.8.0 — multi-unit .quadlets bundle format
    auto_update_dry_run: bool  # >= 4.7.0 — podman auto-update --dry-run

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


_features_cache: PodmanFeatures | None = None
_features_lock = threading.Lock()


def _detect_features() -> PodmanFeatures:
    """Run ``podman --version`` and build a :class:`PodmanFeatures` object."""
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
        slirp4netns=is_field_available(SLIRP4NETNS, version),
        pasta=is_field_available(PASTA, version),
        quadlet=is_field_available(QUADLET, version),
        image_units=is_field_available(IMAGE_UNITS, version),
        pod_units=is_field_available(POD_UNITS, version),
        build_units=is_field_available(BUILD_UNITS, version),
        quadlet_cli=is_field_available(QUADLET_CLI, version),
        artifact_units=is_field_available(ARTIFACT_UNITS, version),
        bundle=is_field_available(BUNDLE, version),
        auto_update_dry_run=is_field_available(AUTO_UPDATE_DRY_RUN, version),
    )


def get_features() -> PodmanFeatures:
    """Detect the installed Podman version and return a feature-flag object.

    Result is cached until explicitly cleared by :func:`clear_caches`.
    Returns unknown/all-False if Podman is not installed or its version
    cannot be parsed.
    """
    global _features_cache
    if _features_cache is not None:
        return _features_cache
    with _features_lock:
        if _features_cache is not None:
            return _features_cache
        _features_cache = _detect_features()
        return _features_cache


def check_version() -> str | None:
    """Run ``podman --version`` and return the clean version string.

    Returns a string like ``"5.4.2"`` on success, or ``None`` if the binary
    is missing, the command times out, or the output cannot be parsed.
    Does not modify any caches.
    """
    try:
        result = subprocess.run(
            ["podman", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        parsed = _parse_version(result.stdout)
        if parsed is None:
            return None
        return f"{parsed[0]}.{parsed[1]}.{parsed[2]}"
    except Exception as exc:
        logger.warning("Could not run podman --version: %s", exc)
        return None


def get_cached_version_str() -> str:
    """Return the version string from the current cache, or empty string."""
    if _features_cache is not None:
        return str(_features_cache.version_str)
    return ""


def clear_caches() -> None:
    """Clear all cached Podman data so the next call re-detects.

    Clears: get_features, get_host_distro, get_network_drivers,
    get_log_drivers, get_volume_drivers, and the podman info cache.
    """
    global _features_cache, _podman_info_cache, _podman_info_last_attempt
    with _features_lock:
        _features_cache = None
    _podman_info_cache = None
    _podman_info_last_attempt = 0.0
    get_host_distro.cache_clear()
    get_network_drivers.cache_clear()
    get_log_drivers.cache_clear()
    get_volume_drivers.cache_clear()
    logger.info("All Podman caches cleared")


def _podman_info_env() -> dict[str, str]:
    """Build an environment for podman info that works in non-root mode.

    When running as a non-root system user (quadletman, qm-dev), the process
    may lack HOME and XDG_RUNTIME_DIR, which podman needs for storage and
    runtime dirs.  Falls back to a per-uid temp directory when the standard
    runtime dir does not exist (e.g. system user without a login session).
    """
    env = os.environ.copy()
    if os.getuid() != 0:
        uid = os.getuid()
        pw = pwd.getpwuid(uid)
        env.setdefault("HOME", pw.pw_dir)
        runtime_dir = f"/run/user/{uid}"
        if not os.path.isdir(runtime_dir):
            runtime_dir = os.path.join(tempfile.gettempdir(), f"quadletman-runtime-{uid}")
            os.makedirs(runtime_dir, mode=0o700, exist_ok=True)
        env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    return env


def get_podman_info() -> dict:
    """Return the full 'podman info' dict, cached on first success.

    Falls back to an empty dict if Podman is unavailable or output cannot be
    parsed.  Unlike lru_cache, an empty result is not cached — subsequent calls
    will retry after a cooldown period, allowing the app to pick up podman info
    once the runtime environment becomes available without spamming retries.
    """
    global _podman_info_cache, _podman_info_last_attempt
    if _podman_info_cache is not None:
        return _podman_info_cache
    now = time.monotonic()
    if now - _podman_info_last_attempt < _PODMAN_INFO_RETRY_INTERVAL:
        return {}
    _podman_info_last_attempt = now
    stderr = ""
    try:
        result = subprocess.run(
            ["podman", "info", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,  # read-only; short timeout for version detection
            cwd="/",
            env=_podman_info_env(),
        )
        stderr = result.stderr.strip()
        if result.returncode != 0:
            logger.warning("podman info failed (rc=%d): %s", result.returncode, stderr)
            return {}
        info = json.loads(result.stdout.strip())
        if not isinstance(info, dict):
            raise ValueError("unexpected format")
        _podman_info_cache = info
        return info
    except Exception as exc:
        detail = f"{exc}; stderr: {stderr}" if stderr else str(exc)
        logger.warning("Could not query podman info: %s", detail)
        return {}


def _read_os_release() -> str:
    """Read distribution name and version from /etc/os-release."""
    try:
        fields: dict[str, str] = {}
        with open("/etc/os-release") as f:
            for line in f:
                key, _, val = line.strip().partition("=")
                if val:
                    fields[key] = val.strip('"')
        name = fields.get("NAME", "")
        version = fields.get("VERSION_ID", "")
        return f"{name} {version}".strip()
    except OSError:
        return ""


@functools.lru_cache(maxsize=1)
def get_host_distro() -> str:
    """Return the host OS name and version string.

    Tries podman info first; falls back to /etc/os-release when the distribution
    field is absent (common in rootless mode).
    """
    dist = get_podman_info().get("host", {}).get("distribution", {})
    distro = f"{dist.get('distribution', '')} {dist.get('version', '')}".strip()
    if distro:
        return distro
    return _read_os_release()


def _plugins(key: str) -> list[str]:
    """Extract a plugin list from the cached podman info dict."""
    plugins = get_podman_info().get("plugins", {}).get(key, [])
    return plugins if isinstance(plugins, list) else []


@functools.lru_cache(maxsize=1)
def get_network_drivers() -> list[SafeStr]:
    """Return available Podman network plugin names, always including 'bridge'.

    Extracts from the cached get_podman_info() result.
    Falls back to ['bridge'] if Podman is unavailable or the output cannot be parsed.
    """
    drivers = [d for d in _plugins("network") if d != "bridge"]
    return [SafeStr.of("bridge", "get_network_drivers")] + [
        SafeStr.of(d, "get_network_drivers") for d in sorted(drivers)
    ]


@functools.lru_cache(maxsize=1)
def get_log_drivers() -> list[SafeStr]:
    """Return available Podman log driver names.

    Extracts from the cached get_podman_info() result.
    Falls back to a sensible default list if Podman is unavailable or output cannot be parsed.
    """
    drivers = _plugins("log")
    if drivers:
        return [SafeStr.of(d, "get_log_drivers") for d in sorted(drivers)]
    return [
        SafeStr.of(d, "get_log_drivers")
        for d in ["journald", "json-file", "k8s-file", "none", "passthrough"]
    ]


@functools.lru_cache(maxsize=1)
def get_volume_drivers() -> list[SafeStr]:
    """Return available Podman volume plugin names, always including 'local'.

    Extracts from the cached get_podman_info() result.
    Falls back to ['local'] if Podman is unavailable or the output cannot be parsed.
    """
    drivers = [d for d in _plugins("volume") if d != "local"]
    return [SafeStr.of("local", "get_volume_drivers")] + [
        SafeStr.of(d, "get_volume_drivers") for d in sorted(drivers)
    ]

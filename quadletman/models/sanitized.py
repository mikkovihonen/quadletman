"""Branded string types, sanitization decorators, and path/log sanitizers.

Branded types
-------------
``str`` subclasses constructable only via ``.of(value)`` (validates) or
``.trusted(value, reason)`` (skips validation for internally generated values).
Direct instantiation raises ``TypeError``.  Holding an instance proves the
corresponding sanitization contract has been fulfilled.

Types: ``SafeStr``, ``SafeSlug``, ``SafeUsername``, ``SafeImageRef``, ``SafeUnitName``,
``SafeSecretName``, ``SafeResourceName``, ``SafeWebhookUrl``,
``SafePortMapping``, ``SafeUUID``, ``SafeSELinuxContext``,
``SafeMultilineStr``, ``SafeAbsPath``, ``SafeRedirectPath``,
``SafeTimestamp``, ``SafeIpAddress``, ``SafeFormBool``, ``SafeOctalMode``,
``SafeTimeDuration``, ``SafeCalendarSpec``, ``SafePortStr``, ``SafeNetDriver``.

Defense-in-depth layers
-----------------------
Layer 1 — HTTP boundary (``models/api.py``):
    Pydantic field validators call ``SafeSlug.of(v)`` / ``SafeStr.of(v)`` so
    that model instances carry branded strings, not plain ``str``.

Layer 2 — ORM / DB boundary (``compartment_manager.py``):
    DB results deserialized via ``Model.model_validate(dict(row))`` are
    validated automatically.  Raw mapping values passed directly to service
    functions are wrapped explicitly: ``SafeResourceName.of(row["name"], ...)``.

Layer 3 — Service signatures (``services/*.py``):
    All service functions accept branded types in their signatures, making the
    upstream obligation explicit and catchable by type checkers.

Layer 4 — Runtime assertion (``services/*.py``):
    **Every** ``def`` / ``async def`` in ``services/`` must have
    ``@sanitized.enforce`` as the innermost decorator.  The decorator reads
    type annotations at decoration time and calls ``require()`` for each
    branded parameter at every invocation, raising ``TypeError`` if a caller
    passes a plain ``str``.  For functions with no branded-type parameters the
    decorator is a no-op.  Functions that legitimately take plain ``str`` go in
    ``services/unsafe/`` instead.

    Do **not** write manual ``sanitized.require()`` calls —
    ``@sanitized.enforce`` replaces them entirely.

Decorators
----------
``@enforce`` — runtime branded-type check on function parameters.
``@enforce_model`` — marks a Pydantic ``BaseModel`` or ``@dataclass`` so that
    ``@enforce`` skips it when encountered as a parameter type.

Sanitizers
----------
``resolve_safe_path(base, path, *, absolute=False)`` — path-traversal guard
    using ``os.path.realpath()`` + prefix check.  Raises ``ValueError`` on
    traversal.  Referenced by CodeQL model extensions in
    ``.github/codeql/extensions/path-sanitizers.yml``.

``log_safe(v)`` — escapes CR/LF to prevent log injection.

Provenance tracking
-------------------
``.of()`` instances are plain branded-class instances.  ``.trusted()``
instances are private ``_Trusted*`` subclasses inheriting ``_TrustedBase``.
Both pass ``isinstance`` and ``require()`` checks.

The ``@host.audit`` decorator emits DEBUG-level provenance lines showing
which parameters were HTTP-validated versus internally trusted::

    DEBUG  PARAMS USER_CREATE   service_id=SafeSlug(validated:compartment_id @ compartments.py:42)
    DEBUG  PARAMS UNIT_START    service_id=SafeSlug(trusted:DB row) unit=SafeUnitName(trusted:internally constructed)
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import os
import re
import types
import typing
from collections.abc import Callable
from datetime import datetime
from typing import Any, get_type_hints
from urllib.parse import urlparse

from pydantic_core import core_schema as _pcs

# ---------------------------------------------------------------------------
# Validation patterns — kept here as the single source of truth.
# models.py imports from here rather than re-defining them.
# ---------------------------------------------------------------------------

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$|^[a-z0-9]$")
IMAGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-/:@]*$")
SECRET_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
UNIT_NAME_RE = re.compile(r"^[a-zA-Z0-9._@\-]+$")
RESOURCE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
WEBHOOK_URL_RE = re.compile(r"^https?://\S+$")
PORT_MAPPING_RE = re.compile(
    r"^([\d.:]+:)?\d{0,5}:\d{1,5}(/tcp|/udp)?$"
    r"|^\d{1,5}(/tcp|/udp)?$"
)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SELINUX_CONTEXT_RE = re.compile(r"^[a-zA-Z0-9_]+$")
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
ABS_PATH_RE = re.compile(r"^/[^\r\n\x00]*$")
# Matches a ".." that is a standalone path component: /.. , /../ , or the path IS just /..
_DOTDOT_COMPONENT_RE = re.compile(r"(?:^|/)\.\.(?:/|$)")
CONTROL_CHARS_RE = re.compile(r"[\r\n\x00]")
OCTAL_MODE_RE = re.compile(r"^[0-7]{3,4}$")
TIME_DURATION_RE = re.compile(r"^(\d+\s*(usec|msec|sec|s|min|m|h|hr|d|w|M|y)\s*)+$")
CALENDAR_SPEC_RE = re.compile(r"^[a-zA-Z0-9 */:.,~\-]+$")
PORT_STR_RE = re.compile(r"^\d{1,5}$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_control_chars(v: str, field_name: str) -> None:
    if CONTROL_CHARS_RE.search(v):
        raise ValueError(f"{field_name} must not contain newline, carriage return, or null byte")


def _make_validated(cls: type, value: str, field_name: str) -> Any:
    """Create a validated branded instance and record the call site.

    Uses ``inspect.currentframe()`` (O(1) frame pointer walk) rather than
    ``inspect.stack()`` (O(depth) full trace build) so that bulk DB reads
    calling ``.of()`` on every column of every row stay fast.

    Captures the frame of the *caller of .of()* — two levels up:
      _make_validated  ← called by
      .of()            ← called by
      application code ← the frame we want
    """
    frame = inspect.currentframe()
    if frame is not None and frame.f_back is not None and frame.f_back.f_back is not None:
        caller = frame.f_back.f_back
        source = f"{field_name} @ {os.path.basename(caller.f_code.co_filename)}:{caller.f_lineno}"
    else:
        source = field_name
    instance = str.__new__(cls, value)
    instance._source = source  # type: ignore[attr-defined]
    return instance


# ---------------------------------------------------------------------------
# Provenance marker
# ---------------------------------------------------------------------------


class _TrustedBase:
    """Marker mixin applied to instances created via ``.trusted()``.

    Instances carrying this mixin were NOT validated at an HTTP boundary —
    they originate from DB rows or internally constructed strings.  All public
    branded types (``SafeSlug``, ``SafeStr``, …) pass ``isinstance`` checks
    regardless of this marker; it is only inspected by the audit decorator.

    The ``reason`` attribute records why the value was trusted (e.g. ``"DB row —
    compartments.id"``).  It is shown in the DEBUG provenance log lines
    emitted by ``@host.audit``.
    """


# ---------------------------------------------------------------------------
# Base branded type
# ---------------------------------------------------------------------------


class SafeStr(str):
    """String validated to contain no control characters (\\n, \\r, \\x00).

    Construct via ``SafeStr.of(value)`` or ``SafeStr.trusted(value)``.
    Direct instantiation raises ``TypeError``.
    """

    __slots__ = ("_source",)

    def __new__(cls, *args, **kwargs):  # type: ignore[override]
        raise TypeError(
            f"Use {cls.__name__}.of() to construct — direct instantiation bypasses sanitization"
        )

    def __deepcopy__(self, memo: dict) -> SafeStr:
        # Strings are immutable — returning self is correct and avoids __new__ being
        # called by copy.deepcopy(), which would trip the instantiation guard.
        return self

    def __copy__(self) -> SafeStr:
        return self

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeStr:
        """Validate *value* and return a branded instance.

        Raises ``ValueError`` if control characters are found.
        The returned instance carries ``_source`` recording the call site
        (field name and file:line) where validation occurred.
        """
        _check_control_chars(value, field_name)
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeStr:
        """Wrap *value* without re-validating — only for internally constructed strings.

        *reason* must describe why the value can be trusted without validation,
        e.g. ``"DB row — compartments.id"`` or ``"internally constructed"``.
        It is recorded on the instance and shown in DEBUG audit log lines.

        **Never** pass user-supplied data to this method.
        Returns a ``_TrustedSafeStr`` instance that is distinguishable from
        HTTP-validated instances by the audit decorator.
        """
        instance = str.__new__(_TrustedSafeStr, value)
        instance.reason = reason
        return instance

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> _pcs.CoreSchema:
        """Enable use as a FastAPI / Pydantic v2 path or query parameter type.

        FastAPI calls this when the class appears as a parameter type annotation.
        The returned schema validates via ``cls.of()``, so validation errors
        surface as HTTP 422 responses rather than 500s.
        """
        return _pcs.no_info_plain_validator_function(
            lambda v: cls.of(v, "value"),
            serialization=_pcs.to_string_ser_schema(),
        )


# ---------------------------------------------------------------------------
# Specialised branded types
# ---------------------------------------------------------------------------


class SafeSlug(SafeStr):
    """Compartment / timer ID validated against the slug pattern.

    Pattern: ``^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$|^[a-z0-9]$``
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeSlug:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if not SLUG_RE.match(value):
            raise ValueError(
                f"{field_name} must be 1-32 lowercase alphanumeric chars and hyphens, "
                "start and end with alphanumeric"
            )
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeSlug:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeSlug, value)
        instance.reason = reason
        return instance


class SafeUsername(SafeStr):
    """Linux username validated against POSIX conventions.

    Pattern: ``^[a-z_][a-z0-9_-]{0,31}$`` — lowercase, starts with a letter
    or underscore, max 32 chars.  Used for PAM-authenticated usernames
    returned by ``require_auth``.
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeUsername:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if not USERNAME_RE.match(value):
            raise ValueError(
                f"{field_name} must be a valid Linux username "
                "(lowercase alphanumeric, underscore, hyphen; max 32 chars)"
            )
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeUsername:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeUsername, value)
        instance.reason = reason
        return instance


class SafeImageRef(SafeStr):
    """Container image reference validated against the image pattern.

    Pattern: ``^[a-zA-Z0-9][a-zA-Z0-9._\\-/:@]*$``  (max 255 chars)
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeImageRef:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if len(value) > 255:
            raise ValueError(f"{field_name} must be at most 255 characters")
        if not IMAGE_RE.match(value):
            raise ValueError(
                f"{field_name} must be a valid image reference "
                "(alphanumeric, '.', '-', '/', ':', '@')"
            )
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeImageRef:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeImageRef, value)
        instance.reason = reason
        return instance


class SafeUnitName(SafeStr):
    """systemd unit name validated to be safe as a journalctl filter argument.

    Pattern: ``^[a-zA-Z0-9._@\\-]+$``  — rejects systemd journal filter operators.
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeUnitName:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if not UNIT_NAME_RE.match(value):
            raise ValueError(f"{field_name} must contain only alphanumeric chars and '._@-'")
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeUnitName:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeUnitName, value)
        instance.reason = reason
        return instance


class SafeSecretName(SafeStr):
    """Podman secret name validated against the secret name pattern.

    Pattern: ``^[a-zA-Z0-9][a-zA-Z0-9._-]*$``  (max 253 chars)
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeSecretName:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if len(value) > 253:
            raise ValueError(f"{field_name} must be at most 253 characters")
        if not SECRET_NAME_RE.match(value):
            raise ValueError(
                f"{field_name} must start with alphanumeric and contain only "
                "alphanumeric, '.', '_', or '-'"
            )
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeSecretName:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeSecretName, value)
        instance.reason = reason
        return instance


class SafeResourceName(SafeStr):
    """Container/volume/pod/image-unit/timer resource name for quadlet unit files.

    Pattern: ``^[a-z0-9][a-z0-9_-]*$``  (max 63 chars — systemd unit name limit)
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeResourceName:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if len(value) > 63:
            raise ValueError(f"{field_name} must be at most 63 characters")
        if not RESOURCE_NAME_RE.match(value):
            raise ValueError(
                f"{field_name} must start with alphanumeric and contain only "
                "lowercase alphanumeric, '_', or '-'"
            )
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeResourceName:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeResourceName, value)
        instance.reason = reason
        return instance


class SafeWebhookUrl(SafeStr):
    """HTTP/HTTPS webhook URL (max 2048 chars, no whitespace or control chars).

    Pattern: ``^https?://\\S+$``
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeWebhookUrl:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if len(value) > 2048:
            raise ValueError(f"{field_name} must be at most 2048 characters")
        if not WEBHOOK_URL_RE.match(value):
            raise ValueError(f"{field_name} must be a valid http:// or https:// URL")
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeWebhookUrl:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeWebhookUrl, value)
        instance.reason = reason
        return instance


# ---------------------------------------------------------------------------
# Trusted subclasses — defined after the base classes to avoid forward refs.
# Each .trusted() classmethod above returns an instance of the corresponding
# subclass below.  All pass isinstance(x, SafeSlug) etc. normally.
# ---------------------------------------------------------------------------


class _TrustedSafeStr(SafeStr, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeStr."""

    __slots__ = ()


class _TrustedSafeSlug(SafeSlug, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeSlug."""

    __slots__ = ()


class _TrustedSafeUsername(SafeUsername, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeUsername."""

    __slots__ = ()


class _TrustedSafeImageRef(SafeImageRef, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeImageRef."""

    __slots__ = ()


class _TrustedSafeUnitName(SafeUnitName, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeUnitName."""

    __slots__ = ()


class _TrustedSafeSecretName(SafeSecretName, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeSecretName."""

    __slots__ = ()


class _TrustedSafeResourceName(SafeResourceName, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeResourceName."""

    __slots__ = ()


class _TrustedSafeWebhookUrl(SafeWebhookUrl, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeWebhookUrl."""

    __slots__ = ()


class SafePortMapping(SafeStr):
    """Podman/Docker port mapping string.

    Accepted forms (optional ``/tcp`` or ``/udp`` suffix on all):
    - ``80``                     — container port only
    - ``8080:80``                — host:container
    - ``127.0.0.1:8080:80``      — ip:host:container
    - ``:80``                    — OS-assigned host port
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafePortMapping:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if not PORT_MAPPING_RE.match(value):
            raise ValueError(
                f"{field_name} must be a valid port mapping "
                "(e.g. '80', '8080:80', '127.0.0.1:8080:80', ':80', optionally suffixed with /tcp or /udp)"
            )
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafePortMapping:  # type: ignore[override]
        instance = str.__new__(_TrustedSafePortMapping, value)
        instance.reason = reason
        return instance


class _TrustedSafePortMapping(SafePortMapping, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafePortMapping."""

    __slots__ = ()


class SafeUUID(SafeStr):
    """UUID in canonical lowercase hex form.

    Pattern: ``^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$``
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeUUID:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if not UUID_RE.match(value):
            raise ValueError(
                f"{field_name} must be a lowercase UUID (e.g. 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx')"
            )
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeUUID:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeUUID, value)
        instance.reason = reason
        return instance


class SafeSELinuxContext(SafeStr):
    """SELinux file context label (e.g. ``container_file_t``).

    Pattern: ``^[a-zA-Z0-9_]+$``
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeSELinuxContext:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if not SELINUX_CONTEXT_RE.match(value):
            raise ValueError(
                f"{field_name} must contain only alphanumeric characters and underscores"
            )
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeSELinuxContext:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeSELinuxContext, value)
        instance.reason = reason
        return instance


class SafeMultilineStr(SafeStr):
    """String that may contain newlines but must not contain null bytes or carriage returns.

    Use for multi-line freeform content such as Containerfile bodies and raw
    systemd ``[Service]`` directives where ``\\n`` is meaningful but ``\\r``
    and ``\\x00`` are always errors.
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeMultilineStr:  # type: ignore[override]
        if "\x00" in value:
            raise ValueError(f"{field_name} must not contain null bytes")
        if "\r" in value:
            raise ValueError(f"{field_name} must not contain carriage returns")
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeMultilineStr:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeMultilineStr, value)
        instance.reason = reason
        return instance


class SafeAbsPath(SafeStr):
    """Absolute filesystem path with no control characters.

    Must start with ``/`` and contain no ``\\r``, ``\\n``, or ``\\x00``.
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeAbsPath:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if not ABS_PATH_RE.match(value):
            raise ValueError(f"{field_name} must be an absolute path starting with '/'")
        if _DOTDOT_COMPONENT_RE.search(value):
            raise ValueError(f"{field_name} must not contain '..' path traversal components")
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeAbsPath:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeAbsPath, value)
        instance.reason = reason
        return instance


class SafeRedirectPath(SafeStr):
    """Relative redirect path safe from open-redirect attacks.

    Must start with a single ``/``, contain no scheme, netloc, backslashes,
    double-slash prefix, or control characters.  Guarantees the value can be
    used directly in a ``Location`` header without risk of redirecting to an
    external host.
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeRedirectPath:  # type: ignore[override]
        _check_control_chars(value, field_name)
        url_str = value.replace("\\", "")
        parsed = urlparse(url_str)
        if parsed.scheme or parsed.netloc:
            raise ValueError(f"{field_name} must not contain a scheme or host (open redirect)")
        if not url_str.startswith("/") or url_str.startswith("//"):
            raise ValueError(f"{field_name} must be an absolute path starting with a single '/'")
        return _make_validated(cls, url_str, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeRedirectPath:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeRedirectPath, value)
        instance.reason = reason
        return instance


class _TrustedSafeRedirectPath(SafeRedirectPath, _TrustedBase):
    """Trusted (internally constructed) SafeRedirectPath."""

    __slots__ = ()


class SafeTimestamp(SafeStr):
    """ISO 8601 datetime string as produced by SQLite / ``datetime.isoformat()``.

    Validated by ``datetime.fromisoformat()`` — accepts any format that Python's
    standard library considers a valid ISO 8601 datetime string.
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeTimestamp:  # type: ignore[override]
        _check_control_chars(value, field_name)
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a valid ISO 8601 datetime string") from exc
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeTimestamp:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeTimestamp, value)
        instance.reason = reason
        return instance


class _TrustedSafeUUID(SafeUUID, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeUUID."""

    __slots__ = ()


class _TrustedSafeSELinuxContext(SafeSELinuxContext, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeSELinuxContext."""

    __slots__ = ()


class _TrustedSafeMultilineStr(SafeMultilineStr, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeMultilineStr."""

    __slots__ = ()


class _TrustedSafeAbsPath(SafeAbsPath, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeAbsPath."""

    __slots__ = ()


class _TrustedSafeTimestamp(SafeTimestamp, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeTimestamp."""

    __slots__ = ()


class SafeIpAddress(SafeStr):
    """IPv4, IPv6, or CIDR notation (e.g. ``192.168.1.1``, ``::1``, ``10.0.0.0/8``).

    Validates via :func:`ipaddress.ip_network` with ``strict=False`` so both
    host addresses and network prefixes are accepted.  Accepts empty string
    (field not set).
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeIpAddress:  # type: ignore[override]
        import ipaddress

        _check_control_chars(value, field_name)
        if value:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"{field_name} must be a valid IPv4/IPv6 address or CIDR prefix"
                ) from exc
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeIpAddress:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeIpAddress, value)
        instance.reason = reason
        return instance


class _TrustedSafeIpAddress(SafeIpAddress, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeIpAddress."""

    __slots__ = ()


class SafeFormBool(SafeStr):
    """HTML form checkbox/toggle value.

    Accepts empty string (unchecked) or common truthy/falsy form values.
    Pattern: ``^(|true|false|on|off|1|0)$`` (case-insensitive).
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeFormBool:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if value.lower() not in ("", "true", "false", "on", "off", "1", "0"):
            raise ValueError(
                f"{field_name} must be a form boolean (true/false/on/off/1/0 or empty)"
            )
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeFormBool:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeFormBool, value)
        instance.reason = reason
        return instance


class _TrustedSafeFormBool(SafeFormBool, _TrustedBase):
    """Trusted (internally constructed) SafeFormBool."""

    __slots__ = ()


class SafeOctalMode(SafeStr):
    """File permission mode as an octal digit string (e.g. ``644``, ``0755``).

    Pattern: ``^[0-7]{3,4}$``.
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeOctalMode:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if not OCTAL_MODE_RE.match(value):
            raise ValueError(f"{field_name} must be an octal mode string (e.g. 644, 0755)")
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeOctalMode:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeOctalMode, value)
        instance.reason = reason
        return instance


class _TrustedSafeOctalMode(SafeOctalMode, _TrustedBase):
    """Trusted (internally constructed) SafeOctalMode."""

    __slots__ = ()


class SafeTimeDuration(SafeStr):
    """systemd time duration (e.g. ``5min``, ``1h30s``, ``200ms``).

    Accepts empty string (field not set) or one or more ``<digits><unit>``
    groups.  Units: ``usec``, ``msec``, ``sec``, ``s``, ``min``, ``m``,
    ``h``, ``hr``, ``d``, ``w``, ``M``, ``y``.
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeTimeDuration:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if value and not TIME_DURATION_RE.match(value):
            raise ValueError(f"{field_name} must be a systemd time duration (e.g. 5min, 1h30s)")
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeTimeDuration:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeTimeDuration, value)
        instance.reason = reason
        return instance


class _TrustedSafeTimeDuration(SafeTimeDuration, _TrustedBase):
    """Trusted (internally constructed) SafeTimeDuration."""

    __slots__ = ()


class SafeCalendarSpec(SafeStr):
    """systemd OnCalendar expression (e.g. ``daily``, ``Mon *-*-* 00:00:00``).

    Conservative allowlist: alphanumeric, spaces, ``*``, ``/``, ``:``, ``.``,
    ``,``, ``~``, ``-``.  Accepts empty string (field not set).
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeCalendarSpec:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if value and not CALENDAR_SPEC_RE.match(value):
            raise ValueError(f"{field_name} must be a valid systemd OnCalendar expression")
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeCalendarSpec:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeCalendarSpec, value)
        instance.reason = reason
        return instance


class _TrustedSafeCalendarSpec(SafeCalendarSpec, _TrustedBase):
    """Trusted (internally constructed) SafeCalendarSpec."""

    __slots__ = ()


class SafePortStr(SafeStr):
    """Port number as a string (1–65535).

    Accepts empty string (field not set) or a decimal integer in range.
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafePortStr:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if value:
            if not PORT_STR_RE.match(value):
                raise ValueError(f"{field_name} must be a port number (1-65535)")
            port = int(value)
            if port < 1 or port > 65535:
                raise ValueError(f"{field_name} must be a port number (1-65535)")
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafePortStr:  # type: ignore[override]
        instance = str.__new__(_TrustedSafePortStr, value)
        instance.reason = reason
        return instance


class _TrustedSafePortStr(SafePortStr, _TrustedBase):
    """Trusted (internally constructed) SafePortStr."""

    __slots__ = ()


class SafeNetDriver(SafeStr):
    """Podman network driver name.

    Accepts empty string (default) or a known driver: ``bridge``,
    ``macvlan``, ``ipvlan``.
    """

    __slots__ = ()

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeNetDriver:  # type: ignore[override]
        _check_control_chars(value, field_name)
        if value and value not in ("bridge", "macvlan", "ipvlan"):
            raise ValueError(
                f"{field_name} must be a Podman network driver (bridge/macvlan/ipvlan)"
            )
        return _make_validated(cls, value, field_name)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeNetDriver:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeNetDriver, value)
        instance.reason = reason
        return instance


class _TrustedSafeNetDriver(SafeNetDriver, _TrustedBase):
    """Trusted (internally constructed) SafeNetDriver."""

    __slots__ = ()


# ---------------------------------------------------------------------------
# Runtime obligation check
# ---------------------------------------------------------------------------


def require(value: object, *types: type, name: str = "") -> None:
    """Assert that *value* is an instance of one of the given sanitized types.

    Call this at the entry of any service function that reaches critical host
    operations, passing each argument that originates from user input::

        def stop_unit(service_id: SafeSlug, unit: SafeUnitName) -> None:
            sanitized.require(service_id, SafeSlug, name="service_id")
            sanitized.require(unit, SafeUnitName, name="unit")
            ...

    Raises ``TypeError`` — not ``ValueError`` — so it is clearly distinct from
    a user-input validation failure.  A ``TypeError`` here means a programming
    error: a caller passed un-sanitized data to a critical function.

    Prefer ``@sanitized.enforce`` on the function instead of calling this
    manually — it reads the type annotations and inserts these checks
    automatically.
    """
    if not isinstance(value, types):
        label = name or "argument"
        expected = " | ".join(t.__name__ for t in types)
        raise TypeError(
            f"{label}: expected sanitized type ({expected}), "
            f"got raw {type(value).__name__!r} — "
            "upstream caller must sanitize before calling this function"
        )


def enforce(fn: Callable) -> Callable:
    """Decorator that enforces branded types for every string parameter.

    At **decoration time** (import time), raises ``TypeError`` for any parameter
    annotated as plain ``str`` — all string parameters must use a branded type
    (``SafeStr`` or one of its subclasses).  This makes ``@sanitized.enforce``
    a static guard: a bare ``str`` annotation is caught immediately when the
    module loads, not silently ignored until a call arrives.

    At **call time**, calls ``require()`` for every ``SafeStr``-subclass-typed
    parameter to verify that the caller actually passed a branded instance, not
    a plain ``str`` that slipped through type erasure or a dynamic call path::

        @sanitized.enforce
        def create_service_user(service_id: SafeSlug) -> int:
            ...  # no manual require() needed — decoration-time str check +
                 # call-time isinstance check both handled automatically

    Skips parameters named ``self`` / ``cls``, return annotations, and
    non-string types (``int``, ``bool``, ``Connection``, etc.).

    Works transparently on both sync and async functions.  If annotation
    resolution fails (e.g. forward references that cannot be resolved in the
    function's module), the decorator is a no-op and the original function is
    returned unchanged.
    """
    try:
        hints = get_type_hints(fn)
    except Exception:
        return fn

    params = list(inspect.signature(fn).parameters.keys())

    # --- decoration-time check: reject bare `str` or `str | X` annotations --
    _SKIP = {"self", "cls", "return"}
    for param_name in params:
        if param_name in _SKIP:
            continue
        hint = hints.get(param_name)
        if hint is None:
            continue
        # Plain str
        if hint is str:
            raise TypeError(
                f"@sanitized.enforce: parameter '{param_name}' of {fn.__qualname__} "
                f"is annotated as plain str — use a branded type (SafeStr or subclass) instead"
            )
        # str | X  (Python 3.10+ UnionType: types.UnionType)
        if isinstance(hint, types.UnionType) and str in hint.__args__:
            raise TypeError(
                f"@sanitized.enforce: parameter '{param_name}' of {fn.__qualname__} "
                f"contains plain str in a union — use SafeStr | None or a branded subclass instead"
            )
        # Optional[str] / Union[str, X]  (typing.Union)
        origin = typing.get_origin(hint)
        if origin is typing.Union and str in typing.get_args(hint):
            raise TypeError(
                f"@sanitized.enforce: parameter '{param_name}' of {fn.__qualname__} "
                f"contains plain str in a union — use SafeStr | None or a branded subclass instead"
            )
        # Class with own string-typed fields but missing @enforce_model
        if (
            isinstance(hint, type)
            and not issubclass(hint, SafeStr)
            and hint.__dict__.get("__annotations__")  # has own (not inherited) annotations
            and not getattr(hint, "_sanitized_enforce_model", False)
        ):
            raise TypeError(
                f"@sanitized.enforce: parameter '{param_name}' of {fn.__qualname__} "
                f"has type '{hint.__qualname__}' which is not decorated with "
                f"@sanitized.enforce_model — add that decorator to {hint.__qualname__}"
            )

    # --- build call-time require() checks for SafeStr-subclass params -------
    checks: list[tuple[str, type, int]] = [
        (name, hint, idx)
        for idx, name in enumerate(params)
        if (hint := hints.get(name)) is not None
        and isinstance(hint, type)
        and issubclass(hint, SafeStr)
    ]

    if not checks:
        fn._sanitized_enforced = True  # type: ignore[attr-defined]
        return fn

    if asyncio.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def _async_wrapper(*args: object, **kwargs: object) -> object:
            for param_name, typ, idx in checks:
                if idx < len(args):
                    require(args[idx], typ, name=param_name)
                elif param_name in kwargs:
                    require(kwargs[param_name], typ, name=param_name)
                # else: caller omitted the argument — default value will be used; skip
            return await fn(*args, **kwargs)

        _async_wrapper._sanitized_enforced = True  # type: ignore[attr-defined]
        return _async_wrapper

    @functools.wraps(fn)
    def _sync_wrapper(*args: object, **kwargs: object) -> object:
        for param_name, typ, idx in checks:
            if idx < len(args):
                require(args[idx], typ, name=param_name)
            elif param_name in kwargs:
                require(kwargs[param_name], typ, name=param_name)
            # else: caller omitted the argument — default value will be used; skip
        return fn(*args, **kwargs)

    _sync_wrapper._sanitized_enforced = True  # type: ignore[attr-defined]
    return _sync_wrapper


# ---------------------------------------------------------------------------
# Model-level enforce — rejects bare `str` fields in Pydantic models
# ---------------------------------------------------------------------------


def _hint_contains_plain_str(hint: object) -> bool:
    """Return True if *hint* is or contains plain ``str`` at any depth.

    Recurses into union arms (``X | Y``, ``Union[X, Y]``) and generic
    type arguments (``list[X]``, ``dict[K, V]``, ``tuple[X, ...]``, etc.).
    Stops recursing into ``SafeStr`` subclasses — they *are* ``str`` at
    runtime but are explicitly branded and therefore allowed.
    """
    if hint is str:
        return True
    # SafeStr subclass — branded, allowed even though issubclass(x, str) is True
    if isinstance(hint, type) and issubclass(hint, SafeStr):
        return False
    # Python 3.10+ union: X | Y  →  types.UnionType
    if isinstance(hint, types.UnionType):
        return any(_hint_contains_plain_str(a) for a in hint.__args__)
    # Generic aliases: list[X], dict[K,V], Optional[X], Union[X,Y], tuple[X,...], etc.
    args = typing.get_args(hint)
    if args:
        return any(_hint_contains_plain_str(a) for a in args)
    return False


def enforce_model(cls: type) -> type:
    """Class decorator that rejects bare ``str`` annotations in a model class.

    Apply to every model class to get the same compile-time (import-time)
    protection that ``@sanitized.enforce`` provides for function parameters::

        @sanitized.enforce_model
        class CompartmentCreate(BaseModel):
            name: SafeSlug                      # OK
            tags: list[SafeStr] = []            # OK
            label: str                          # TypeError at import time
            mapping: dict[str, str] = {}        # TypeError — str in type args

    Checks the full type expression recursively, so ``list[dict[str, str]]``
    is caught even though the top-level annotation is ``list``.

    Only inspects ``__annotations__`` declared directly on *cls* — inherited
    annotations from parent classes are not re-checked (they were checked when
    the parent was decorated).
    """
    own = cls.__annotations__  # only this class's own declared annotations
    for field_name, hint in own.items():
        if _hint_contains_plain_str(hint):
            raise TypeError(
                f"@sanitized.enforce_model: field '{field_name}' of {cls.__qualname__} "
                f"contains plain str — use a branded type (SafeStr or subclass) instead; "
                f"annotation: {hint!r}"
            )
    cls._sanitized_enforce_model = True  # type: ignore[attr-defined]
    return cls


# ---------------------------------------------------------------------------
# Provenance helper — used by host.audit
# ---------------------------------------------------------------------------


def provenance(value: object) -> tuple[str, str] | None:
    """Return ``(type_name, provenance_label)`` for a branded value, or ``None``.

    ``type_name`` is the public branded class name (e.g. ``"SafeSlug"``).
    ``provenance_label`` is one of:

    - ``"validated:<field> @ <file>:<line>"`` — created via ``.of()``; records
      the field name and call site where validation occurred.
    - ``"trusted:<reason>"`` — created via ``.trusted()``; records the stated
      reason why validation was skipped.

    Returns ``None`` if *value* is not a branded type instance.
    """
    if not isinstance(value, SafeStr):
        return None
    is_trusted = isinstance(value, _TrustedBase)
    if is_trusted:
        label = f"trusted:{value.reason}"  # type: ignore[union-attr]
    else:
        source = getattr(value, "_source", "unknown")
        label = f"validated:{source}"
    # Walk the MRO to find the first public branded class (no leading underscore).
    for cls in type(value).__mro__:
        if cls is SafeStr or (
            issubclass(cls, SafeStr)
            and not issubclass(cls, _TrustedBase)
            and not cls.__name__.startswith("_")
        ):
            return cls.__name__, label
    return type(value).__name__, label


def resolve_safe_path(base: str, path: str, *, absolute: bool = False) -> str:
    """Resolve *path* within *base*, raising ``ValueError`` on traversal.

    Uses ``os.path.realpath()`` to resolve symlinks and normalise segments,
    then verifies the result stays within *base*.

    Parameters
    ----------
    base:
        The trusted root directory.
    path:
        The user-supplied path component.
    absolute:
        If ``True``, treat *path* as an absolute filesystem path and verify
        it is contained within *base*.  If ``False`` (default), treat *path*
        as relative to *base* (leading ``/`` is stripped).
    """
    real_base = os.path.realpath(base)
    if not path or path in ("/", "."):
        return real_base
    if absolute:
        target = os.path.realpath(path)
    else:
        target = os.path.realpath(os.path.join(real_base, path.lstrip("/")))
    if target != real_base and not target.startswith(real_base + os.sep):
        raise ValueError("Path escapes base directory")
    return target


def log_safe(v: object) -> str:
    """Return a log-safe string, escaping CR/LF to prevent log-injection attacks.

    Pass any user-supplied value through this function before including it in a
    log message.  Branded ``SafeStr`` instances have already been validated
    against regexes that exclude control characters, but calling ``log_safe``
    makes the sanitization explicit and statically verifiable by tools such as
    CodeQL.
    """
    return str(v).replace("\r", "\\r").replace("\n", "\\n")

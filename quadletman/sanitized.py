"""Branded string types for defense-in-depth input sanitization.

These types are ``str`` subclasses that can *only* be constructed through their
validation constructor (``ClassName.of(value)``).  Direct instantiation raises
``TypeError``.  Holding an instance of any type in this module is proof that
the corresponding sanitization contract has been fulfilled.

Defense-in-depth usage
----------------------
Layer 1 — HTTP boundary:
    Pydantic field validators call ``SafeSlug.of(v)`` / ``SafeStr.of(v)`` so
    that model instances carry typed-and-proof strings, not plain ``str``.

Layer 2 — Service signatures:
    Public service functions accept ``SafeSlug`` / ``SafeStr`` in their
    signatures.  This documents and enforces the upstream obligation: callers
    must validate before calling.

Layer 3 — Runtime assertion at service entry:
    At the top of critical service functions call ``sanitized.require()`` so
    that the downstream function verifies the upstream has fulfilled its
    obligation even if type erasure or a dynamic call path is used::

        def stop_unit(service_id: SafeSlug, unit: SafeUnitName) -> None:
            require(service_id, SafeSlug)
            require(unit, SafeUnitName)
            ...

``require()`` raises ``TypeError`` when called with an un-sanitized plain
``str``, giving a clear error that points developers at the sanitization gap.

Bypassing sanitization
----------------------
Occasionally the application itself constructs a value from trusted internal
components (e.g. building a unit name from a DB-stored slug).  In that case
use ``ClassName.trusted(value, reason)`` which skips regex validation but still
wraps the value in the branded type.  *reason* must explain why the value needs
no validation (e.g. ``"DB row — compartments.id"``).  Never use ``trusted()``
on user-supplied data.

Provenance tracking
-------------------
Instances created via ``.of()`` are plain instances of the branded class.
Instances created via ``.trusted()`` are instances of a private ``_Trusted*``
subclass that also inherits from ``_TrustedBase``.  Both pass
``isinstance(x, SafeSlug)`` checks and ``require()``.

The ``@host.audit`` decorator uses this marker to emit DEBUG-level provenance
lines showing which branded-type parameters were HTTP-validated versus
internally trusted, making the full call visible in the audit log when DEBUG
logging is enabled::

    DEBUG  PARAMS USER_CREATE   service_id=SafeSlug(validated)
    DEBUG  PARAMS UNIT_START    service_id=SafeSlug(trusted) unit=SafeUnitName(trusted)
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Validation patterns — kept here as the single source of truth.
# models.py imports from here rather than re-defining them.
# ---------------------------------------------------------------------------

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$|^[a-z0-9]$")
IMAGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-/:@]*$")
SECRET_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
UNIT_NAME_RE = re.compile(r"^[a-zA-Z0-9._@\-]+$")
CONTROL_CHARS_RE = re.compile(r"[\r\n\x00]")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_control_chars(v: str, field_name: str) -> None:
    if CONTROL_CHARS_RE.search(v):
        raise ValueError(f"{field_name} must not contain newline, carriage return, or null byte")


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

    __slots__ = ()

    def __new__(cls, *args, **kwargs):  # type: ignore[override]
        raise TypeError(
            f"Use {cls.__name__}.of() to construct — direct instantiation bypasses sanitization"
        )

    @classmethod
    def of(cls, value: str, field_name: str = "value") -> SafeStr:
        """Validate *value* and return a branded instance.

        Raises ``ValueError`` if control characters are found.
        """
        _check_control_chars(value, field_name)
        return str.__new__(cls, value)

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
        return str.__new__(cls, value)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeSlug:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeSlug, value)
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
        return str.__new__(cls, value)

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
        return str.__new__(cls, value)

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
        return str.__new__(cls, value)

    @classmethod
    def trusted(cls, value: str, reason: str) -> SafeSecretName:  # type: ignore[override]
        instance = str.__new__(_TrustedSafeSecretName, value)
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


class _TrustedSafeImageRef(SafeImageRef, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeImageRef."""

    __slots__ = ()


class _TrustedSafeUnitName(SafeUnitName, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeUnitName."""

    __slots__ = ()


class _TrustedSafeSecretName(SafeSecretName, _TrustedBase):
    """Trusted (DB-sourced / internally constructed) SafeSecretName."""

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
    """
    if not isinstance(value, types):
        label = name or "argument"
        expected = " | ".join(t.__name__ for t in types)
        raise TypeError(
            f"{label}: expected sanitized type ({expected}), "
            f"got raw {type(value).__name__!r} — "
            "upstream caller must sanitize before calling this function"
        )


# ---------------------------------------------------------------------------
# Provenance helper — used by host.audit
# ---------------------------------------------------------------------------


def provenance(value: object) -> tuple[str, str] | None:
    """Return ``(type_name, provenance_label)`` for a branded value, or ``None``.

    ``type_name`` is the public branded class name (e.g. ``"SafeSlug"``).
    ``provenance_label`` is ``"validated"`` (created via ``.of()``) or
    ``"trusted"`` (created via ``.trusted()``).

    Returns ``None`` if *value* is not a branded type instance.
    """
    if not isinstance(value, SafeStr):
        return None
    is_trusted = isinstance(value, _TrustedBase)
    label = f"trusted:{value.reason}" if is_trusted else "validated"  # type: ignore[union-attr]
    # Walk the MRO to find the first public branded class (no leading underscore).
    for cls in type(value).__mro__:
        if cls is SafeStr or (
            issubclass(cls, SafeStr)
            and not issubclass(cls, _TrustedBase)
            and not cls.__name__.startswith("_")
        ):
            return cls.__name__, label
    return type(value).__name__, label

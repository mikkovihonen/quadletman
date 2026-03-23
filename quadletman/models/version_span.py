"""Podman version lifecycle metadata for Pydantic model fields.

Attach a ``VersionSpan`` instance to any model field via ``typing.Annotated``::

    apparmor_profile: Annotated[SafeStr, VersionSpan(
        introduced=(5, 8, 0),
        quadlet_key="AppArmor",
    )] = SafeStr.trusted("", "default")

The metadata travels with the type annotation and can be extracted at runtime
via ``get_version_spans(ModelClass)`` to drive:

- Route-level validation (``validate_version_spans``)
- Template-level UI gating (``field_availability``)
- Quadlet unit file key gating (``field_availability``)
- Deprecation warnings with migration guidance

Feature-level spans (not tied to a model field) are also defined here as
module-level constants: ``QUADLET``, ``BUILD_UNITS``, ``BUNDLE``, ``PASTA``.
"""

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel

from .choices import FieldChoices

logger = logging.getLogger(__name__)

# Type alias for the (major, minor, patch) version tuple used throughout.
PodmanVersion = tuple[int, int, int]


@dataclass(frozen=True)
class VersionSpan:
    """Podman version lifecycle metadata for a single model field.

    Attributes:
        introduced: The Podman version that first supported this feature.
        quadlet_key: The corresponding key name in the Quadlet unit file
            (e.g. ``"AppArmor"``, ``"PullPolicy"``).  Empty string if the
            field does not map to a unit file key.
        deprecated: The Podman version that deprecated this feature, or
            ``None`` if still current.
        removed: The Podman version that removed this feature, or ``None``
            if not yet removed.
        deprecation_message: Human-readable migration guidance shown when a
            deprecated field is used (e.g. ``"Use NewKey= instead"``).
        replacement: The field name of the successor property on the same
            model, or ``None``.
        value_constraints: Maps specific field *values* to minimum Podman
            versions.  For example ``{"image": (5, 0, 0)}`` means the value
            ``"image"`` requires Podman 5.0+ even though the field itself is
            available on older versions.
    """

    introduced: PodmanVersion
    quadlet_key: str = ""
    deprecated: PodmanVersion | None = None
    removed: PodmanVersion | None = None
    deprecation_message: str | None = None
    replacement: str | None = None
    value_constraints: dict[str, PodmanVersion] | None = field(default=None)


# ---------------------------------------------------------------------------
# Feature-level spans — capabilities not tied to a single model field
# ---------------------------------------------------------------------------

SLIRP4NETNS = VersionSpan(
    introduced=(1, 0, 0),
    deprecated=(5, 7, 0),
    removed=(6, 0, 0),
    deprecation_message="Use pasta networking instead (available since Podman 4.1).",
)
"""slirp4netns rootless networking — deprecated in 5.7, removed in 6.0."""

PASTA = VersionSpan(introduced=(4, 1, 0))
"""Pasta network driver (fast user-mode networking for rootless)."""

QUADLET = VersionSpan(introduced=(4, 4, 0))
"""Basic Quadlet support (.container, .volume, .network, .kube unit files)."""

KUBE_UNITS = VersionSpan(introduced=(4, 4, 0))
""".kube unit files for deploying Kubernetes YAML through Quadlet."""

IMAGE_UNITS = VersionSpan(introduced=(4, 8, 0))
""".image unit files for managing container images."""

POD_UNITS = VersionSpan(introduced=(5, 0, 0))
""".pod unit files for dedicated pod management."""

BUILD_UNITS = VersionSpan(introduced=(5, 2, 0))
""".build quadlet units for Containerfile-based image builds."""

QUADLET_CLI = VersionSpan(introduced=(5, 6, 0))
"""``podman quadlet`` CLI (install, list, print, rm) for managing unit files."""

ARTIFACT_UNITS = VersionSpan(introduced=(5, 7, 0))
""".artifact unit files for OCI artifact management."""

BUNDLE = VersionSpan(introduced=(5, 8, 0))
"""Multi-unit .quadlets bundle format (import/export)."""


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------


def is_field_available(span: VersionSpan, version: PodmanVersion | None) -> bool:
    """Return ``True`` if the field is usable on *version*.

    A field is available when the detected version is at or above
    ``introduced`` and below ``removed`` (if set).  Returns ``False``
    when *version* is ``None`` (Podman not detected).
    """
    if version is None:
        return False
    if version < span.introduced:
        return False
    return span.removed is None or version < span.removed


def is_field_deprecated(span: VersionSpan, version: PodmanVersion | None) -> bool:
    """Return ``True`` if the field is deprecated but not yet removed."""
    if version is None or span.deprecated is None:
        return False
    if version < span.deprecated:
        return False
    return span.removed is None or version < span.removed


def is_value_available(
    span: VersionSpan,
    value: str,
    version: PodmanVersion | None,
) -> bool:
    """Return ``True`` if a specific *value* is available on *version*.

    Checks ``value_constraints`` first; if the value has no constraint (or
    the field has no ``value_constraints`` at all), falls back to
    field-level availability via ``is_field_available``.
    """
    if not is_field_available(span, version):
        return False
    if span.value_constraints is not None and value in span.value_constraints:
        if version is None:
            return False
        return version >= span.value_constraints[value]
    return True


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------


def get_version_spans(model_cls: type[BaseModel]) -> dict[str, VersionSpan]:
    """Extract ``VersionSpan`` metadata from all ``Annotated`` fields.

    Returns a dict mapping field names to their ``VersionSpan``.  Fields
    without a ``VersionSpan`` annotation are omitted.
    """
    spans: dict[str, VersionSpan] = {}
    for name, field_info in model_cls.model_fields.items():
        for meta in field_info.metadata:
            if isinstance(meta, VersionSpan):
                spans[name] = meta
                break
    return spans


def get_field_choices(model_cls: type[BaseModel]) -> dict[str, FieldChoices]:
    """Extract ``FieldChoices`` metadata from all ``Annotated`` fields.

    Returns a dict mapping field names to their ``FieldChoices``.  Fields
    without a ``FieldChoices`` annotation are omitted.
    """
    result: dict[str, FieldChoices] = {}
    for name, field_info in model_cls.model_fields.items():
        for meta in field_info.metadata:
            if isinstance(meta, FieldChoices):
                result[name] = meta
                break
    return result


# ---------------------------------------------------------------------------
# Pre-computed availability dicts (for templates and quadlet writer)
# ---------------------------------------------------------------------------


def field_availability(
    model_cls: type[BaseModel],
    version: PodmanVersion | None,
) -> dict[str, bool]:
    """Return ``{field_name: available}`` for every version-gated field.

    Fields without a ``VersionSpan`` are not included — callers should
    treat missing keys as "always available" (use ``dict.get(name, True)``).
    """
    spans = get_version_spans(model_cls)
    return {name: is_field_available(span, version) for name, span in spans.items()}


def value_availability(
    model_cls: type[BaseModel],
    version: PodmanVersion | None,
) -> dict[str, dict[str, bool]]:
    """Return ``{field_name: {value: available}}`` for value-level gating.

    Only includes fields that have ``value_constraints``.
    """
    result: dict[str, dict[str, bool]] = {}
    for name, span in get_version_spans(model_cls).items():
        if span.value_constraints:
            result[name] = {
                val: is_value_available(span, val, version) for val in span.value_constraints
            }
    return result


def field_tooltips(
    model_cls: type[BaseModel],
    version: PodmanVersion | None,
) -> dict[str, str]:
    """Return ``{field_name: tooltip}`` for every unavailable/deprecated field.

    Fields that are fully available on *version* get an empty string.
    Templates use these tooltips on disabled form inputs to explain *why*
    the control is inactive.
    """
    spans = get_version_spans(model_cls)
    return {name: field_tooltip(span, version) for name, span in spans.items()}


# ---------------------------------------------------------------------------
# Tooltip helpers
# ---------------------------------------------------------------------------


def _fmt_version(v: PodmanVersion) -> str:
    return f"{v[0]}.{v[1]}.{v[2]}"


def field_tooltip(span: VersionSpan, version: PodmanVersion | None) -> str:
    """Return a human-readable tooltip for a version-gated field.

    Examples:
        ``"Requires Podman 5.8.0+"``
        ``"Requires Podman 5.8.0+ (detected: 4.4.0)"``
        ``"Deprecated in Podman 6.0.0 — Use NewKey= instead"``
        ``"Removed in Podman 7.0.0"``
    """
    detected = f" (detected: {_fmt_version(version)})" if version else ""

    if span.removed is not None and version is not None and version >= span.removed:
        msg = f"Removed in Podman {_fmt_version(span.removed)}{detected}"
        if span.deprecation_message:
            msg += f" — {span.deprecation_message}"
        return msg

    if is_field_deprecated(span, version):
        msg = f"Deprecated in Podman {_fmt_version(span.deprecated)}{detected}"  # type: ignore[arg-type]
        if span.deprecation_message:
            msg += f" — {span.deprecation_message}"
        return msg

    if not is_field_available(span, version):
        return f"Requires Podman {_fmt_version(span.introduced)}+{detected}"

    return ""


def value_tooltip(
    span: VersionSpan,
    value: str,
    version: PodmanVersion | None,
) -> str:
    """Return a tooltip for a specific value-level constraint.

    Returns empty string if the value has no constraint or is available.
    """
    if span.value_constraints and value in span.value_constraints:
        min_ver = span.value_constraints[value]
        if version is None or version < min_ver:
            detected = f" (detected: {_fmt_version(version)})" if version else ""
            return f"Requires Podman {_fmt_version(min_ver)}+{detected}"
    return ""


# ---------------------------------------------------------------------------
# Route-level validation
# ---------------------------------------------------------------------------


def validate_version_spans(
    model: BaseModel,
    version: PodmanVersion | None,
    version_str: str,
) -> None:
    """Validate all version-gated fields on *model* against *version*.

    Raises ``fastapi.HTTPException(400)`` if any version-gated field is set
    to a non-default value on an unsupported Podman version.  Logs a warning
    for deprecated fields.

    Also checks ``value_constraints`` — e.g. ``vol_driver="image"`` on
    Podman < 5.0.
    """
    from fastapi import HTTPException

    spans = get_version_spans(type(model))
    defaults = {name: info.default for name, info in type(model).model_fields.items()}

    for field_name, span in spans.items():
        value = getattr(model, field_name)
        default = defaults.get(field_name)

        # Skip fields that are at their default value — nothing to validate.
        if value == default:
            continue

        # Field-level availability check.
        if not is_field_available(span, version):
            key_label = span.quadlet_key or field_name
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Field '{key_label}' requires Podman "
                    f"{_fmt_version(span.introduced)}+ "
                    f"(detected: {version_str})"
                ),
            )

        # Field-level deprecation warning.
        if is_field_deprecated(span, version):
            logger.warning(
                "Field '%s' is deprecated in Podman %s: %s",
                field_name,
                version_str,
                span.deprecation_message or "(no migration guidance)",
            )

        # Value-level constraint check.
        if span.value_constraints and isinstance(value, str) and value in span.value_constraints:
            min_ver = span.value_constraints[value]
            if version is None or version < min_ver:
                key_label = span.quadlet_key or field_name
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Value '{value}' for '{key_label}' requires Podman "
                        f"{_fmt_version(min_ver)}+ "
                        f"(detected: {version_str})"
                    ),
                )


# ---------------------------------------------------------------------------
# Model-level version gating enforcement
# ---------------------------------------------------------------------------


def enforce_model_version_gating(
    cls: type[BaseModel] | None = None,
    *,
    exempt: dict[str, str] | None = None,
) -> type[BaseModel]:
    """Class decorator that requires ``VersionSpan`` on every model field.

    Apply to every Pydantic model in ``models/api`` to ensure new fields
    always include version lifecycle metadata::

        @enforce_model_version_gating(exempt={
            "name": "identity field — not a Quadlet key",
            "image": "reference to image source, not version-dependent",
        })
        @sanitized.enforce_model_safety
        class ContainerCreate(BaseModel):
            name: SafeResourceName                          # exempt (reason above)
            entrypoint: Annotated[SafeStr, VersionSpan(...)]  # OK
            foo: SafeStr = ...                              # TypeError at import time

    Only inspects annotations declared directly on *cls* — inherited fields
    from parent classes are not re-checked.

    Parameters:
        exempt: Maps field names to human-readable reasons explaining why
            each field does not need a ``VersionSpan``.  Every exemption
            must state its rationale so that a code auditor can evaluate
            it without consulting external resources.
    """
    if exempt is None:
        exempt = {}

    def _wrap(klass: type[BaseModel]) -> type[BaseModel]:
        own = klass.__annotations__
        for field_name, hint in own.items():
            if field_name in exempt:
                continue
            # Walk Annotated metadata looking for VersionSpan
            has_span = False
            origin = getattr(hint, "__class__", None)
            if origin is not None and getattr(hint, "__metadata__", None) is not None:
                for meta in hint.__metadata__:
                    if isinstance(meta, VersionSpan):
                        has_span = True
                        break
            if not has_span:
                raise TypeError(
                    f"@enforce_model_version_gating: field '{field_name}' of "
                    f"{klass.__qualname__} is missing a VersionSpan annotation. "
                    f"Add Annotated[<type>, VersionSpan(introduced=...)] or add "
                    f"'{field_name}' to the exempt dict with a reason string."
                )
        return klass

    if cls is not None:
        # Called as @enforce_model_version_gating without arguments
        return _wrap(cls)
    # Called as @enforce_model_version_gating(exempt=...)
    return _wrap  # type: ignore[return-value]

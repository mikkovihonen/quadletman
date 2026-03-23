"""Field choice metadata for Pydantic model fields.

Attach a ``FieldChoices`` instance to any model field via ``typing.Annotated``
alongside ``VersionSpan``::

    restart_policy: Annotated[
        SafeRestartPolicy,
        VersionSpan(introduced=(4, 4, 0)),
        RESTART_POLICY_CHOICES,
    ] = SafeRestartPolicy.trusted("always", "default")

Static choices are known at code time; dynamic choices (``dynamic=True``) are
populated at request time from runtime sources (e.g. ``podman info``, DB queries).
Both produce the same template-ready format via ``choices_for_template()``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldChoice:
    """A single allowed value for a select/enum field.

    Attributes:
        value: The programmatic value (e.g. ``"always"``, ``"on-failure"``).
        label: English display label used as a gettext key — templates render
            it with ``{{ _(opt.label) }}``.
        is_default: Whether this value should be pre-selected when creating a
            new resource.
    """

    value: str
    label: str
    is_default: bool = False


@dataclass(frozen=True)
class FieldChoices:
    """Select-field choice metadata for a model field.

    Attributes:
        choices: Static ``(value, label)`` entries.  ``None`` when
            ``dynamic=True`` — the actual values are supplied at render time.
        default_value: The value to pre-select (alternative to setting
            ``FieldChoice.is_default`` on a single entry).
        empty_label: Label for the empty-string (``""``) option prepended to
            the list.  ``None`` means no empty option is rendered.
        dynamic: If ``True``, ``choices`` is ignored at annotation time and
            must be supplied at render time via the template context.  The
            annotation still carries ``empty_label`` and ``default_value``.
    """

    choices: tuple[FieldChoice, ...] | None = None
    default_value: str = ""
    empty_label: str | None = None
    dynamic: bool = False


def choices_to_frozenset(fc: FieldChoices) -> frozenset[str]:
    """Derive the allowed-value set for branded type validation.

    Includes ``""`` when ``empty_label`` is set (i.e. the empty string is a
    valid submission meaning "not set / use default").
    """
    values: set[str] = set()
    if fc.choices:
        values.update(c.value for c in fc.choices)
    if fc.empty_label is not None:
        values.add("")
    return frozenset(values)


# ---------------------------------------------------------------------------
# Gettext extraction marker
# ---------------------------------------------------------------------------


def N_(s: str) -> str:
    """Mark a string for gettext extraction without translating it.

    Babel extracts ``N_("...")`` calls at scan time.  The actual translation
    happens later in the Jinja2 template via ``{{ _(opt.label) }}``.
    """
    return s


# ---------------------------------------------------------------------------
# Static choice constants — single source of truth
# ---------------------------------------------------------------------------

RESTART_POLICY_CHOICES = FieldChoices(
    choices=(
        FieldChoice("always", N_("always"), is_default=True),
        FieldChoice("on-failure", N_("on-failure")),
        FieldChoice("unless-stopped", N_("unless-stopped")),
        FieldChoice("no", N_("no")),
    ),
)

PULL_POLICY_CHOICES = FieldChoices(
    choices=(
        FieldChoice("always", N_("always")),
        FieldChoice("missing", N_("missing")),
        FieldChoice("never", N_("never")),
        FieldChoice("newer", N_("newer")),
    ),
    empty_label=N_("default"),
)

AUTO_UPDATE_POLICY_CHOICES = FieldChoices(
    choices=(
        FieldChoice("registry", N_("registry — pull newer image on update")),
        FieldChoice("local", N_("local — rebuild when local image changes")),
    ),
    empty_label=N_("Disabled"),
)

HEALTH_ON_FAILURE_CHOICES = FieldChoices(
    choices=(
        FieldChoice("kill", N_("kill — systemd restarts via policy")),
        FieldChoice("restart", N_("restart — podman restarts")),
        FieldChoice("stop", N_("stop")),
    ),
    empty_label=N_("none (log only)"),
)

EVENT_TYPE_CHOICES = FieldChoices(
    choices=(
        FieldChoice("on_start", N_("Container started")),
        FieldChoice("on_stop", N_("Container stopped")),
        FieldChoice("on_failure", N_("Container failed"), is_default=True),
        FieldChoice("on_restart", N_("Container restarting")),
        FieldChoice("on_unexpected_process", N_("Unexpected process detected")),
        FieldChoice("on_unexpected_connection", N_("Unexpected connection detected")),
    ),
)

SELINUX_CONTEXT_CHOICES = FieldChoices(
    choices=(
        FieldChoice("container_file_t", "container_file_t", is_default=True),
        FieldChoice("svirt_sandbox_file_t", "svirt_sandbox_file_t"),
        FieldChoice("public_content_rw_t", "public_content_rw_t"),
    ),
)

NET_DRIVER_CHOICES = FieldChoices(
    choices=(
        FieldChoice("bridge", "bridge"),
        FieldChoice("macvlan", "macvlan"),
        FieldChoice("ipvlan", "ipvlan"),
    ),
    empty_label=N_("default"),
)

DIRECTION_CHOICES = FieldChoices(
    choices=(
        FieldChoice("outbound", N_("from container")),
        FieldChoice("inbound", N_("to container")),
    ),
    empty_label=N_("any"),
)

PROTO_CHOICES = FieldChoices(
    choices=(
        FieldChoice("tcp", "tcp"),
        FieldChoice("udp", "udp"),
        FieldChoice("icmp", "icmp"),
    ),
    empty_label=N_("any"),
)

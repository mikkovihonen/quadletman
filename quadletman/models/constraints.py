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


@dataclass(frozen=True)
class FieldConstraints:
    """Value constraint metadata for a model field.

    Attach via ``typing.Annotated`` alongside ``VersionSpan`` and/or
    ``FieldChoices``::

        memory_limit: Annotated[
            SafeByteSize,
            VersionSpan(introduced=(4, 4, 0), quadlet_key="MemoryMax"),
            FieldConstraints(placeholder=N_("512m"), label_hint=N_("hard max, e.g. 512m")),
        ] = SafeByteSize.trusted("", "default")

    Attributes:
        min: Minimum numeric value (renders as HTML ``min=`` attribute).
        max: Maximum numeric value (renders as HTML ``max=`` attribute).
        step: Step increment (renders as HTML ``step=`` attribute).
        minlength: Minimum string length (HTML ``minlength=``).
        maxlength: Maximum string length (HTML ``maxlength=``).
        html_pattern: Regex for client-side validation (HTML ``pattern=``).
            Omit ``^...$`` anchors — HTML5 wraps in ``^(?:...)$`` automatically.
            This is a simplified pattern for the browser; the branded type regex
            remains the authoritative server-side validator.
        placeholder: Format hint shown in the input (e.g. ``"512m"``).
            Wrap with ``N_()`` for gettext extraction.
        label_hint: Parenthetical appended to the label
            (e.g. ``"hard max, e.g. 512m"``).  Wrap with ``N_()`` for
            gettext extraction.  Templates render with ``{{ _(...) }}``.
        description: Short translatable description of what the field does
            (e.g. ``"Maximum memory the container can use"``).  Wrap with
            ``N_()`` for gettext extraction.  Shown as help text below form
            inputs and alongside version tooltips in the Podman features list.
        pattern_error: Custom browser validation message shown when the
            ``html_pattern`` fails (replaces the generic "Please match the
            requested format").  Wrap with ``N_()`` for gettext extraction.
            Templates render with ``{{ _(...) }}`` and emit as
            ``data-pattern-error=`` attribute; a global JS listener calls
            ``setCustomValidity()`` on ``invalid`` events.
    """

    min: int | float | None = None
    max: int | float | None = None
    step: int | float | None = None
    minlength: int | None = None
    maxlength: int | None = None
    html_pattern: str | None = None
    placeholder: str | None = None
    label_hint: str | None = None
    description: str | None = None
    pattern_error: str | None = None


# ---------------------------------------------------------------------------
# Validation pattern strings — single source of truth.
# sanitized.py compiles these into regex objects.  FieldConstraints references
# them as html_pattern values.  HTML5 pattern= is implicitly anchored, so
# these strings omit ^...$ anchors.
# ---------------------------------------------------------------------------

SLUG_PATTERN = r"(?:[a-z0-9][a-z0-9-]{0,30}[a-z0-9]|[a-z0-9])"
IMAGE_PATTERN = r"[a-zA-Z0-9][a-zA-Z0-9._\-/:@]*"
SECRET_NAME_PATTERN = r"[a-zA-Z0-9][a-zA-Z0-9._-]*"
UNIT_NAME_PATTERN = r"[a-zA-Z0-9._@\-]+"
RESOURCE_NAME_PATTERN = r"[a-z0-9][a-z0-9_-]*"
WEBHOOK_URL_PATTERN = r"https?://\S+"
PORT_MAPPING_PATTERN = r"(?:([\d.:]+:)?\d{0,5}:\d{1,5}(/tcp|/udp)?|\d{1,5}(/tcp|/udp)?)"
UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
SELINUX_CONTEXT_PATTERN = r"[a-zA-Z0-9_]+"
USERNAME_PATTERN = r"[a-z_][a-z0-9_-]{0,31}"
ABS_PATH_PATTERN = r"/[^\r\n\x00]*"
CONTROL_CHARS_PATTERN = r"[\r\n\x00]"
OCTAL_MODE_PATTERN = r"[0-7]{3,4}"
TIME_DURATION_PATTERN = r"(\d+\s*(usec|msec|sec|s|min|m|h|hr|d|w|M|y)\s*)+"
CALENDAR_SPEC_PATTERN = r"[a-zA-Z0-9 */:.,~\-]+"
PORT_STR_PATTERN = r"\d{1,5}"
INT_OR_EMPTY_PATTERN = r"(-?\d{1,10})?"
BYTE_SIZE_PATTERN = r"(\d+[bBkKmMgGtT]?)?"
LINUX_CAP_PATTERN = r"(ALL|all|CAP_[A-Z][A-Z0-9_]*)"
SIGNAL_NAME_PATTERN = r"(SIG[A-Z][A-Z0-9+]*|\d{1,2})?"
ANNOTATION_PATTERN = r"[a-zA-Z0-9][a-zA-Z0-9._\-/]*=.+"
EXPOSE_PORT_PATTERN = r"\d{1,5}(-\d{1,5})?(/tcp|/udp)?"
USER_GROUP_REF_PATTERN = r"[a-zA-Z_][a-zA-Z0-9_.\-]*|\d{1,10}"
TIMEZONE_PATTERN = r"[A-Za-z][A-Za-z0-9_+\-/]*"
HOST_IP_MAPPING_PATTERN = r"[a-zA-Z0-9][a-zA-Z0-9.\-]*:[0-9a-fA-F.:]+"
ENV_VAR_NAME_PATTERN = r"[a-zA-Z_][a-zA-Z0-9_]*"
HOSTNAME_PATTERN = r"[a-zA-Z0-9]([a-zA-Z0-9.\-]*[a-zA-Z0-9])?"
IDENTIFIER_PATTERN = r"[a-zA-Z0-9][a-zA-Z0-9._/\-]*"
DIGEST_PATTERN = r"[a-zA-Z0-9_+.\-]+:[a-fA-F0-9]+"


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


# ---------------------------------------------------------------------------
# Field constraint constants — single source of truth
# ---------------------------------------------------------------------------

RESOURCE_NAME_CN = FieldConstraints(
    maxlength=63,
    html_pattern=RESOURCE_NAME_PATTERN,
    pattern_error=N_("Lowercase letters, digits, and hyphens"),
)

SECRET_NAME_CN = FieldConstraints(
    maxlength=253,
    html_pattern=SECRET_NAME_PATTERN,
    pattern_error=N_("Letters, digits, dots, and hyphens"),
)

SLUG_CN = FieldConstraints(
    maxlength=32,
    html_pattern=SLUG_PATTERN,
    pattern_error=N_("Lowercase letters, digits, and hyphens (2-32 chars)"),
)

IMAGE_REF_CN = FieldConstraints(
    maxlength=255,
    html_pattern=IMAGE_PATTERN,
    placeholder=N_("docker.io/library/nginx:latest"),
    pattern_error=N_("Image reference (e.g. docker.io/library/nginx:latest)"),
)

WEBHOOK_URL_CN = FieldConstraints(
    maxlength=2048,
    placeholder="https://hooks.example.com/…",
)

PORT_NUMBER_CN = FieldConstraints(min=1, max=65535)

BYTE_SIZE_CN = FieldConstraints(
    maxlength=20,
    html_pattern=BYTE_SIZE_PATTERN,
    pattern_error=N_("Number with optional unit (e.g. 512m, 1G)"),
)

TIME_DURATION_CN = FieldConstraints(
    maxlength=50,
    html_pattern=TIME_DURATION_PATTERN,
    pattern_error=N_("Duration (e.g. 30s, 5min, 1h)"),
)

ABS_PATH_CN = FieldConstraints(
    maxlength=4096,
    html_pattern=ABS_PATH_PATTERN,
    pattern_error=N_("Absolute path starting with /"),
)

INT_OR_EMPTY_CN = FieldConstraints(
    maxlength=11,
    html_pattern=INT_OR_EMPTY_PATTERN,
    pattern_error=N_("Integer or empty"),
)

PORT_MAPPING_CN = FieldConstraints(
    maxlength=50,
    html_pattern=PORT_MAPPING_PATTERN,
    pattern_error=N_("Port mapping (e.g. 8080:80/tcp)"),
)

LINUX_CAP_CN = FieldConstraints(
    maxlength=30,
    html_pattern=LINUX_CAP_PATTERN,
    pattern_error=N_("Linux capability (e.g. CAP_NET_ADMIN, ALL)"),
)

SIGNAL_NAME_CN = FieldConstraints(
    maxlength=20,
    html_pattern=SIGNAL_NAME_PATTERN,
    pattern_error=N_("Signal name or number (e.g. SIGTERM, 9)"),
)

CALENDAR_SPEC_CN = FieldConstraints(
    maxlength=200,
    html_pattern=CALENDAR_SPEC_PATTERN,
    pattern_error=N_("Calendar expression (e.g. daily, *-*-* 02:00:00)"),
)

IP_ADDRESS_CN = FieldConstraints(maxlength=45)

UNIT_NAME_CN = FieldConstraints(
    maxlength=256,
    html_pattern=UNIT_NAME_PATTERN,
    pattern_error=N_("Systemd unit name"),
)

ANNOTATION_CN = FieldConstraints(
    maxlength=512,
    html_pattern=ANNOTATION_PATTERN,
    pattern_error=N_("key=value annotation"),
)

EXPOSE_PORT_CN = FieldConstraints(
    maxlength=15,
    html_pattern=EXPOSE_PORT_PATTERN,
    pattern_error=N_("Port or range (e.g. 8080, 8080-8090/tcp)"),
)

USER_GROUP_REF_CN = FieldConstraints(
    maxlength=32,
    html_pattern=USER_GROUP_REF_PATTERN,
    pattern_error=N_("Username, group name, or numeric ID"),
)

TIMEZONE_CN = FieldConstraints(
    maxlength=40,
    html_pattern=TIMEZONE_PATTERN,
    pattern_error=N_("IANA timezone (e.g. UTC, Europe/Helsinki)"),
)

HOST_IP_MAPPING_CN = FieldConstraints(
    maxlength=260,
    html_pattern=HOST_IP_MAPPING_PATTERN,
    pattern_error=N_("hostname:IP (e.g. myhost:10.0.0.1)"),
)

ENV_VAR_NAME_CN = FieldConstraints(
    maxlength=255,
    html_pattern=ENV_VAR_NAME_PATTERN,
    pattern_error=N_("Variable name: letters, digits, underscores"),
)

HOSTNAME_CN = FieldConstraints(
    maxlength=253,
    html_pattern=HOSTNAME_PATTERN,
    pattern_error=N_("Hostname (e.g. myhost, db.example.com)"),
)

IDENTIFIER_CN = FieldConstraints(
    maxlength=128,
    html_pattern=IDENTIFIER_PATTERN,
    pattern_error=N_("Identifier: letters, digits, dots, hyphens"),
)

DIGEST_CN = FieldConstraints(
    maxlength=136,
    html_pattern=DIGEST_PATTERN,
    pattern_error=N_("OCI digest (e.g. sha256:abc123...)"),
)

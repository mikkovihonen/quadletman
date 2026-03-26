"""Router helpers package — re-exports all helpers for a single import point."""

from .common import (
    EXEC_USER_RE,
    MAX_ENVFILE_BYTES,
    MAX_UPLOAD_BYTES,
    choices_for_template,
    comp_ctx,
    field_choices_for_template,
    field_constraints_for_template,
    fmt_bytes,
    get_vol_sizes,
    is_htmx,
    require_auth,
    require_compartment,
    run_blocking,
    toast_trigger,
    validate_version_spans,
)
from .compartments import (
    connection_monitor_ctx,
    notification_hooks_ctx,
    process_monitor_ctx,
    status_dot_context,
)
from .host import read_audit_lines, read_journalctl_lines
from .ui import check_login_rate_limit, record_login_attempt
from .volumes import browse_ctx, get_vol, is_text, mode_bits

__all__ = [
    "EXEC_USER_RE",
    "MAX_ENVFILE_BYTES",
    "MAX_UPLOAD_BYTES",
    "browse_ctx",
    "choices_for_template",
    "check_login_rate_limit",
    "comp_ctx",
    "connection_monitor_ctx",
    "field_choices_for_template",
    "field_constraints_for_template",
    "fmt_bytes",
    "get_vol",
    "get_vol_sizes",
    "is_htmx",
    "is_text",
    "mode_bits",
    "notification_hooks_ctx",
    "process_monitor_ctx",
    "read_audit_lines",
    "record_login_attempt",
    "read_journalctl_lines",
    "require_auth",
    "require_compartment",
    "status_dot_context",
    "toast_trigger",
    "validate_version_spans",
    "run_blocking",
]

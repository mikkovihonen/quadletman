import json as _json
import re
from typing import Literal

from ..sanitized import SafeStr

# Keep the compiled regex accessible under the old private name for internal use.
_CONTROL_CHARS_RE = re.compile(r"[\r\n\x00]")

# Host path prefixes that must not be bind-mounted into containers
_BIND_MOUNT_DENYLIST = (
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/root",
    "/var/lib/quadletman",
    "/run/dbus",
)

_EventType = Literal[
    "on_failure",
    "on_restart",
    "on_start",
    "on_stop",
    "on_unexpected_process",
    "on_unexpected_connection",
]
_Proto = Literal["tcp", "udp"]
_Direction = Literal["outbound", "inbound"]


def _no_control_chars(v: str, field_name: str = "value") -> SafeStr:
    """Reject strings containing control chars and return a ``SafeStr`` instance.

    Returning ``SafeStr`` (a branded ``str`` subclass) is the proof that this
    check has been performed.  Downstream service functions that accept
    ``SafeStr`` parameters can verify with ``sanitized.require()``.
    """
    return SafeStr.of(v, field_name)


def _loads(d: dict, *fields: str) -> None:
    """In-place JSON-decode string values for the given fields."""
    for f in fields:
        v = d.get(f)
        if isinstance(v, str):
            d[f] = _json.loads(v)

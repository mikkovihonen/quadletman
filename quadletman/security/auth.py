import logging
from contextvars import ContextVar

logger = logging.getLogger(__name__)


class NotAuthenticated(Exception):
    pass


# ContextVar holding (username, password) for the current request.
# Set by AdminCredentialMiddleware when a valid session with stored credentials
# exists.  Read by host.py to escalate privileges via the user's sudo.
_admin_credentials: ContextVar[tuple[str, str] | None] = ContextVar(
    "_admin_credentials", default=None
)


def get_admin_credentials() -> tuple[str, str] | None:
    """Return (username, password) for the current request, or None."""
    return _admin_credentials.get()


def set_admin_credentials(creds: tuple[str, str] | None) -> None:
    """Set admin credentials for the current request context."""
    _admin_credentials.set(creds)

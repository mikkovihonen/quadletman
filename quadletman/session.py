import secrets
import time

from .models import sanitized
from .models.sanitized import SafeStr

_SESSION_TTL = (
    8 * 3600
)  # absolute session TTL in seconds; idle TTL is half this value (_SESSION_TTL // 2)
_sessions: dict[str, dict] = {}


@sanitized.enforce
def create_session(username: SafeStr) -> tuple[str, str]:
    """Create a new session and return (session_id, csrf_token)."""
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = time.time()
    _sessions[sid] = {"username": username, "csrf_token": csrf, "created_at": now, "last_seen": now}
    return sid, csrf


@sanitized.enforce
def get_session(sid: SafeStr) -> SafeStr | None:
    s = _sessions.get(sid)
    if not s:
        return None
    now = time.time()
    # Absolute expiry: session cannot live longer than _SESSION_TTL regardless of activity
    if now - s["created_at"] > _SESSION_TTL:
        del _sessions[sid]
        return None
    # Idle expiry: if inactive for more than half the TTL, expire
    if now - s["last_seen"] > _SESSION_TTL // 2:
        del _sessions[sid]
        return None
    s["last_seen"] = now
    return SafeStr.trusted(s["username"], "get_session")


@sanitized.enforce
def delete_session(sid: SafeStr) -> None:
    _sessions.pop(sid, None)

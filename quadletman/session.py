import secrets
import time

_SESSION_TTL = 8 * 3600  # seconds
_sessions: dict[str, dict] = {}


def create_session(username: str) -> str:
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = {"username": username, "last_seen": time.time()}
    return sid


def get_session(sid: str) -> str | None:
    s = _sessions.get(sid)
    if not s:
        return None
    if time.time() - s["last_seen"] > _SESSION_TTL:
        del _sessions[sid]
        return None
    s["last_seen"] = time.time()
    return s["username"]


def delete_session(sid: str) -> None:
    _sessions.pop(sid, None)

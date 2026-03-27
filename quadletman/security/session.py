import asyncio
import logging
import secrets
import time

from cryptography.fernet import Fernet

from ..config.settings import settings
from ..models import sanitized
from ..models.sanitized import SafeStr, SafeUsername
from . import keyring as kring

logger = logging.getLogger(__name__)

_SESSION_TTL = settings.session_ttl
_sessions: dict[str, dict] = {}


@sanitized.enforce
def create_session(
    username: SafeUsername, password: SafeStr = SafeStr.trusted("", "default")
) -> tuple[str, str]:
    """Create a new session and return (session_id, csrf_token).

    When *password* is provided (non-empty), it is encrypted with a per-session
    Fernet key and stored alongside the session.  The encrypted credential is
    used by the privilege-escalation layer (``host.py``) to run admin operations
    via the authenticated user's ``sudo``.

    The password is never logged or written to disk.
    """
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = time.time()
    session_data: dict = {
        "username": username,
        "csrf_token": csrf,
        "created_at": now,
        "last_seen": now,
    }
    if password:
        stored = False
        if kring.is_available():
            key_id = kring.store_credential(sid, str(password).encode("utf-8"), _SESSION_TTL)
            if key_id is not None:
                session_data["_keyring_id"] = key_id
                stored = True
        if not stored:
            key = Fernet.generate_key()
            f = Fernet(key)
            session_data["_cred_key"] = key
            session_data["_cred_enc"] = f.encrypt(str(password).encode("utf-8"))
    _sessions[sid] = session_data
    return sid, csrf


@sanitized.enforce
def get_session(sid: SafeStr) -> SafeUsername | None:
    s = _sessions.get(sid)
    if not s:
        return None
    now = time.time()
    # Absolute expiry: session cannot live longer than _SESSION_TTL regardless of activity
    if now - s["created_at"] > _SESSION_TTL:
        _clear_and_delete(sid, s)
        return None
    # Idle expiry: if inactive for more than half the TTL, expire
    if now - s["last_seen"] > _SESSION_TTL // 2:
        _clear_and_delete(sid, s)
        return None
    s["last_seen"] = now
    return SafeUsername.trusted(s["username"], "get_session")


@sanitized.enforce
def get_session_credentials(sid: SafeStr) -> tuple[str, str] | None:
    """Return (username, password) for the given session, or None.

    Returns None if the session does not exist, has expired, or was created
    without a password (e.g. test auth bypass).
    """
    s = _sessions.get(sid)
    if not s:
        return None
    now = time.time()
    if now - s["created_at"] > _SESSION_TTL:
        _clear_and_delete(sid, s)
        return None
    if now - s["last_seen"] > _SESSION_TTL // 2:
        _clear_and_delete(sid, s)
        return None
    s["last_seen"] = now
    # Try kernel keyring first
    keyring_id = s.get("_keyring_id")
    if keyring_id is not None:
        payload = kring.read_credential(keyring_id)
        if payload is not None:
            return str(s["username"]), payload.decode("utf-8")
        logger.warning("Keyring credential read failed for session — invalidating")
        _clear_and_delete(sid, s)
        return None
    # Fallback: Fernet-encrypted in-memory
    key = s.get("_cred_key")
    enc = s.get("_cred_enc")
    if not key or not enc:
        return None
    try:
        f = Fernet(key)
        password = f.decrypt(enc).decode("utf-8")
    except Exception:
        logger.warning("Session credential decryption failed — invalidating session")
        _clear_and_delete(sid, s)
        return None
    return str(s["username"]), password


@sanitized.enforce
def delete_session(sid: SafeStr) -> None:
    s = _sessions.pop(sid, None)
    if s:
        _clear_credentials(s)


def _clear_credentials(session_data: dict) -> None:
    """Securely clear credential material from session data."""
    keyring_id = session_data.pop("_keyring_id", None)
    if keyring_id is not None:
        kring.revoke_credential(keyring_id)
    session_data.pop("_cred_key", None)
    session_data.pop("_cred_enc", None)


def _clear_and_delete(sid: str, session_data: dict) -> None:
    """Clear credentials and remove the session."""
    _clear_credentials(session_data)
    _sessions.pop(sid, None)


_REAPER_INTERVAL = 300  # seconds between session reaper sweeps (5 minutes)


async def reaper_loop() -> None:
    """Periodically remove expired sessions that were never accessed again."""
    while True:
        await asyncio.sleep(_REAPER_INTERVAL)
        try:
            now = time.time()
            idle_ttl = _SESSION_TTL // 2
            expired = [
                sid
                for sid, s in _sessions.items()
                if now - s["created_at"] > _SESSION_TTL or now - s["last_seen"] > idle_ttl
            ]
            for sid in expired:
                s = _sessions.get(sid)
                if s:
                    _clear_and_delete(sid, s)
            if expired:
                logger.debug("Session reaper cleaned up %d expired session(s)", len(expired))
        except Exception as exc:
            logger.warning("Session reaper error: %s", exc)

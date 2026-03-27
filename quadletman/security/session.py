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

# Separate dict for Fernet encryption keys — kept apart from session data
# so a single memory dump of _sessions does not reveal both key and ciphertext.
_cred_keys: dict[str, bytes] = {}


def _is_expired(s: dict, now: float) -> bool:
    """Check if session is expired (absolute or idle)."""
    return now - s["created_at"] > _SESSION_TTL or now - s["last_seen"] > _SESSION_TTL // 2


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
    now = time.monotonic()
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
            _cred_keys[sid] = key
            session_data["_cred_enc"] = f.encrypt(str(password).encode("utf-8"))
    _sessions[sid] = session_data
    return sid, csrf


@sanitized.enforce
def get_session(sid: SafeStr) -> SafeUsername | None:
    s = _sessions.get(sid)
    if not s:
        return None
    now = time.monotonic()
    if _is_expired(s, now):
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
    now = time.monotonic()
    if _is_expired(s, now):
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
    # Fallback: Fernet-encrypted in-memory (key stored separately in _cred_keys)
    key = _cred_keys.get(sid)
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
        _clear_credentials(sid, s)


def _clear_credentials(sid: str, session_data: dict) -> None:
    """Securely clear credential material from session data."""
    keyring_id = session_data.pop("_keyring_id", None)
    if keyring_id is not None:
        kring.revoke_credential(keyring_id)
    _cred_keys.pop(sid, None)
    session_data.pop("_cred_enc", None)


def _clear_and_delete(sid: str, session_data: dict) -> None:
    """Clear credentials and remove the session."""
    _clear_credentials(sid, session_data)
    _sessions.pop(sid, None)


_REAPER_INTERVAL = 300  # seconds between session reaper sweeps (5 minutes)


async def reaper_loop() -> None:
    """Periodically remove expired sessions that were never accessed again."""
    while True:
        await asyncio.sleep(_REAPER_INTERVAL)
        try:
            now = time.monotonic()
            idle_ttl = _SESSION_TTL // 2
            expired = [
                sid
                for sid, s in _sessions.items()
                if now - s["created_at"] > _SESSION_TTL or now - s["last_seen"] > idle_ttl
            ]
            cleaned = 0
            for sid in expired:
                s = _sessions.get(sid)
                # Re-check expiry to avoid deleting a session that was accessed
                # between the snapshot and this deletion (race condition guard).
                if s and _is_expired(s, time.monotonic()):
                    _clear_and_delete(sid, s)
                    cleaned += 1
            if cleaned:
                logger.debug("Session reaper cleaned up %d expired session(s)", cleaned)
        except Exception as exc:
            logger.warning("Session reaper error: %s", exc)

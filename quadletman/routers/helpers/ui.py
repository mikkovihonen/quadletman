"""UI helpers."""

import time
from collections import defaultdict

from ...config.settings import settings

# Simple in-memory login rate limiter: max N attempts (success or failure)
# per IP per M seconds.  Tracking all attempts — not just failures — prevents
# credential-stuffing attacks that rotate across multiple valid accounts.
_LOGIN_MAX_ATTEMPTS = settings.login_max_attempts
_LOGIN_WINDOW_SECONDS = settings.login_window_seconds
_login_attempts: dict[str, list[float]] = defaultdict(list)


def check_login_rate_limit(ip: str) -> bool:
    """Return True if the IP is allowed to attempt login, False if rate-limited."""
    now = time.monotonic()
    cutoff = now - _LOGIN_WINDOW_SECONDS
    attempts = _login_attempts[ip]
    # Purge expired entries
    _login_attempts[ip] = [t for t in attempts if t > cutoff]
    if not _login_attempts[ip]:
        del _login_attempts[ip]
        return True
    return len(_login_attempts[ip]) < _LOGIN_MAX_ATTEMPTS


def record_login_attempt(ip: str) -> None:
    """Record a login attempt (successful or failed) for rate limiting."""
    _login_attempts[ip].append(time.monotonic())

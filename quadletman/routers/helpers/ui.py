"""UI helpers."""

import time
from collections import defaultdict

from ...config.settings import settings

# Simple in-memory login rate limiter: max N attempts (success or failure)
# per IP per M seconds.  Tracking all attempts — not just failures — prevents
# credential-stuffing attacks that rotate across multiple valid accounts.
#
# Per-username limiting (half the IP budget) blocks distributed attacks that
# target a single account from many source IPs.
_LOGIN_MAX_ATTEMPTS = settings.login_max_attempts
_LOGIN_WINDOW_SECONDS = settings.login_window_seconds
_login_attempts_ip: dict[str, list[float]] = defaultdict(list)
_login_attempts_user: dict[str, list[float]] = defaultdict(list)
_LOGIN_MAX_ATTEMPTS_PER_USER = max(1, _LOGIN_MAX_ATTEMPTS // 2)


def _purge_and_check(store: dict[str, list[float]], key: str, limit: int) -> bool:
    """Purge expired entries for *key* and return True if under *limit*."""
    now = time.monotonic()
    cutoff = now - _LOGIN_WINDOW_SECONDS
    store[key] = [t for t in store[key] if t > cutoff]
    if not store[key]:
        del store[key]
        return True
    return len(store[key]) < limit


def check_login_rate_limit(ip: str, username: str = "") -> bool:
    """Return True if the IP (and optionally user) is allowed to attempt login."""
    ip_ok = _purge_and_check(_login_attempts_ip, ip, _LOGIN_MAX_ATTEMPTS)
    if username:
        user_ok = _purge_and_check(_login_attempts_user, username, _LOGIN_MAX_ATTEMPTS_PER_USER)
        return ip_ok and user_ok
    return ip_ok


def record_login_attempt(ip: str, username: str = "") -> None:
    """Record a login attempt (successful or failed) for rate limiting."""
    now = time.monotonic()
    _login_attempts_ip[ip].append(now)
    if username:
        _login_attempts_user[username].append(now)

"""UI helpers."""

import time
from collections import defaultdict

# Simple in-memory login rate limiter: max 10 attempts (success or failure)
# per IP per 60 seconds.  Tracking all attempts — not just failures — prevents
# credential-stuffing attacks that rotate across multiple valid accounts.
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW_SECONDS = 60
_login_attempts: dict[str, list[float]] = defaultdict(list)


def check_login_rate_limit(ip: str) -> bool:
    """Return True if the IP is allowed to attempt login, False if rate-limited."""
    now = time.monotonic()
    cutoff = now - _LOGIN_WINDOW_SECONDS
    attempts = _login_attempts[ip]
    # Purge expired entries
    _login_attempts[ip] = [t for t in attempts if t > cutoff]
    return len(_login_attempts[ip]) < _LOGIN_MAX_ATTEMPTS


def record_login_attempt(ip: str) -> None:
    """Record a login attempt (successful or failed) for rate limiting."""
    _login_attempts[ip].append(time.monotonic())

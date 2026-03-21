"""UI helpers."""

import time
from collections import defaultdict
from urllib.parse import urlparse

# Simple in-memory login rate limiter: max 5 failed attempts per IP per 60 seconds.
_LOGIN_MAX_ATTEMPTS = 5
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


def record_failed_login(ip: str) -> None:
    _login_attempts[ip].append(time.monotonic())


def safe_next(url: str) -> str:
    """Prevent open redirect — only allow relative paths on this host.

    The input may be user-controlled; we normalize it and ensure that it does not
    specify a scheme or host and is a single-slash-prefixed path.
    """
    if not url:
        return "/"

    # Ensure we are working with a plain string (SafeStr-like objects may be passed).
    url_str = str(url)

    # Normalize backslashes to avoid browser-specific interpretations.
    url_str = url_str.replace("\\", "")

    # Parse the URL to ensure there is no scheme or netloc (host).
    parsed = urlparse(url_str)
    if parsed.scheme or parsed.netloc:
        return "/"

    # Only allow paths starting with a single "/" and not "//".
    if url_str.startswith("/") and not url_str.startswith("//"):
        return url_str

    return "/"

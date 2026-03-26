"""Linux kernel keyring credential storage via ctypes.

Provides process-scoped credential storage using the kernel key retention
service.  When ``libkeyutils.so`` is available on the host, session passwords
are stored in kernel memory (inaccessible via ``/proc/pid/mem``) rather than
in the application's Python heap.

If the library is missing or the keyring subsystem is unavailable, all public
functions degrade gracefully — callers fall back to Fernet-encrypted in-memory
storage.
"""

import ctypes
import ctypes.util
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Kernel keyring constants
# ---------------------------------------------------------------------------
KEY_SPEC_PROCESS_KEYRING = -2

KEYCTL_READ = 11
KEYCTL_REVOKE = 3
KEYCTL_SET_TIMEOUT = 15

# ---------------------------------------------------------------------------
# Library binding (populated by _init())
# ---------------------------------------------------------------------------
_lib: ctypes.CDLL | None = None
_available: bool = False


def _init() -> None:
    """Try to load libkeyutils and verify the keyring subsystem works."""
    global _lib, _available  # noqa: PLW0603

    path = ctypes.util.find_library("keyutils")
    if path is None:
        logger.info("libkeyutils not found — credential keyring disabled")
        return

    try:
        lib = ctypes.CDLL(path, use_errno=True)
    except OSError as exc:
        logger.info("Failed to load libkeyutils (%s) — credential keyring disabled", exc)
        return

    # int32_t add_key(const char *type, const char *description,
    #                 const void *payload, size_t plen, int32_t keyring)
    lib.add_key.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_int32,
    ]
    lib.add_key.restype = ctypes.c_int32

    # long keyctl(int option, ...)
    # We call it with varying args per operation; set a generic signature.
    lib.keyctl.restype = ctypes.c_long

    # Probe: add a key and immediately revoke it to confirm the subsystem works.
    probe_payload = b"probe"
    key_id = lib.add_key(
        b"user",
        b"qm:probe",
        probe_payload,
        len(probe_payload),
        KEY_SPEC_PROCESS_KEYRING,
    )
    if key_id < 0:
        logger.info(
            "Kernel keyring probe failed (add_key returned %d) — credential keyring disabled",
            key_id,
        )
        return

    lib.keyctl(KEYCTL_REVOKE, ctypes.c_int32(key_id))

    _lib = lib
    _available = True
    logger.info("Kernel keyring available — session credentials stored in process keyring")


_init()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """Return True if the kernel keyring is usable for credential storage."""
    return _available


def store_credential(session_id: str, payload: bytes, timeout_seconds: int) -> int | None:
    """Store *payload* in the process keyring.

    Returns the key serial number on success, or ``None`` on failure.
    The key is automatically expired by the kernel after *timeout_seconds*.
    """
    if _lib is None:
        return None

    description = f"qm:cred:{session_id}".encode()
    key_id = _lib.add_key(
        b"user",
        description,
        payload,
        len(payload),
        KEY_SPEC_PROCESS_KEYRING,
    )
    if key_id < 0:
        logger.warning("Keyring add_key failed (returned %d) for session", key_id)
        return None

    rc = _lib.keyctl(KEYCTL_SET_TIMEOUT, ctypes.c_int32(key_id), ctypes.c_uint(timeout_seconds))
    if rc < 0:
        logger.warning("Keyring set_timeout failed (returned %d) — revoking key", rc)
        _lib.keyctl(KEYCTL_REVOKE, ctypes.c_int32(key_id))
        return None

    return key_id


def read_credential(key_id: int) -> bytes | None:
    """Read the payload of a previously stored key.

    Returns the raw bytes on success, or ``None`` if the key has been revoked,
    expired, or is otherwise unreadable.
    """
    if _lib is None:
        return None

    # First call: get the payload size (pass NULL buffer with 0 length).
    size = _lib.keyctl(KEYCTL_READ, ctypes.c_int32(key_id), None, ctypes.c_size_t(0))
    if size < 0:
        return None

    # Second call: read the actual payload.
    buf = ctypes.create_string_buffer(size)
    rc = _lib.keyctl(KEYCTL_READ, ctypes.c_int32(key_id), buf, ctypes.c_size_t(size))
    if rc < 0:
        return None

    return buf.raw[:rc]


def revoke_credential(key_id: int) -> bool:
    """Revoke a key, making it immediately unreadable.

    Returns ``True`` on success, ``False`` on failure (e.g. already revoked).
    """
    if _lib is None:
        return False

    rc = _lib.keyctl(KEYCTL_REVOKE, ctypes.c_int32(key_id))
    return rc >= 0

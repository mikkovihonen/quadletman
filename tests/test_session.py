"""Tests for quadletman/session.py — in-memory session store."""

import time
from unittest.mock import patch

import pytest

import quadletman.security.keyring as kring_module
import quadletman.security.session as session_module
from quadletman.models.sanitized import SafeStr, SafeUsername
from quadletman.security.session import (
    create_session,
    delete_session,
    get_session,
    get_session_credentials,
)

_s = lambda v: SafeStr.trusted(v, "test fixture")  # noqa: E731
_u = lambda v: SafeUsername.trusted(v, "test fixture")  # noqa: E731


@pytest.fixture(autouse=True)
def clear_sessions():
    """Ensure a clean session store before and after each test."""
    session_module._sessions.clear()
    session_module._cred_keys.clear()
    yield
    session_module._sessions.clear()
    session_module._cred_keys.clear()


class TestCreateSession:
    def test_returns_two_tokens(self):
        sid, csrf = create_session(_u("alice"))
        assert sid and csrf

    def test_tokens_are_distinct(self):
        sid, csrf = create_session(_u("alice"))
        assert sid != csrf

    def test_successive_sessions_have_different_ids(self):
        sid1, _ = create_session(_u("alice"))
        sid2, _ = create_session(_u("alice"))
        assert sid1 != sid2

    def test_session_stored_in_dict(self):
        sid, _ = create_session(_u("bob"))
        assert sid in session_module._sessions


class TestGetSession:
    def test_returns_username_for_valid_session(self):
        sid, _ = create_session(_u("alice"))
        assert get_session(_s(sid)) == "alice"

    def test_returns_none_for_unknown_sid(self):
        assert get_session(_s("nonexistent")) is None

    def test_updates_last_seen(self):
        sid, _ = create_session(_u("alice"))
        t_before = session_module._sessions[sid]["last_seen"]
        time.sleep(0.01)
        get_session(_s(sid))
        assert session_module._sessions[sid]["last_seen"] > t_before

    def test_absolute_expiry(self, monkeypatch):
        sid, _ = create_session(_u("alice"))
        # Wind clock past the absolute TTL
        future = time.monotonic() + session_module._SESSION_TTL + 1
        monkeypatch.setattr(time, "monotonic", lambda: future)
        assert get_session(_s(sid)) is None
        assert sid not in session_module._sessions

    def test_idle_expiry(self, monkeypatch):
        sid, _ = create_session(_u("alice"))
        # Wind clock past the idle TTL (half of absolute)
        future = time.monotonic() + session_module._SESSION_TTL // 2 + 1
        monkeypatch.setattr(time, "monotonic", lambda: future)
        assert get_session(_s(sid)) is None
        assert sid not in session_module._sessions

    def test_active_session_not_expired_within_idle_window(self, monkeypatch):
        sid, _ = create_session(_u("alice"))
        # Within the idle window but before absolute expiry
        future = time.monotonic() + session_module._SESSION_TTL // 2 - 1
        monkeypatch.setattr(time, "monotonic", lambda: future)
        assert get_session(_s(sid)) == "alice"


class TestDeleteSession:
    def test_delete_removes_session(self):
        sid, _ = create_session(_u("alice"))
        delete_session(_s(sid))
        assert sid not in session_module._sessions

    def test_delete_nonexistent_is_no_op(self):
        delete_session(_s("ghost"))  # must not raise

    def test_get_returns_none_after_delete(self):
        sid, _ = create_session(_u("alice"))
        delete_session(_s(sid))
        assert get_session(_s(sid)) is None


# ---------------------------------------------------------------------------
# Kernel keyring integration tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def keyring_available():
    """Patch keyring to appear available with mock store/read/revoke."""
    stored_keys: dict[int, bytes] = {}
    next_id = [100]

    def mock_store(session_id, payload, timeout):
        key_id = next_id[0]
        next_id[0] += 1
        stored_keys[key_id] = payload
        return key_id

    def mock_read(key_id):
        return stored_keys.get(key_id)

    def mock_revoke(key_id):
        stored_keys.pop(key_id, None)
        return True

    with (
        patch.object(kring_module, "is_available", return_value=True),
        patch.object(kring_module, "store_credential", side_effect=mock_store),
        patch.object(kring_module, "read_credential", side_effect=mock_read),
        patch.object(kring_module, "revoke_credential", side_effect=mock_revoke),
    ):
        yield stored_keys


@pytest.fixture()
def keyring_unavailable():
    """Patch keyring to appear unavailable."""
    with patch.object(kring_module, "is_available", return_value=False):
        yield


class TestCreateSessionKeyring:
    def test_uses_keyring_when_available(self, keyring_available):
        sid, _ = create_session(_u("alice"), password=_s("secret"))
        data = session_module._sessions[sid]
        assert "_keyring_id" in data
        assert sid not in session_module._cred_keys
        assert "_cred_enc" not in data

    def test_falls_back_when_unavailable(self, keyring_unavailable):
        sid, _ = create_session(_u("alice"), password=_s("secret"))
        data = session_module._sessions[sid]
        assert "_keyring_id" not in data
        assert sid in session_module._cred_keys
        assert "_cred_enc" in data

    def test_falls_back_when_store_fails(self):
        with (
            patch.object(kring_module, "is_available", return_value=True),
            patch.object(kring_module, "store_credential", return_value=None),
        ):
            sid, _ = create_session(_u("alice"), password=_s("secret"))
            data = session_module._sessions[sid]
            assert "_keyring_id" not in data
            assert sid in session_module._cred_keys


class TestGetSessionCredentialsKeyring:
    def test_reads_from_keyring(self, keyring_available):
        sid, _ = create_session(_u("alice"), password=_s("s3cret"))
        result = get_session_credentials(_s(sid))
        assert result == ("alice", "s3cret")

    def test_invalidates_on_keyring_read_failure(self, keyring_available):
        sid, _ = create_session(_u("alice"), password=_s("s3cret"))
        # Simulate key being revoked/expired externally
        with patch.object(kring_module, "read_credential", return_value=None):
            result = get_session_credentials(_s(sid))
        assert result is None
        assert sid not in session_module._sessions

    def test_falls_back_to_fernet(self, keyring_unavailable):
        sid, _ = create_session(_u("alice"), password=_s("s3cret"))
        result = get_session_credentials(_s(sid))
        assert result == ("alice", "s3cret")

    def test_returns_none_without_credentials(self, keyring_unavailable):
        sid, _ = create_session(_u("alice"))
        assert get_session_credentials(_s(sid)) is None


class TestDeleteSessionKeyring:
    def test_revokes_keyring_key(self, keyring_available):
        sid, _ = create_session(_u("alice"), password=_s("s3cret"))
        key_id = session_module._sessions[sid]["_keyring_id"]
        assert key_id in keyring_available
        delete_session(_s(sid))
        assert key_id not in keyring_available

    def test_revokes_on_expiry(self, keyring_available, monkeypatch):
        sid, _ = create_session(_u("alice"), password=_s("s3cret"))
        key_id = session_module._sessions[sid]["_keyring_id"]
        future = time.monotonic() + session_module._SESSION_TTL + 1
        monkeypatch.setattr(time, "monotonic", lambda: future)
        get_session_credentials(_s(sid))
        assert key_id not in keyring_available

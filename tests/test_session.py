"""Tests for quadletman/session.py — in-memory session store."""

import time

import pytest

import quadletman.session as session_module
from quadletman.session import create_session, delete_session, get_session


@pytest.fixture(autouse=True)
def clear_sessions():
    """Ensure a clean session store before and after each test."""
    session_module._sessions.clear()
    yield
    session_module._sessions.clear()


class TestCreateSession:
    def test_returns_two_tokens(self):
        sid, csrf = create_session("alice")
        assert sid and csrf

    def test_tokens_are_distinct(self):
        sid, csrf = create_session("alice")
        assert sid != csrf

    def test_successive_sessions_have_different_ids(self):
        sid1, _ = create_session("alice")
        sid2, _ = create_session("alice")
        assert sid1 != sid2

    def test_session_stored_in_dict(self):
        sid, _ = create_session("bob")
        assert sid in session_module._sessions


class TestGetSession:
    def test_returns_username_for_valid_session(self):
        sid, _ = create_session("alice")
        assert get_session(sid) == "alice"

    def test_returns_none_for_unknown_sid(self):
        assert get_session("nonexistent") is None

    def test_updates_last_seen(self):
        sid, _ = create_session("alice")
        t_before = session_module._sessions[sid]["last_seen"]
        time.sleep(0.01)
        get_session(sid)
        assert session_module._sessions[sid]["last_seen"] > t_before

    def test_absolute_expiry(self, monkeypatch):
        sid, _ = create_session("alice")
        # Wind clock past the absolute TTL
        future = time.time() + session_module._SESSION_TTL + 1
        monkeypatch.setattr(time, "time", lambda: future)
        assert get_session(sid) is None
        assert sid not in session_module._sessions

    def test_idle_expiry(self, monkeypatch):
        sid, _ = create_session("alice")
        # Wind clock past the idle TTL (half of absolute)
        future = time.time() + session_module._SESSION_TTL // 2 + 1
        monkeypatch.setattr(time, "time", lambda: future)
        assert get_session(sid) is None
        assert sid not in session_module._sessions

    def test_active_session_not_expired_within_idle_window(self, monkeypatch):
        sid, _ = create_session("alice")
        # Within the idle window but before absolute expiry
        future = time.time() + session_module._SESSION_TTL // 2 - 1
        monkeypatch.setattr(time, "time", lambda: future)
        assert get_session(sid) == "alice"


class TestDeleteSession:
    def test_delete_removes_session(self):
        sid, _ = create_session("alice")
        delete_session(sid)
        assert sid not in session_module._sessions

    def test_delete_nonexistent_is_no_op(self):
        delete_session("ghost")  # must not raise

    def test_get_returns_none_after_delete(self):
        sid, _ = create_session("alice")
        delete_session(sid)
        assert get_session(sid) is None

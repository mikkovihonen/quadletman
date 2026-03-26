"""Tests for quadletman/keyring.py — kernel keyring ctypes binding."""

import ctypes
from unittest.mock import MagicMock, patch

from quadletman.security import keyring as kring


class TestIsAvailable:
    def test_returns_bool(self):
        assert isinstance(kring.is_available(), bool)

    def test_false_when_lib_is_none(self):
        original = kring._lib
        try:
            kring._lib = None
            kring._available = False
            assert kring.is_available() is False
        finally:
            kring._lib = original
            kring._available = original is not None


class TestStoreCredential:
    def test_returns_none_when_unavailable(self):
        with patch.object(kring, "_lib", None):
            assert kring.store_credential("sid", b"secret", 3600) is None

    def test_returns_key_id_on_success(self):
        mock_lib = MagicMock()
        mock_lib.add_key.return_value = 42
        mock_lib.keyctl.return_value = 0
        with patch.object(kring, "_lib", mock_lib):
            result = kring.store_credential("sid123", b"password", 3600)
        assert result == 42
        mock_lib.add_key.assert_called_once_with(
            b"user",
            b"qm:cred:sid123",
            b"password",
            8,
            kring.KEY_SPEC_PROCESS_KEYRING,
        )

    def test_sets_timeout(self):
        mock_lib = MagicMock()
        mock_lib.add_key.return_value = 42
        mock_lib.keyctl.return_value = 0
        with patch.object(kring, "_lib", mock_lib):
            kring.store_credential("sid", b"pw", 7200)
        # keyctl called with SET_TIMEOUT
        call_args = mock_lib.keyctl.call_args
        assert call_args[0][0] == kring.KEYCTL_SET_TIMEOUT

    def test_returns_none_on_add_key_failure(self):
        mock_lib = MagicMock()
        mock_lib.add_key.return_value = -1
        with patch.object(kring, "_lib", mock_lib):
            assert kring.store_credential("sid", b"pw", 3600) is None

    def test_revokes_on_set_timeout_failure(self):
        mock_lib = MagicMock()
        mock_lib.add_key.return_value = 42
        mock_lib.keyctl.side_effect = [-1, 0]
        with patch.object(kring, "_lib", mock_lib):
            assert kring.store_credential("sid", b"pw", 3600) is None
        # Should have called keyctl twice: set_timeout (failed) then revoke
        assert mock_lib.keyctl.call_count == 2


class TestReadCredential:
    def test_returns_none_when_unavailable(self):
        with patch.object(kring, "_lib", None):
            assert kring.read_credential(42) is None

    def test_reads_payload(self):
        payload = b"my-secret-password"
        mock_lib = MagicMock()
        # First call returns size, second call fills buffer
        mock_lib.keyctl.side_effect = [len(payload), len(payload)]
        with (
            patch.object(kring, "_lib", mock_lib),
            patch("ctypes.create_string_buffer") as mock_buf,
        ):
            buf_instance = ctypes.create_string_buffer(len(payload))
            buf_instance.raw = payload
            mock_buf.return_value = buf_instance
            result = kring.read_credential(99)
        assert result == payload

    def test_returns_none_on_size_query_failure(self):
        mock_lib = MagicMock()
        mock_lib.keyctl.return_value = -1
        with patch.object(kring, "_lib", mock_lib):
            assert kring.read_credential(42) is None

    def test_returns_none_on_read_failure(self):
        mock_lib = MagicMock()
        # First call returns size, second returns error
        mock_lib.keyctl.side_effect = [10, -1]
        with patch.object(kring, "_lib", mock_lib):
            assert kring.read_credential(42) is None


class TestRevokeCredential:
    def test_returns_false_when_unavailable(self):
        with patch.object(kring, "_lib", None):
            assert kring.revoke_credential(42) is False

    def test_returns_true_on_success(self):
        mock_lib = MagicMock()
        mock_lib.keyctl.return_value = 0
        with patch.object(kring, "_lib", mock_lib):
            assert kring.revoke_credential(42) is True

    def test_returns_false_on_failure(self):
        mock_lib = MagicMock()
        mock_lib.keyctl.return_value = -1
        with patch.object(kring, "_lib", mock_lib):
            assert kring.revoke_credential(42) is False


class TestInit:
    def test_init_with_missing_library(self):
        with patch("ctypes.util.find_library", return_value=None):
            kring._lib = None
            kring._available = False
            kring._init()
            assert kring._available is False

    def test_init_with_failed_cdll(self):
        with (
            patch("ctypes.util.find_library", return_value="libkeyutils.so"),
            patch("ctypes.CDLL", side_effect=OSError("mock")),
        ):
            kring._lib = None
            kring._available = False
            kring._init()
            assert kring._available is False

    def test_init_with_failed_probe(self):
        mock_lib = MagicMock()
        mock_lib.add_key.return_value = -1
        with (
            patch("ctypes.util.find_library", return_value="libkeyutils.so"),
            patch("ctypes.CDLL", return_value=mock_lib),
        ):
            kring._lib = None
            kring._available = False
            kring._init()
            assert kring._available is False

    def test_init_success(self):
        mock_lib = MagicMock()
        mock_lib.add_key.return_value = 99
        mock_lib.keyctl.return_value = 0
        with (
            patch("ctypes.util.find_library", return_value="libkeyutils.so"),
            patch("ctypes.CDLL", return_value=mock_lib),
        ):
            kring._lib = None
            kring._available = False
            kring._init()
            assert kring._available is True
            assert kring._lib is mock_lib

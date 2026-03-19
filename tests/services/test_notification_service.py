"""Tests for quadletman/services/notification_service.py."""

import logging

import httpx
import pytest

import quadletman.services.notification_service as ns
from quadletman.models.sanitized import SafeStr, SafeWebhookUrl

_url = lambda v: SafeWebhookUrl.trusted(v, "test fixture")  # noqa: E731
_secret = lambda v: SafeStr.trusted(v, "test fixture")  # noqa: E731


@pytest.fixture(autouse=True)
def zero_retry_delay(monkeypatch):
    """Patch retry delay to 0 so tests run fast."""
    monkeypatch.setattr(ns, "_RETRY_BASE_DELAY", 0)


def _mock_client(mocker, responses):
    """Return an async-compatible mock httpx.AsyncClient that yields the given responses."""
    resp_iter = iter(responses)

    class _MockResponse:
        def __init__(self, status_code):
            self.status_code = status_code

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            val = next(resp_iter)
            if isinstance(val, Exception):
                raise val
            return _MockResponse(val)

    mocker.patch("httpx.AsyncClient", return_value=_MockClient())
    return _MockClient()


class TestFireWebhookSuccess:
    async def test_posts_once_on_success(self, mocker):
        calls = []

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, **kwargs):
                calls.append(url)
                return type("R", (), {"status_code": 200})()

        mocker.patch("httpx.AsyncClient", return_value=_Client())
        await ns.fire_webhook(_url("http://example.com/hook"), _secret(""), {"event": "on_failure"})
        assert len(calls) == 1

    async def test_sends_secret_header(self, mocker):
        received_headers = {}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, headers=None, **kwargs):
                received_headers.update(headers or {})
                return type("R", (), {"status_code": 200})()

        mocker.patch("httpx.AsyncClient", return_value=_Client())
        await ns.fire_webhook(_url("http://example.com/hook"), _secret("mysecret"), {})
        assert received_headers.get("X-Webhook-Secret") == "mysecret"

    async def test_no_secret_header_when_empty(self, mocker):
        received_headers = {}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, headers=None, **kwargs):
                received_headers.update(headers or {})
                return type("R", (), {"status_code": 200})()

        mocker.patch("httpx.AsyncClient", return_value=_Client())
        await ns.fire_webhook(_url("http://example.com/hook"), _secret(""), {})
        assert "X-Webhook-Secret" not in received_headers


class TestFireWebhookRetry:
    async def test_retries_on_http_error(self, mocker):
        calls = []

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, **kwargs):
                calls.append(1)
                return type("R", (), {"status_code": 503})()

        mocker.patch("httpx.AsyncClient", return_value=_Client())
        await ns.fire_webhook(_url("http://example.com/hook"), _secret(""), {})
        assert len(calls) == ns._MAX_ATTEMPTS

    async def test_succeeds_on_second_attempt(self, mocker):
        call_count = {"n": 0}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, **kwargs):
                call_count["n"] += 1
                code = 503 if call_count["n"] == 1 else 200
                return type("R", (), {"status_code": code})()

        mocker.patch("httpx.AsyncClient", return_value=_Client())
        await ns.fire_webhook(_url("http://example.com/hook"), _secret(""), {})
        assert call_count["n"] == 2

    async def test_retries_on_network_error(self, mocker):
        calls = []

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, **kwargs):
                calls.append(1)
                raise httpx.ConnectError("refused")

        mocker.patch("httpx.AsyncClient", return_value=_Client())
        await ns.fire_webhook(_url("http://example.com/hook"), _secret(""), {})
        assert len(calls) == ns._MAX_ATTEMPTS

    async def test_logs_error_after_exhausting_retries(self, mocker, caplog):
        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, **kwargs):
                return type("R", (), {"status_code": 503})()

        mocker.patch("httpx.AsyncClient", return_value=_Client())
        with caplog.at_level(logging.ERROR, logger="quadletman.services.notification_service"):
            await ns.fire_webhook(_url("http://example.com/hook"), _secret(""), {})
        assert any("failed after" in r.message for r in caplog.records)

    async def test_no_error_log_on_success(self, mocker, caplog):
        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, **kwargs):
                return type("R", (), {"status_code": 200})()

        mocker.patch("httpx.AsyncClient", return_value=_Client())
        with caplog.at_level(logging.ERROR, logger="quadletman.services.notification_service"):
            await ns.fire_webhook(_url("http://example.com/hook"), _secret(""), {})
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)

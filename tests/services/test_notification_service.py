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


# ---------------------------------------------------------------------------
# _check_once — unit state transition handling
# ---------------------------------------------------------------------------


def _make_fake_db_factory(mocker, compartments=None, hooks=None):
    """Build a minimal async db_factory mock for _check_once tests."""
    db = mocker.MagicMock()
    db.execute = mocker.AsyncMock(return_value=None)
    db.commit = mocker.AsyncMock(return_value=None)
    db.rollback = mocker.AsyncMock(return_value=None)

    async def _gen():
        yield db
        # second __anext__ raises StopAsyncIteration (cleanup)
        return
        yield  # type: ignore[misc]  # unreachable yield — makes this an async generator

    mocker.patch(
        "quadletman.services.compartment_manager.list_compartments",
        return_value=compartments or [],
    )
    mocker.patch(
        "quadletman.services.compartment_manager.list_all_notification_hooks",
        return_value=hooks or [],
    )

    def _factory():
        return _gen()

    return _factory


class TestCheckOnce:
    async def test_no_compartments_is_noop(self, mocker):
        factory = _make_fake_db_factory(mocker)
        # Should complete without error
        await ns._check_once(factory)

    async def test_compartment_with_no_containers_is_skipped(self, mocker):
        comp = mocker.MagicMock()
        comp.id = "comp1"
        comp.containers = []
        factory = _make_fake_db_factory(mocker, compartments=[comp])
        await ns._check_once(factory)

    async def test_new_state_cached_on_first_poll(self, mocker):
        comp = mocker.MagicMock()
        comp.id = "testcomp"
        cont = mocker.MagicMock()
        cont.name = "web"
        comp.containers = [cont]
        factory = _make_fake_db_factory(mocker, compartments=[comp])
        mocker.patch(
            "quadletman.services.systemd_manager.get_service_status",
            return_value=[{"container": "web", "active_state": "active"}],
        )
        ns._last_states.clear()
        await ns._check_once(factory)
        assert ns._last_states.get("testcomp/web") == "active"
        ns._last_states.clear()

    async def test_state_transition_fires_webhook(self, mocker):
        comp = mocker.MagicMock()
        comp.id = "testcomp"
        cont = mocker.MagicMock()
        cont.name = "web"
        comp.containers = [cont]

        from quadletman.models.sanitized import SafeStr, SafeWebhookUrl

        hook = mocker.MagicMock()
        hook.compartment_id = "testcomp"
        hook.container_name = "web"
        hook.event_type = "on_failure"
        hook.enabled = True
        hook.webhook_url = SafeWebhookUrl.trusted("https://hooks.example.com/fail", "test")
        hook.webhook_secret = SafeStr.trusted("", "test")

        factory = _make_fake_db_factory(mocker, compartments=[comp], hooks=[hook])
        mocker.patch(
            "quadletman.services.systemd_manager.get_service_status",
            return_value=[{"container": "web", "active_state": "failed"}],
        )
        mocker.patch(
            "quadletman.services.notification_service.fire_webhook",
            new=mocker.MagicMock(return_value=None),
        )
        # Set old state so transition fires
        ns._last_states["testcomp/web"] = "active"
        mocker.patch("asyncio.create_task")
        await ns._check_once(factory)
        ns._last_states.clear()

    async def test_on_start_transition_detected(self, mocker):
        comp = mocker.MagicMock()
        comp.id = "sc"
        cont = mocker.MagicMock()
        cont.name = "db"
        comp.containers = [cont]
        factory = _make_fake_db_factory(mocker, compartments=[comp])
        mocker.patch(
            "quadletman.services.systemd_manager.get_service_status",
            return_value=[{"container": "db", "active_state": "active"}],
        )
        mocker.patch("asyncio.create_task")
        ns._last_states["sc/db"] = "activating"
        await ns._check_once(factory)
        assert ns._last_states.get("sc/db") == "active"
        ns._last_states.clear()

    async def test_on_stop_transition_detected(self, mocker):
        comp = mocker.MagicMock()
        comp.id = "sc"
        cont = mocker.MagicMock()
        cont.name = "api"
        comp.containers = [cont]
        factory = _make_fake_db_factory(mocker, compartments=[comp])
        mocker.patch(
            "quadletman.services.systemd_manager.get_service_status",
            return_value=[{"container": "api", "active_state": "inactive"}],
        )
        mocker.patch("asyncio.create_task")
        ns._last_states["sc/api"] = "active"
        await ns._check_once(factory)
        assert ns._last_states.get("sc/api") == "inactive"
        ns._last_states.clear()

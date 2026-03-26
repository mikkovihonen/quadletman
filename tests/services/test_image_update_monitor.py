"""Tests for image update monitoring (on_image_update webhook event type)."""

import json
import subprocess

import pytest

import quadletman.services.agent_api as agent_api
import quadletman.services.notification_service as ns
from quadletman.models.sanitized import SafeSlug, SafeStr, SafeWebhookUrl
from quadletman.services import systemd_manager

_sid = lambda v: SafeSlug.trusted(v, "test fixture")  # noqa: E731
_url = lambda v: SafeWebhookUrl.trusted(v, "test fixture")  # noqa: E731
_secret = lambda v: SafeStr.trusted(v, "test fixture")  # noqa: E731


@pytest.fixture
def mock_user(mocker):
    """Stub out pwd lookups so _base_cmd works without real system users."""
    mocker.patch("quadletman.services.systemd_manager.get_uid", return_value=1001)
    mocker.patch(
        "quadletman.services.systemd_manager._username",
        return_value=SafeStr.trusted("qm-testcomp", "test fixture"),
    )


# ---------------------------------------------------------------------------
# systemd_manager.auto_update_dry_run
# ---------------------------------------------------------------------------


class TestAutoUpdateDryRun:
    def test_parses_json(self, mocker, mock_user):
        dry_run_output = json.dumps(
            [
                {
                    "Unit": "web.service",
                    "Container": "abc123",
                    "Image": "docker.io/library/nginx:latest",
                    "Policy": "registry",
                    "Updated": "pending",
                }
            ]
        )
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=dry_run_output, stderr=""
            ),
        )
        result = systemd_manager.auto_update_dry_run(_sid("testcomp"))
        assert len(result) == 1
        assert result[0]["Updated"] == "pending"
        assert result[0]["Image"] == "docker.io/library/nginx:latest"

    def test_returns_empty_on_error(self, mocker, mock_user):
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="error"
            ),
        )
        result = systemd_manager.auto_update_dry_run(_sid("testcomp"))
        assert result == []

    def test_returns_empty_on_bad_json(self, mocker, mock_user):
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="not json", stderr=""
            ),
        )
        result = systemd_manager.auto_update_dry_run(_sid("testcomp"))
        assert result == []

    def test_returns_empty_on_empty_output(self, mocker, mock_user):
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        )
        result = systemd_manager.auto_update_dry_run(_sid("testcomp"))
        assert result == []

    def test_returns_empty_on_non_list(self, mocker, mock_user):
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"key": "value"}', stderr=""
            ),
        )
        result = systemd_manager.auto_update_dry_run(_sid("testcomp"))
        assert result == []


# ---------------------------------------------------------------------------
# notification_service.image_update_monitor_loop dedup
# ---------------------------------------------------------------------------


class TestImageUpdateDedup:
    """Test the in-memory dedup dict behaviour directly."""

    def setup_method(self):
        ns._notified_image_updates.clear()

    def test_dedup_key_is_set(self):
        key = "comp1/web/nginx:latest"
        ns._notified_image_updates[key] = True
        assert key in ns._notified_image_updates

    def test_stale_cleanup(self):
        ns._notified_image_updates["comp1/web/nginx:latest"] = True
        ns._notified_image_updates["comp1/api/redis:7"] = True

        still_pending = {"comp1/web/nginx:latest"}
        stale = [k for k in ns._notified_image_updates if k not in still_pending]
        for k in stale:
            del ns._notified_image_updates[k]

        assert "comp1/web/nginx:latest" in ns._notified_image_updates
        assert "comp1/api/redis:7" not in ns._notified_image_updates


# ---------------------------------------------------------------------------
# agent_api.handle_image_updates_report dedup
# ---------------------------------------------------------------------------


class TestAgentImageUpdateDedup:
    """Test the agent_api dedup dict behaviour directly."""

    def setup_method(self):
        agent_api._notified_image_updates.clear()

    def test_stale_cleanup_scoped_to_compartment(self):
        agent_api._notified_image_updates["comp1/web/nginx:latest"] = True
        agent_api._notified_image_updates["comp2/db/postgres:16"] = True

        # Simulate cleanup for comp1 only
        prefix = "comp1/"
        still_pending: set[str] = set()
        stale = [
            k
            for k in agent_api._notified_image_updates
            if k.startswith(prefix) and k not in still_pending
        ]
        for k in stale:
            del agent_api._notified_image_updates[k]

        assert "comp1/web/nginx:latest" not in agent_api._notified_image_updates
        assert "comp2/db/postgres:16" in agent_api._notified_image_updates

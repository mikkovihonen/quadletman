"""Tests for quadletman/config/settings.py — environment variable loading."""

import os


class TestSettingsFromEnv:
    def test_default_settings(self):
        from quadletman.config.settings import Settings

        s = Settings()
        assert str(s.db_path) == "/var/lib/quadletman/quadletman.db"
        assert s.port == 8080

    def test_from_env_picks_up_overrides(self, monkeypatch):
        from quadletman.config.settings import Settings

        monkeypatch.setenv("QUADLETMAN_PORT", "9090")
        monkeypatch.setenv("QUADLETMAN_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("QUADLETMAN_SECURE_COOKIES", "true")
        monkeypatch.setenv("QUADLETMAN_DB_PATH", "/tmp/test.db")
        monkeypatch.setenv("QUADLETMAN_HOST", "127.0.0.1")
        monkeypatch.setenv("QUADLETMAN_VOLUMES_BASE", "/tmp/volumes")
        monkeypatch.setenv("QUADLETMAN_UNIX_SOCKET", "/tmp/qm.sock")
        monkeypatch.setenv("QUADLETMAN_AGENT_SOCKET", "/tmp/agent.sock")
        monkeypatch.setenv("QUADLETMAN_SERVICE_USER_PREFIX", "test-")
        monkeypatch.setenv("QUADLETMAN_ALLOWED_GROUPS", "admin,ops")
        monkeypatch.setenv("QUADLETMAN_TEST_AUTH_USER", "testadmin")
        monkeypatch.setenv("QUADLETMAN_PROCESS_MONITOR_INTERVAL", "30")
        monkeypatch.setenv("QUADLETMAN_CONNECTION_MONITOR_INTERVAL", "45")
        monkeypatch.setenv("QUADLETMAN_IMAGE_UPDATE_CHECK_INTERVAL", "3600")
        s = Settings.from_env()
        assert s.port == 9090
        assert str(s.log_level) == "DEBUG"
        assert s.secure_cookies is True
        assert str(s.db_path) == "/tmp/test.db"
        assert str(s.host) == "127.0.0.1"
        assert str(s.volumes_base) == "/tmp/volumes"
        assert str(s.unix_socket) == "/tmp/qm.sock"
        assert str(s.agent_socket) == "/tmp/agent.sock"
        assert str(s.service_user_prefix) == "test-"
        assert len(s.allowed_groups) == 2
        assert str(s.test_auth_user) == "testadmin"
        assert s.process_monitor_interval == 30
        assert s.connection_monitor_interval == 45
        assert s.image_update_check_interval == 3600

    def test_from_env_defaults_when_no_vars(self, monkeypatch):
        from quadletman.config.settings import Settings

        # Clear any test vars
        for key in list(os.environ):
            if key.startswith("QUADLETMAN_"):
                monkeypatch.delenv(key, raising=False)
        s = Settings.from_env()
        assert s.port == 8080

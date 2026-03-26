import os

from pydantic import BaseModel

from ..models.sanitized import SafeAbsPath, SafeStr


def _env(key: str, default: str = "") -> str:
    """Read a QUADLETMAN_* environment variable."""
    return os.environ.get(f"QUADLETMAN_{key}", default)


class Settings(BaseModel):
    db_path: SafeAbsPath = SafeAbsPath.of("/var/lib/quadletman/quadletman.db", "default")
    volumes_base: SafeAbsPath = SafeAbsPath.of("/var/lib/quadletman/volumes", "default")
    host: SafeStr = SafeStr.of("0.0.0.0", "default")
    port: int = 8080
    unix_socket: SafeStr = SafeStr.trusted(
        "", "default"
    )  # absolute path to Unix domain socket; when set, host/port are ignored
    agent_socket: SafeStr = SafeStr.trusted(
        "/run/quadletman/agent.sock", "default"
    )  # Unix socket for per-user monitoring agents to report to
    service_user_prefix: SafeStr = SafeStr.of("qm-", "default")
    allowed_groups: list[SafeStr] = [SafeStr.of("sudo", "default"), SafeStr.of("wheel", "default")]
    log_level: SafeStr = SafeStr.of("INFO", "default")
    secure_cookies: bool = False  # set True in production when serving over HTTPS
    # non-empty bypasses PAM — for Playwright E2E tests only, never set in production
    test_auth_user: SafeStr = SafeStr.trusted("", "default")
    process_monitor_interval: int = 60  # seconds between process allowlist checks
    connection_monitor_interval: int = 60  # seconds between connection allowlist checks
    image_update_check_interval: int = 21600  # seconds between image update checks (6 hours)
    subprocess_timeout: int = 30  # default timeout for systemctl/podman commands
    image_pull_timeout: int = 300  # timeout for image pull and auto-update
    webhook_timeout: int = 10  # timeout for webhook HTTP POST delivery
    poll_interval: int = 30  # seconds between container state polls
    metrics_interval: int = 300  # seconds between metrics history samples

    @classmethod
    def from_env(cls) -> "Settings":
        """Build Settings from QUADLETMAN_* environment variables."""
        overrides: dict = {}
        if v := _env("DB_PATH"):
            overrides["db_path"] = SafeAbsPath.of(v, "env:DB_PATH")
        if v := _env("VOLUMES_BASE"):
            overrides["volumes_base"] = SafeAbsPath.of(v, "env:VOLUMES_BASE")
        if v := _env("HOST"):
            overrides["host"] = SafeStr.of(v, "env:HOST")
        if v := _env("PORT"):
            overrides["port"] = int(v)
        if v := _env("UNIX_SOCKET"):
            overrides["unix_socket"] = SafeStr.of(v, "env:UNIX_SOCKET")
        if v := _env("AGENT_SOCKET"):
            overrides["agent_socket"] = SafeStr.of(v, "env:AGENT_SOCKET")
        if v := _env("SERVICE_USER_PREFIX"):
            overrides["service_user_prefix"] = SafeStr.of(v, "env:SERVICE_USER_PREFIX")
        if v := _env("ALLOWED_GROUPS"):
            overrides["allowed_groups"] = [
                SafeStr.of(g.strip(), "env:ALLOWED_GROUPS") for g in v.split(",")
            ]
        if v := _env("LOG_LEVEL"):
            overrides["log_level"] = SafeStr.of(v, "env:LOG_LEVEL")
        if v := _env("SECURE_COOKIES"):
            overrides["secure_cookies"] = v.lower() in ("true", "1", "yes")
        if v := _env("TEST_AUTH_USER"):
            overrides["test_auth_user"] = SafeStr.of(v, "env:TEST_AUTH_USER")
        if v := _env("PROCESS_MONITOR_INTERVAL"):
            overrides["process_monitor_interval"] = int(v)
        if v := _env("CONNECTION_MONITOR_INTERVAL"):
            overrides["connection_monitor_interval"] = int(v)
        if v := _env("IMAGE_UPDATE_CHECK_INTERVAL"):
            overrides["image_update_check_interval"] = int(v)
        if v := _env("SUBPROCESS_TIMEOUT"):
            overrides["subprocess_timeout"] = int(v)
        if v := _env("IMAGE_PULL_TIMEOUT"):
            overrides["image_pull_timeout"] = int(v)
        if v := _env("WEBHOOK_TIMEOUT"):
            overrides["webhook_timeout"] = int(v)
        if v := _env("POLL_INTERVAL"):
            overrides["poll_interval"] = int(v)
        if v := _env("METRICS_INTERVAL"):
            overrides["metrics_interval"] = int(v)
        return cls(**overrides)


settings = Settings.from_env()

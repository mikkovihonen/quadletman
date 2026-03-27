import logging
import os

from pydantic import BaseModel, model_validator

from ..models.sanitized import SafeAbsPath, SafeStr

_logger = logging.getLogger(__name__)


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
    webhook_max_retries: int = 3  # max webhook delivery attempts (exponential backoff)
    poll_interval: int = 30  # seconds between container state polls
    metrics_interval: int = 300  # seconds between metrics history samples
    session_ttl: int = 28800  # absolute session lifetime in seconds (8 hours); idle TTL is half
    lock_timeout: int = 30  # seconds to wait for per-compartment lock before returning 409
    status_cache_ttl: int = 5  # seconds to cache systemctl unit status queries
    db_busy_timeout: int = 5000  # milliseconds SQLite waits for a locked database
    terminal_session_timeout: int = (
        7200  # max seconds for a WebSocket terminal/shell session (2 hours)
    )
    agent_request_timeout: int = 60  # max seconds for a single agent API request
    webhook_retry_delay: int = 2  # base delay (seconds) for webhook exponential backoff
    login_max_attempts: int = 10  # max login attempts per IP within rate limit window
    login_window_seconds: int = 60  # time window (seconds) for login rate limiting
    max_upload_bytes: int = 512 * 1024 * 1024  # max file size for archive uploads (512 MiB)
    max_envfile_bytes: int = 64 * 1024  # max size for container environment files (64 KiB)
    podman_info_retry_interval: int = 60  # seconds between retries when podman info fails
    version_check_interval: int = 300  # seconds between Podman version checks (0 = disabled)
    metrics_retention_hours: int = (
        168  # hours to keep metrics history rows (7 days; 0 = no cleanup)
    )
    status_cache_max_size: int = 1000  # max entries in systemctl status cache
    webhook_dedup_max_entries: int = 10000  # max entries in image update dedup cache

    _MINIMUM_BOUNDS: dict[str, int] = {
        "subprocess_timeout": 1,
        "image_pull_timeout": 1,
        "webhook_timeout": 1,
        "webhook_max_retries": 1,
        "poll_interval": 5,
        "metrics_interval": 10,
        "process_monitor_interval": 5,
        "connection_monitor_interval": 5,
        "image_update_check_interval": 60,
        "session_ttl": 60,
        "lock_timeout": 1,
        "status_cache_ttl": 1,
        "db_busy_timeout": 100,
        "terminal_session_timeout": 60,
        "agent_request_timeout": 5,
        "webhook_retry_delay": 1,
        "login_max_attempts": 1,
        "login_window_seconds": 5,
        "max_upload_bytes": 1024,
        "max_envfile_bytes": 1024,
        "podman_info_retry_interval": 5,
        "version_check_interval": 30,
        "metrics_retention_hours": 1,
        "status_cache_max_size": 10,
        "webhook_dedup_max_entries": 100,
        "port": 1,
    }

    @model_validator(mode="after")
    def _clamp_bounds(self) -> "Settings":
        """Ensure timeout and interval settings have sensible minimum values."""
        for field_name, min_val in self._MINIMUM_BOUNDS.items():
            val = getattr(self, field_name)
            if val < min_val:
                _logger.warning(
                    "Setting %s=%d is below minimum %d — clamping to %d",
                    field_name,
                    val,
                    min_val,
                    min_val,
                )
                object.__setattr__(self, field_name, min_val)
        return self

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
        if v := _env("SESSION_TTL"):
            overrides["session_ttl"] = int(v)
        if v := _env("LOCK_TIMEOUT"):
            overrides["lock_timeout"] = int(v)
        if v := _env("STATUS_CACHE_TTL"):
            overrides["status_cache_ttl"] = int(v)
        if v := _env("DB_BUSY_TIMEOUT"):
            overrides["db_busy_timeout"] = int(v)
        if v := _env("WEBHOOK_MAX_RETRIES"):
            overrides["webhook_max_retries"] = int(v)
        if v := _env("TERMINAL_SESSION_TIMEOUT"):
            overrides["terminal_session_timeout"] = int(v)
        if v := _env("AGENT_REQUEST_TIMEOUT"):
            overrides["agent_request_timeout"] = int(v)
        if v := _env("WEBHOOK_RETRY_DELAY"):
            overrides["webhook_retry_delay"] = int(v)
        if v := _env("LOGIN_MAX_ATTEMPTS"):
            overrides["login_max_attempts"] = int(v)
        if v := _env("LOGIN_WINDOW_SECONDS"):
            overrides["login_window_seconds"] = int(v)
        if v := _env("MAX_UPLOAD_BYTES"):
            overrides["max_upload_bytes"] = int(v)
        if v := _env("MAX_ENVFILE_BYTES"):
            overrides["max_envfile_bytes"] = int(v)
        if v := _env("PODMAN_INFO_RETRY_INTERVAL"):
            overrides["podman_info_retry_interval"] = int(v)
        if v := _env("VERSION_CHECK_INTERVAL"):
            overrides["version_check_interval"] = int(v)
        if v := _env("METRICS_RETENTION_HOURS"):
            overrides["metrics_retention_hours"] = int(v)
        if v := _env("STATUS_CACHE_MAX_SIZE"):
            overrides["status_cache_max_size"] = int(v)
        if v := _env("WEBHOOK_DEDUP_MAX_ENTRIES"):
            overrides["webhook_dedup_max_entries"] = int(v)
        return cls(**overrides)


settings = Settings.from_env()

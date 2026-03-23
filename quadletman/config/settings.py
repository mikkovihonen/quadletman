from pydantic_settings import BaseSettings

from ..models.sanitized import SafeAbsPath, SafeStr


class Settings(BaseSettings):
    db_path: SafeAbsPath = SafeAbsPath.of("/var/lib/quadletman/quadletman.db", "default")
    volumes_base: SafeAbsPath = SafeAbsPath.of("/var/lib/quadletman/volumes", "default")
    host: SafeStr = SafeStr.of("0.0.0.0", "default")
    port: int = 8080
    unix_socket: SafeStr = SafeStr.trusted(
        "", "default"
    )  # absolute path to Unix domain socket; when set, host/port are ignored
    service_user_prefix: SafeStr = SafeStr.of("qm-", "default")
    allowed_groups: list[SafeStr] = [SafeStr.of("sudo", "default"), SafeStr.of("wheel", "default")]
    log_level: SafeStr = SafeStr.of("INFO", "default")
    secure_cookies: bool = False  # set True in production when serving over HTTPS
    # non-empty bypasses PAM — for Playwright E2E tests only, never set in production
    test_auth_user: SafeStr = SafeStr.trusted("", "default")
    process_monitor_interval: int = 60  # seconds between process allowlist checks
    connection_monitor_interval: int = 60  # seconds between connection allowlist checks

    model_config = {"env_prefix": "QUADLETMAN_"}


settings = Settings()

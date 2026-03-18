from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_path: str = "/var/lib/quadletman/quadletman.db"
    volumes_base: str = "/var/lib/quadletman/volumes"
    host: str = "0.0.0.0"
    port: int = 8080
    unix_socket: str = ""  # absolute path to Unix domain socket; when set, host/port are ignored
    service_user_prefix: str = "qm-"
    allowed_groups: list[str] = ["sudo", "wheel"]
    log_level: str = "INFO"
    secure_cookies: bool = False  # set True in production when serving over HTTPS
    test_auth_user: str = (
        ""  # non-empty bypasses PAM — for Playwright E2E tests only, never set in production
    )
    process_monitor_interval: int = 60  # seconds between process whitelist checks
    connection_monitor_interval: int = 60  # seconds between connection whitelist checks

    model_config = {"env_prefix": "QUADLETMAN_"}


settings = Settings()

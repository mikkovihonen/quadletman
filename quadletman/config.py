from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_path: str = "/var/lib/quadletman/quadletman.db"
    volumes_base: str = "/var/lib/quadletman/volumes"
    host: str = "0.0.0.0"
    port: int = 8080
    service_user_prefix: str = "qm-"
    allowed_groups: list[str] = ["sudo", "wheel"]
    log_level: str = "INFO"
    secure_cookies: bool = False  # set True in production when serving over HTTPS

    model_config = {"env_prefix": "QUADLETMAN_"}


settings = Settings()

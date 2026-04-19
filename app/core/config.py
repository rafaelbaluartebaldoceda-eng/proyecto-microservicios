from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Microservicio de Reportes"
    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = False
    api_prefix: str = ""

    database_url: str = "sqlite+aiosqlite:///./data/reporting.db"
    sync_database_url: str | None = None

    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    task_always_eager: bool = False
    task_time_limit_seconds: int = 600
    task_soft_time_limit_seconds: int = 540

    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_audience: str = "reporting-api"
    jwt_issuer: str = "reporting-service"
    access_token_expire_minutes: int = 60

    storage_backend: Literal["local", "s3"] = "local"
    local_storage_path: Path = Path("./storage/private")
    download_token_secret: str = "download-secret-change-me"
    download_token_ttl_seconds: int = 900
    storage_bucket_name: str = "reports"
    storage_region: str = "us-east-1"
    storage_endpoint_url: str | None = None
    storage_access_key_id: str | None = None
    storage_secret_access_key: str | None = None

    report_retention_days: int = 30
    max_page_size: int = 100
    log_level: str = "INFO"
    default_currency: str = "USD"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @computed_field  # type: ignore[misc]
    @property
    def effective_sync_database_url(self) -> str:
        if self.sync_database_url:
            return self.sync_database_url

        if self.database_url.startswith("sqlite+aiosqlite:///"):
            return self.database_url.replace("sqlite+aiosqlite:///", "sqlite:///")
        if self.database_url.startswith("postgresql+asyncpg://"):
            return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
        return self.database_url

    @computed_field  # type: ignore[misc]
    @property
    def effective_celery_broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @computed_field  # type: ignore[misc]
    @property
    def effective_celery_result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @computed_field  # type: ignore[misc]
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @computed_field  # type: ignore[misc]
    @property
    def allowed_origins(self) -> list[str]:
        return ["*"] if not self.is_production else []


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


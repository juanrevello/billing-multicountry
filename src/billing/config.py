from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "billing-multicountry"
    environment: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://test_user:test_user_password@localhost:5432/invoicing"


@lru_cache
def get_settings() -> Settings:
    return Settings()

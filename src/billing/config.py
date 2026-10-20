from dataclasses import dataclass
from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://test_user:test_user_password@localhost:5432/invoicing"

    # Defaults for every provider; override per country with PROVIDER_<COUNTRY>_*.
    # The base URL gets the lowercase country code appended unless overridden.
    provider_base_url: str = "http://localhost:8001"
    provider_timeout_seconds: float = 5.0
    provider_max_retries: int = 3
    provider_backoff_base_seconds: float = 0.5


@cache
def get_settings() -> Settings:
    return Settings()


@dataclass(frozen=True)
class ProviderSettings:
    base_url: str
    timeout_seconds: float
    max_retries: int
    backoff_base_seconds: float


class _ProviderOverrides(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    base_url: str | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None
    backoff_base_seconds: float | None = None


@cache
def provider_settings(country_code: str) -> ProviderSettings:
    """Global defaults, overridden by PROVIDER_<COUNTRY>_* env vars."""
    country_code = country_code.upper()
    defaults = get_settings()
    overrides = _ProviderOverrides(_env_prefix=f"PROVIDER_{country_code}_")
    return ProviderSettings(
        base_url=overrides.base_url
        or f"{defaults.provider_base_url.rstrip('/')}/{country_code.lower()}",
        timeout_seconds=_pick(overrides.timeout_seconds, defaults.provider_timeout_seconds),
        max_retries=_pick(overrides.max_retries, defaults.provider_max_retries),
        backoff_base_seconds=_pick(
            overrides.backoff_base_seconds, defaults.provider_backoff_base_seconds
        ),
    )


def _pick[T](override: T | None, default: T) -> T:
    return default if override is None else override

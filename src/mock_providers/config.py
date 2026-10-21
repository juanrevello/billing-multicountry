from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class MockSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MOCK_", env_file=".env", extra="ignore")

    # Relative weights for the scenario picked when no X-Mock-Scenario header is sent.
    success_weight: int = 80
    error_weight: int = 10
    timeout_weight: int = 5
    reject_weight: int = 5
    timeout_seconds: float = 30.0


@cache
def get_settings() -> MockSettings:
    return MockSettings()

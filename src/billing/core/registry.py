import importlib
import pkgutil
from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from billing.config import get_settings
from billing.core.provider import InvoiceProvider, ProviderConfig

ADAPTERS_PACKAGE = "billing.providers"

_registry: dict[str, type[InvoiceProvider]] = {}


class UnsupportedCountryError(LookupError):
    pass


def register[P: type[InvoiceProvider]](cls: P) -> P:
    country_code = cls.country_code.upper()
    if country_code in _registry:
        raise ValueError(f"Provider already registered for {country_code}")
    _registry[country_code] = cls
    return cls


def discover(package: str = ADAPTERS_PACKAGE) -> None:
    """Import every module in the adapters package so their @register decorators run."""
    module = importlib.import_module(package)
    for info in pkgutil.iter_modules(module.__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"{package}.{info.name}")


def provider_classes() -> list[type[InvoiceProvider]]:
    return sorted(_registry.values(), key=lambda cls: cls.country_code)


@cache
def get_provider(country_code: str) -> InvoiceProvider:
    cls = _registry.get(country_code.upper())
    if cls is None:
        raise UnsupportedCountryError(country_code)
    return cls(provider_config(cls.country_code))


class _ProviderOverrides(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    timeout_seconds: float | None = None
    max_retries: int | None = None
    backoff_base_seconds: float | None = None


def provider_config(country_code: str) -> ProviderConfig:
    """Global defaults, overridden by PROVIDER_<COUNTRY>_* env vars."""
    defaults = get_settings()
    overrides = _ProviderOverrides(_env_prefix=f"PROVIDER_{country_code.upper()}_")
    return ProviderConfig(
        timeout_seconds=_pick(overrides.timeout_seconds, defaults.provider_timeout_seconds),
        max_retries=_pick(overrides.max_retries, defaults.provider_max_retries),
        backoff_base_seconds=_pick(
            overrides.backoff_base_seconds, defaults.provider_backoff_base_seconds
        ),
    )


def _pick[T](override: T | None, default: T) -> T:
    return default if override is None else override

import importlib
import pkgutil

from billing.domain.provider import InvoiceProvider

_registry: dict[str, type[InvoiceProvider]] = {}
_instances: dict[str, InvoiceProvider] = {}


class UnsupportedCountryError(LookupError):
    def __init__(self, country_code: str) -> None:
        super().__init__(f"No provider registered for {country_code}")


def register[P: type[InvoiceProvider]](cls: P) -> P:
    country_code = cls.country_code.upper()
    if country_code in _registry:
        raise ValueError(f"Provider already registered for {country_code}")
    _registry[country_code] = cls
    return cls


def discover(package: str) -> None:
    """Import every module in the adapters package so their @register decorators run."""
    module = importlib.import_module(package)
    for info in pkgutil.iter_modules(module.__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"{package}.{info.name}")


def provider_classes() -> list[type[InvoiceProvider]]:
    return sorted(_registry.values(), key=lambda cls: cls.country_code)


def get_provider(country_code: str) -> InvoiceProvider:
    country_code = country_code.upper()
    if country_code not in _instances:
        cls = _registry.get(country_code)
        if cls is None:
            raise UnsupportedCountryError(country_code)
        _instances[country_code] = cls()
    return _instances[country_code]


async def close_providers() -> None:
    for provider in _instances.values():
        await provider.aclose()
    _instances.clear()

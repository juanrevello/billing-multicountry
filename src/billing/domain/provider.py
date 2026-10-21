import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar


@dataclass(frozen=True)
class ProviderInvoice:
    invoice_id: uuid.UUID
    entity_id: str
    amount: Decimal
    currency: str
    country_code: str


@dataclass(frozen=True)
class ProviderResult:
    external_reference: str
    status_code: int
    body: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ProviderTransientError(ProviderError):
    """5xx or connection error: safe to retry."""


class ProviderTimeoutError(ProviderError):
    """No response in time: the provider may or may not have issued the invoice."""


class ProviderRejectedError(ProviderError):
    """4xx: the provider refused the data; retrying will not help."""


class UnsupportedCurrencyError(ValueError):
    def __init__(self, country_code: str, currency: str, supported: frozenset[str]) -> None:
        super().__init__(f"{country_code} does not support {currency}")
        self.supported = sorted(supported)


class InvoiceProvider(ABC):
    country_code: ClassVar[str]
    name: ClassVar[str]
    supported_currencies: ClassVar[frozenset[str]]

    @abstractmethod
    def build_payload(self, invoice: ProviderInvoice) -> dict[str, Any]: ...

    @abstractmethod
    async def issue(self, payload: dict[str, Any]) -> ProviderResult: ...

    @abstractmethod
    async def lookup(self, invoice_id: uuid.UUID) -> ProviderResult | None: ...

    async def aclose(self) -> None:
        """Release resources such as HTTP connections. No-op by default."""
        return None


# Transient errors and timeouts are retried (the provider deduplicates by invoice_id);
# rejections never are, since the same data would be rejected again.
RETRYABLE_ERRORS: tuple[type[ProviderError], ...] = (ProviderTransientError, ProviderTimeoutError)

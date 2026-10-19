import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar


@dataclass(frozen=True)
class ProviderConfig:
    timeout_seconds: float
    max_retries: int
    backoff_base_seconds: float


@dataclass(frozen=True)
class InvoiceData:
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


class InvoiceProvider(ABC):
    country_code: ClassVar[str]
    name: ClassVar[str]
    supported_currencies: ClassVar[frozenset[str]]

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @abstractmethod
    def build_payload(self, invoice: InvoiceData) -> dict[str, Any]: ...

    @abstractmethod
    async def issue(self, invoice: InvoiceData) -> ProviderResult: ...

    @abstractmethod
    async def lookup(self, invoice_id: uuid.UUID) -> ProviderResult | None: ...

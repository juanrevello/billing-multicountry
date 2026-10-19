import uuid
from typing import Any

from billing.core.provider import InvoiceData, InvoiceProvider, ProviderResult
from billing.core.registry import register


@register
class BrazilProvider(InvoiceProvider):
    country_code = "BR"
    name = "sefaz"
    supported_currencies = frozenset({"BRL"})

    def build_payload(self, invoice: InvoiceData) -> dict[str, Any]:
        raise NotImplementedError

    async def issue(self, invoice: InvoiceData) -> ProviderResult:
        raise NotImplementedError

    async def lookup(self, invoice_id: uuid.UUID) -> ProviderResult | None:
        raise NotImplementedError

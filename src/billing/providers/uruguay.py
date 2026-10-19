# Example: uncomment to register a new country; no other change is needed.

# import uuid
# from typing import Any

# from billing.core.provider import InvoiceData, InvoiceProvider, ProviderResult
# from billing.core.registry import register


# @register
# class UruguayProvider(InvoiceProvider):
#     country_code = "UY"
#     name = "dgi"
#     supported_currencies = frozenset({"UYU"})

#     def build_payload(self, invoice: InvoiceData) -> dict[str, Any]:
#         raise NotImplementedError

#     async def issue(self, invoice: InvoiceData) -> ProviderResult:
#         raise NotImplementedError

#     async def lookup(self, invoice_id: uuid.UUID) -> ProviderResult | None:
#         raise NotImplementedError

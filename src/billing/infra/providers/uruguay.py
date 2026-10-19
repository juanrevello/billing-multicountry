# Example: uncomment to register a new country; no other change is needed.
# Point it to the real provider with PROVIDER_UY_BASE_URL.

# import uuid
# from typing import Any

# from billing.domain.provider import InvoiceData, ProviderRejectedError, ProviderResult
# from billing.domain.registry import register
# from billing.infra.http import HttpInvoiceProvider, response_body


# @register
# class UruguayProvider(HttpInvoiceProvider):
#     country_code = "UY"
#     name = "dgi"
#     supported_currencies = frozenset({"UYU"})

#     def build_payload(self, invoice: InvoiceData) -> dict[str, Any]:
#         return {
#             "reference": str(invoice.invoice_id),
#             "customer": invoice.entity_id,
#             "amount": f"{invoice.amount:.2f}",
#             "currency": invoice.currency,
#         }

#     async def issue(self, invoice: InvoiceData) -> ProviderResult:
#         response = await self.request("POST", "/cfe", json=self.build_payload(invoice))
#         body = response_body(response)
#         if response.status_code != 201:
#             raise ProviderRejectedError(str(body), status_code=response.status_code, body=body)
#         return ProviderResult(body["cfe_id"], response.status_code, body)

#     async def lookup(self, invoice_id: uuid.UUID) -> ProviderResult | None:
#         response = await self.request("GET", f"/cfe/{invoice_id}")
#         if response.status_code == 404:
#             return None
#         body = response_body(response)
#         return ProviderResult(body["cfe_id"], response.status_code, body)

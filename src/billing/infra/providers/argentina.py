import uuid
from typing import Any

from billing.domain.provider import ProviderInvoice, ProviderRejectedError, ProviderResult
from billing.domain.registry import register
from billing.infra.http import HttpInvoiceProvider, response_body

# ISO 4217 to the provider's own currency codes.
CURRENCY_CODES = {"ARS": "PES", "USD": "DOL"}


@register
class ArgentinaProvider(HttpInvoiceProvider):
    country_code = "AR"
    name = "arca"
    supported_currencies = frozenset(CURRENCY_CODES)

    def build_payload(self, invoice: ProviderInvoice) -> dict[str, Any]:
        return {
            "external_id": str(invoice.invoice_id),
            "customer_id": invoice.entity_id,
            "total": f"{invoice.amount:.2f}",
            "currency_code": CURRENCY_CODES[invoice.currency],
        }

    async def issue(self, payload: dict[str, Any]) -> ProviderResult:
        response = await self.request("POST", "/vouchers", json=payload)
        body = response_body(response)
        if response.status_code not in (200, 201):
            raise ProviderRejectedError(
                _error_message(body), status_code=response.status_code, body=body
            )
        return ProviderResult(body["cae"], response.status_code, body)

    async def lookup(self, invoice_id: uuid.UUID) -> ProviderResult | None:
        response = await self.request("GET", f"/vouchers/{invoice_id}")
        body = response_body(response)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ProviderRejectedError(
                _error_message(body), status_code=response.status_code, body=body
            )
        return ProviderResult(body["cae"], response.status_code, body)


def _error_message(body: dict[str, Any]) -> str:
    errors = body.get("errors") or [{}]
    return "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errors)

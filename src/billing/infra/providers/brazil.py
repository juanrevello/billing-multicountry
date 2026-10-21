import uuid
from typing import Any

from billing.domain.provider import ProviderInvoice, ProviderRejectedError, ProviderResult
from billing.domain.registry import register
from billing.infra.http import HttpInvoiceProvider, response_body


@register
class BrazilProvider(HttpInvoiceProvider):
    country_code = "BR"
    name = "sefaz"
    supported_currencies = frozenset({"BRL"})

    def build_payload(self, invoice: ProviderInvoice) -> dict[str, Any]:
        return {
            "reference": str(invoice.invoice_id),
            "recipient": {"document": invoice.entity_id},
            "amount_cents": int(invoice.amount * 100),
            "currency": invoice.currency,
        }

    async def issue(self, payload: dict[str, Any]) -> ProviderResult:
        response = await self.request("POST", "/nfe", json=payload)
        body = response_body(response)
        if response.status_code != 200:
            raise ProviderRejectedError(
                _error_message(body, response.status_code),
                status_code=response.status_code,
                body=body,
            )
        return ProviderResult(body["access_key"], response.status_code, body)

    async def lookup(self, invoice_id: uuid.UUID) -> ProviderResult | None:
        response = await self.request("GET", f"/nfe/{invoice_id}")
        body = response_body(response)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ProviderRejectedError(
                _error_message(body, response.status_code),
                status_code=response.status_code,
                body=body,
            )
        return ProviderResult(body["access_key"], response.status_code, body)


def _error_message(body: dict[str, Any], status_code: int) -> str:
    if "reason" not in body:
        return f"Provider returned {status_code}"
    return f"{body.get('code')}: {body['reason']}"

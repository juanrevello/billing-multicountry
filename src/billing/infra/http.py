from typing import Any

import httpx

from billing.config import provider_settings
from billing.domain.provider import InvoiceProvider, ProviderTimeoutError, ProviderTransientError


class HttpInvoiceProvider(InvoiceProvider):
    """Shared HTTP plumbing: one pooled client per provider and transport error mapping."""

    def __init__(self) -> None:
        settings = provider_settings(self.country_code)
        self.timeout_seconds = settings.timeout_seconds
        self.client = httpx.AsyncClient(
            base_url=settings.base_url, timeout=settings.timeout_seconds
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Single HTTP call. Raises on transport errors and 5xx; 4xx is left to the adapter."""
        try:
            response = await self.client.request(method, path, json=json)
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # The request never reached the provider, so retrying is safe.
            raise ProviderTransientError(f"{type(exc).__name__}: {exc}") from exc
        except httpx.TimeoutException as exc:
            # The provider may have processed it: the outcome is unknown.
            raise ProviderTimeoutError(
                f"{type(exc).__name__} after {self.timeout_seconds}s"
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderTransientError(f"{type(exc).__name__}: {exc}") from exc

        if response.status_code >= 500:
            raise ProviderTransientError(
                f"Provider returned {response.status_code}",
                status_code=response.status_code,
                body=response_body(response),
            )
        return response


def response_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {"raw": response.text}
    return body if isinstance(body, dict) else {"data": body}

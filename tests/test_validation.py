import uuid
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from tests.conftest import AR_INVOICE, post_invoice


@pytest.mark.parametrize(
    ("body", "expected_type"),
    [
        ({**AR_INVOICE, "amount": 0}, "/problems/validation-error"),
        ({**AR_INVOICE, "amount": "-10.00"}, "/problems/validation-error"),
        ({**AR_INVOICE, "amount": "10.005"}, "/problems/validation-error"),
        ({**AR_INVOICE, "currency": "ars"}, "/problems/validation-error"),
        ({**AR_INVOICE, "entity_id": ""}, "/problems/validation-error"),
        ({**AR_INVOICE, "unexpected": 1}, "/problems/validation-error"),
        ({k: v for k, v in AR_INVOICE.items() if k != "amount"}, "/problems/validation-error"),
        ({**AR_INVOICE, "country_code": "UY", "currency": "UYU"}, "/problems/unsupported-country"),
        ({**AR_INVOICE, "currency": "EUR"}, "/problems/unsupported-currency"),
    ],
)
def test_invalid_invoice_is_rejected(
    client: TestClient, providers: respx.MockRouter, body: dict[str, Any], expected_type: str
) -> None:
    response, _ = post_invoice(client, body)

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == expected_type
    assert not providers.calls


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": "not-a-uuid"}])
def test_idempotency_key_is_required(
    client: TestClient, providers: respx.MockRouter, headers: dict[str, str]
) -> None:
    response = client.post("/invoices", json=AR_INVOICE, headers=headers)

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["header", "Idempotency-Key"]
    assert not providers.calls


def test_unknown_invoice_is_not_found(client: TestClient) -> None:
    response = client.get(f"/invoices/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["type"] == "/problems/invoice-not-found"


def test_providers_are_listed(client: TestClient) -> None:
    response = client.get("/providers")

    assert response.status_code == 200
    assert response.json() == [
        {"country_code": "AR", "provider": "arca", "currencies": ["ARS", "USD"]},
        {"country_code": "BR", "provider": "sefaz", "currencies": ["BRL"]},
    ]

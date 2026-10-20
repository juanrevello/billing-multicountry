import os
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

PROVIDERS_URL = "http://providers.test"

# Always the ephemeral test DB (`docker compose up -d db-test`), never the one in .env.
TEST_DATABASE_URL = (
    "postgresql+asyncpg://test_user:test_user_password@localhost:5433/invoicing_test"
)

# Fast, deterministic provider settings; they also override any local .env.
os.environ.update(
    {
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", TEST_DATABASE_URL),
        "PROVIDER_BASE_URL": PROVIDERS_URL,
        "PROVIDER_TIMEOUT_SECONDS": "1",
        "PROVIDER_MAX_RETRIES": "2",
        "PROVIDER_BACKOFF_BASE_SECONDS": "0",
        "PROVIDER_AR_TIMEOUT_SECONDS": "1",
        "PROVIDER_AR_MAX_RETRIES": "2",
        "PROVIDER_AR_BACKOFF_BASE_SECONDS": "0",
    }
)

from billing.main import app  # noqa: E402

AR_INVOICE = {
    "entity_id": "20-12345678-9",
    "amount": 1500.00,
    "currency": "ARS",
    "country_code": "AR",
}
BR_INVOICE = {
    "entity_id": "12345678000199",
    "amount": "250.00",
    "currency": "BRL",
    "country_code": "BR",
}

AR_VOUCHER = {
    "external_id": "ignored",
    "voucher_number": 1,
    "cae": "71234567890123",
    "total": "1500.00",
    "currency_code": "PES",
}
BR_NFE = {
    "reference": "ignored",
    "access_key": "3" * 44,
    "protocol": "135000000000001",
    "status": "authorized",
    "amount_cents": 25000,
}


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    # One client for the whole session: the app's DB pool lives on its event loop.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def providers() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=PROVIDERS_URL, assert_all_called=False) as router:
        yield router


def post_invoice(
    client: TestClient, body: dict[str, Any] = AR_INVOICE, key: str | None = None
) -> tuple[httpx.Response, str]:
    key = key or str(uuid.uuid4())
    return client.post("/invoices", json=body, headers={"Idempotency-Key": key}), key


def attempts(client: TestClient, invoice_id: str) -> list[dict[str, Any]]:
    return client.get(f"/invoices/{invoice_id}").json()["attempts"]

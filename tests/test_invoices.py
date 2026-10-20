import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from billing.infra.db.repository import InvoiceRepository
from tests.conftest import AR_INVOICE, AR_VOUCHER, BR_INVOICE, BR_NFE, attempts, post_invoice

AR_ISSUE = "/ar/vouchers"
AR_LOOKUP = r"^/ar/vouchers/.+$"


def outcomes(client: TestClient, invoice_id: str) -> list[str]:
    return [f"{a['kind']}:{a['outcome']}" for a in attempts(client, invoice_id)]


def test_issue_ar_invoice(client: TestClient, providers: respx.MockRouter) -> None:
    route = providers.post(AR_ISSUE).respond(201, json=AR_VOUCHER)

    response, _ = post_invoice(client)

    assert response.status_code == 201
    invoice = response.json()
    assert invoice["status"] == "issued"
    assert invoice["provider_used"] == "arca"
    assert invoice["external_reference"] == AR_VOUCHER["cae"]

    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "external_id": invoice["invoice_id"],
        "customer_id": AR_INVOICE["entity_id"],
        "total": "1500.00",
        "currency_code": "PES",
    }

    [attempt] = attempts(client, invoice["invoice_id"])
    assert attempt["kind"] == "issue"
    assert attempt["outcome"] == "success"
    assert attempt["request_payload"] == sent
    assert attempt["response_status"] == 201
    assert attempt["response_body"]["cae"] == AR_VOUCHER["cae"]
    assert attempt["duration_ms"] is not None


def test_issue_br_invoice(client: TestClient, providers: respx.MockRouter) -> None:
    route = providers.post("/br/nfe").respond(200, json=BR_NFE)

    response, _ = post_invoice(client, BR_INVOICE)

    assert response.status_code == 201
    assert response.json()["provider_used"] == "sefaz"
    assert response.json()["external_reference"] == BR_NFE["access_key"]
    sent = json.loads(route.calls.last.request.content)
    assert sent["amount_cents"] == 25000
    assert sent["recipient"] == {"document": BR_INVOICE["entity_id"]}


def test_transient_error_is_retried(client: TestClient, providers: respx.MockRouter) -> None:
    providers.post(AR_ISSUE).mock(
        side_effect=[httpx.Response(500), httpx.Response(201, json=AR_VOUCHER)]
    )

    response, _ = post_invoice(client)

    assert response.status_code == 201
    invoice_id = response.json()["invoice_id"]
    assert outcomes(client, invoice_id) == ["issue:transient_error", "issue:success"]


def test_provider_unavailable_after_retries(
    client: TestClient, providers: respx.MockRouter
) -> None:
    route = providers.post(AR_ISSUE).respond(500)

    response, _ = post_invoice(client)

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    problem = response.json()
    assert problem["type"] == "/problems/provider-unavailable"
    assert problem["invoice_status"] == "failed"
    assert route.call_count == 3  # first attempt + 2 retries
    assert outcomes(client, problem["invoice_id"]) == ["issue:transient_error"] * 3


def test_timeout_leaves_invoice_pending(client: TestClient, providers: respx.MockRouter) -> None:
    providers.post(AR_ISSUE).mock(side_effect=httpx.ReadTimeout("timed out"))

    response, _ = post_invoice(client)

    assert response.status_code == 202
    invoice = response.json()
    assert invoice["status"] == "pending"
    assert outcomes(client, invoice["invoice_id"]) == ["issue:timeout"] * 3


def test_rejection_is_not_retried(client: TestClient, providers: respx.MockRouter) -> None:
    route = providers.post(AR_ISSUE).respond(
        400, json={"errors": [{"code": 10016, "message": "Customer is not a valid taxpayer"}]}
    )

    response, _ = post_invoice(client)

    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == "/problems/provider-rejected"
    assert "10016" in problem["detail"]
    assert route.call_count == 1
    assert outcomes(client, problem["invoice_id"]) == ["issue:rejected"]


def test_replay_returns_same_invoice(client: TestClient, providers: respx.MockRouter) -> None:
    route = providers.post(AR_ISSUE).respond(201, json=AR_VOUCHER)

    first, key = post_invoice(client)
    replay, _ = post_invoice(client, key=key)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["invoice_id"] == first.json()["invoice_id"]
    assert route.call_count == 1


def test_same_key_with_another_body_conflicts(
    client: TestClient, providers: respx.MockRouter
) -> None:
    providers.post(AR_ISSUE).respond(201, json=AR_VOUCHER)

    first, key = post_invoice(client)
    conflict, _ = post_invoice(client, {**AR_INVOICE, "amount": "1.00"}, key=key)

    assert conflict.status_code == 409
    assert conflict.json()["type"] == "/problems/idempotency-key-conflict"
    assert conflict.json()["invoice_id"] == first.json()["invoice_id"]


def test_confirmation_without_persistence_is_reconciled(
    client: TestClient, providers: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider issues the invoice but saving the result fails; the retry must not reissue."""
    issue = providers.post(AR_ISSUE).respond(201, json=AR_VOUCHER)
    lookup = providers.get(path__regex=AR_LOOKUP).respond(200, json=AR_VOUCHER)

    async def database_down(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(InvoiceRepository, "set_status", database_down)
        crashed, key = post_invoice(client)
    assert crashed.status_code == 500

    retry, _ = post_invoice(client, key=key)

    assert retry.status_code == 201
    assert retry.json()["status"] == "issued"
    assert retry.json()["external_reference"] == AR_VOUCHER["cae"]
    assert issue.call_count == 1
    assert lookup.call_count == 1
    assert outcomes(client, retry.json()["invoice_id"]) == ["issue:success", "lookup:success"]


def test_failed_invoice_is_resumed_when_provider_recovers(
    client: TestClient, providers: respx.MockRouter
) -> None:
    issue = providers.post(AR_ISSUE).mock(
        side_effect=[httpx.Response(500)] * 3 + [httpx.Response(201, json=AR_VOUCHER)]
    )
    providers.get(path__regex=AR_LOOKUP).respond(404, json={"errors": [{"code": 404}]})

    failed, key = post_invoice(client)
    retry, _ = post_invoice(client, key=key)

    assert failed.status_code == 502
    assert retry.status_code == 201
    assert issue.call_count == 4
    assert outcomes(client, retry.json()["invoice_id"]) == [
        "issue:transient_error",
        "issue:transient_error",
        "issue:transient_error",
        "lookup:not_found",
        "issue:success",
    ]


def test_concurrent_creation_with_same_key_conflicts(
    client: TestClient, providers: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = providers.post(AR_ISSUE).respond(201, json=AR_VOUCHER)
    _, key = post_invoice(client)

    async def not_found_yet(*args: object, **kwargs: object) -> None:
        return None

    # Simulate the race: the second request checks before the first one has committed.
    monkeypatch.setattr(InvoiceRepository, "get_by_idempotency_key", not_found_yet)
    response, _ = post_invoice(client, key=key)

    assert response.status_code == 409
    assert response.json()["type"] == "/problems/invoice-in-progress"
    assert route.call_count == 1

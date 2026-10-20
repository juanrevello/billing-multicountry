import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from billing.api.problems import PROBLEM_MEDIA_TYPE, Problem
from billing.application.invoicing import (
    IdempotencyKeyReusedError,
    InvoiceService,
    IssueInvoiceCommand,
    UnsupportedCurrencyError,
)
from billing.domain.invoice import AttemptKind, AttemptOutcome, InvoiceErrorCode, InvoiceStatus
from billing.domain.registry import UnsupportedCountryError
from billing.infra.db.models import Invoice
from billing.infra.db.repository import InvoiceRepository
from billing.infra.db.session import sessionmaker

router = APIRouter(prefix="/invoices", tags=["invoices"])


class InvoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1, max_length=64, examples=["20-12345678-9"])
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, examples=["100.50"])
    currency: str = Field(pattern=r"^[A-Z]{3}$", examples=["ARS"])
    country_code: str = Field(pattern=r"^[A-Z]{2}$", examples=["AR"])


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: InvoiceStatus
    entity_id: str
    amount: Decimal
    currency: str
    country_code: str
    provider: str
    external_reference: str | None
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attempt_number: int
    kind: AttemptKind
    outcome: AttemptOutcome
    request_payload: dict[str, Any]
    response_status: int | None
    response_body: dict[str, Any] | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None


class InvoiceDetailOut(InvoiceOut):
    attempts: list[AttemptOut]


def get_invoice_service() -> InvoiceService:
    return InvoiceService(InvoiceRepository(sessionmaker))


Service = Annotated[InvoiceService, Depends(get_invoice_service)]


def idempotency_key(
    key: Annotated[
        str,
        Header(alias="Idempotency-Key", description="UUIDv7 that identifies this invoice request"),
    ],
) -> uuid.UUID:
    try:
        value = uuid.UUID(key)
    except ValueError:
        value = None
    if value is None or value.version != 7:
        raise Problem(
            422,
            "/problems/invalid-idempotency-key",
            "Invalid Idempotency-Key",
            "Idempotency-Key must be a UUIDv7",
        )
    return value


def _problem_doc(description: str) -> dict[str, Any]:
    return {"description": description, "content": {PROBLEM_MEDIA_TYPE: {}}}


@router.post(
    "",
    status_code=201,
    response_model=InvoiceOut,
    responses={
        202: {"model": InvoiceOut, "description": "Provider timed out; outcome unknown"},
        409: _problem_doc("Idempotency-Key already used"),
        422: _problem_doc("Invalid input, unsupported country/currency or provider rejection"),
        502: _problem_doc("Provider unavailable after retries"),
    },
)
async def issue_invoice(
    body: InvoiceIn,
    key: Annotated[uuid.UUID, Depends(idempotency_key)],
    service: Service,
):
    command = IssueInvoiceCommand(idempotency_key=key, **body.model_dump())
    try:
        invoice = await service.issue(command)
    except UnsupportedCountryError:
        raise Problem(
            422,
            "/problems/unsupported-country",
            "Unsupported country",
            f"No provider registered for {body.country_code}",
        ) from None
    except UnsupportedCurrencyError as exc:
        raise Problem(
            422,
            "/problems/unsupported-currency",
            "Unsupported currency",
            str(exc),
            supported_currencies=exc.supported,
        ) from None
    except IdempotencyKeyReusedError:
        raise Problem(
            409,
            "/problems/idempotency-key-reused",
            "Idempotency-Key already used",
            f"An invoice already exists for Idempotency-Key {key}",
        ) from None
    return _issue_response(invoice)


def _issue_response(invoice: Invoice) -> JSONResponse | InvoiceOut:
    body = InvoiceOut.model_validate(invoice)
    match invoice.status, invoice.error_code:
        case InvoiceStatus.ISSUED, _:
            return body
        case InvoiceStatus.PENDING, _:
            return JSONResponse(body.model_dump(mode="json"), status_code=202)
        case InvoiceStatus.FAILED, InvoiceErrorCode.PROVIDER_REJECTED:
            raise Problem(
                422,
                "/problems/provider-rejected",
                "Invoice rejected by provider",
                invoice.error_detail,
                invoice_id=invoice.id,
                invoice_status=invoice.status,
            )
        case _:
            raise Problem(
                502,
                "/problems/provider-unavailable",
                "Provider unavailable",
                invoice.error_detail,
                invoice_id=invoice.id,
                invoice_status=invoice.status,
            )


@router.get(
    "/{invoice_id}",
    response_model=InvoiceDetailOut,
    responses={404: _problem_doc("Invoice not found")},
)
async def get_invoice(invoice_id: uuid.UUID, service: Service):
    invoice = await service.get(invoice_id)
    if invoice is None:
        raise Problem(
            404, "/problems/invoice-not-found", "Invoice not found", f"No invoice {invoice_id}"
        )
    return invoice

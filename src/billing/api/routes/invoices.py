import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field

from billing.api.problems import Problem, problem_doc
from billing.application.invoices import (
    IdempotencyKeyConflictError,
    InvoiceInProgressError,
    InvoiceService,
    IssueInvoiceCommand,
    IssueInvoiceResult,
)
from billing.domain.invoice import AttemptKind, AttemptOutcome, InvoiceStatus
from billing.domain.provider import ProviderRejectedError, UnsupportedCurrencyError
from billing.domain.registry import UnsupportedCountryError
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

    invoice_id: uuid.UUID = Field(validation_alias="id")
    status: InvoiceStatus
    provider_used: str = Field(validation_alias="provider")
    external_reference: str | None


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: AttemptKind
    outcome: AttemptOutcome
    request_payload: dict[str, Any]
    response_status: int | None
    response_body: dict[str, Any] | None
    error: str | None
    started_at: datetime
    duration_ms: int | None


class InvoiceDetailOut(InvoiceOut):
    entity_id: str
    amount: Decimal
    currency: str
    country_code: str
    created_at: datetime
    attempts: list[AttemptOut]


def get_invoice_service() -> InvoiceService:
    return InvoiceService(InvoiceRepository(sessionmaker))


Service = Annotated[InvoiceService, Depends(get_invoice_service)]


IdempotencyKey = Annotated[
    uuid.UUID,
    Header(alias="Idempotency-Key", description="UUID that identifies this invoice request"),
]


@router.post(
    "",
    status_code=201,
    response_model=InvoiceOut,
    responses={
        201: {"description": "Invoice issued"},
        200: {"model": InvoiceOut, "description": "Replay of an invoice already issued"},
        202: {"model": InvoiceOut, "description": "Provider timed out; outcome unknown"},
        409: problem_doc("Idempotency-Key reused with another body, or a concurrent request"),
        422: problem_doc("Invalid input, unsupported country/currency or provider rejection"),
        502: problem_doc("Provider unavailable after retries"),
    },
)
async def issue_invoice(
    body: InvoiceIn,
    idempotency_key: IdempotencyKey,
    service: Service,
    response: Response,
):
    command = IssueInvoiceCommand(idempotency_key=idempotency_key, **body.model_dump())
    try:
        result = await service.issue(command)
    except UnsupportedCountryError as exc:
        raise Problem(
            422,
            "/problems/unsupported-country",
            "Unsupported country",
            str(exc),
        ) from None
    except UnsupportedCurrencyError as exc:
        raise Problem(
            422,
            "/problems/unsupported-currency",
            "Unsupported currency",
            str(exc),
            supported_currencies=exc.supported,
        ) from None
    except IdempotencyKeyConflictError as exc:
        raise Problem(
            409,
            "/problems/idempotency-key-conflict",
            "Idempotency-Key reused with a different body",
            f"Idempotency-Key {idempotency_key} was already used for another request",
            invoice_id=exc.invoice_id,
        ) from None
    except InvoiceInProgressError:
        raise Problem(
            409,
            "/problems/invoice-in-progress",
            "Invoice in progress",
            "Another request with this Idempotency-Key is being processed; retry later",
        ) from None

    invoice = result.invoice
    if invoice.status is InvoiceStatus.FAILED:
        raise _failure_problem(result)
    if invoice.status is InvoiceStatus.PENDING:
        response.status_code = 202
    elif result.replayed:
        response.status_code = 200
    return invoice


def _failure_problem(result: IssueInvoiceResult) -> Problem:
    """The provider failed for good: 422 if it rejected the data, 502 if it was unavailable."""
    invoice = result.invoice
    if isinstance(result.error, ProviderRejectedError):
        return Problem(
            422,
            "/problems/provider-rejected",
            "Invoice rejected by provider",
            str(result.error),
            invoice_id=invoice.id,
            invoice_status=invoice.status,
        )
    return Problem(
        502,
        "/problems/provider-unavailable",
        "Provider unavailable",
        str(result.error),
        invoice_id=invoice.id,
        invoice_status=invoice.status,
    )


@router.get(
    "/{invoice_id}",
    response_model=InvoiceDetailOut,
    responses={
        200: {"description": "Invoice with its full audit trail"},
        404: problem_doc("Invoice not found"),
        422: problem_doc("Invalid invoice_id"),
    },
)
async def get_invoice(invoice_id: uuid.UUID, service: Service):
    invoice = await service.get(invoice_id)
    if invoice is None:
        raise Problem(
            404, "/problems/invoice-not-found", "Invoice not found", f"No invoice {invoice_id}"
        )
    return invoice

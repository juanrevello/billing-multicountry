import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from billing.domain.invoice import AttemptKind, AttemptOutcome, InvoiceStatus
from billing.domain.provider import (
    InvoiceProvider,
    ProviderError,
    ProviderInvoice,
    ProviderRejectedError,
    ProviderResult,
    ProviderTimeoutError,
    ProviderTransientError,
    UnsupportedCurrencyError,
)
from billing.domain.registry import get_provider
from billing.infra.db.models import Invoice
from billing.infra.db.repository import DuplicateIdempotencyKeyError, InvoiceRepository
from billing.infra.retry import retrying

_ATTEMPT_OUTCOMES: dict[type[ProviderError], AttemptOutcome] = {
    ProviderTransientError: AttemptOutcome.RETRYABLE_ERROR,
    ProviderTimeoutError: AttemptOutcome.TIMEOUT,
    ProviderRejectedError: AttemptOutcome.REJECTED,
}


class IdempotencyKeyConflictError(Exception):
    """Same Idempotency-Key, different request body."""

    def __init__(self, invoice_id: uuid.UUID) -> None:
        super().__init__(str(invoice_id))
        self.invoice_id = invoice_id


class InvoiceInProgressError(Exception):
    """Another request with the same Idempotency-Key is creating this invoice."""


@dataclass(frozen=True)
class IssueInvoiceCommand:
    idempotency_key: uuid.UUID
    entity_id: str
    amount: Decimal
    currency: str
    country_code: str

    def matches(self, invoice: Invoice) -> bool:
        """Whether the invoice was created from this same request body."""
        return (invoice.entity_id, invoice.amount, invoice.currency, invoice.country_code) == (
            self.entity_id,
            self.amount,
            self.currency,
            self.country_code,
        )


@dataclass(frozen=True)
class IssueInvoiceResult:
    invoice: Invoice
    replayed: bool = False
    # Why the invoice is not issued: the last provider error.
    error: ProviderError | None = None


@dataclass(frozen=True)
class _CallOutcome:
    """Result of a provider call after retries: either a result (None = not found) or an error."""

    result: ProviderResult | None
    error: ProviderError | None
    timed_out: bool


class InvoiceService:
    def __init__(self, repository: InvoiceRepository) -> None:
        self.repository = repository

    async def get(self, invoice_id: uuid.UUID) -> Invoice | None:
        return await self.repository.get_with_attempts(invoice_id)

    async def issue(self, command: IssueInvoiceCommand) -> IssueInvoiceResult:
        provider = get_provider(command.country_code)
        if command.currency not in provider.supported_currencies:
            raise UnsupportedCurrencyError(
                command.country_code, command.currency, provider.supported_currencies
            )

        invoice = await self.repository.get_by_idempotency_key(command.idempotency_key)
        if invoice is None:
            try:
                invoice = await self.repository.create(self._new_invoice(command, provider))
            except DuplicateIdempotencyKeyError:
                # A concurrent request with the same key created it first.
                raise InvoiceInProgressError from None
            return await self._issue(invoice, provider)

        if not command.matches(invoice):
            raise IdempotencyKeyConflictError(invoice.id)
        if invoice.status is InvoiceStatus.ISSUED:
            return IssueInvoiceResult(invoice, replayed=True)

        return await self._resume(invoice, provider)

    async def _resume(self, invoice: Invoice, provider: InvoiceProvider) -> IssueInvoiceResult:
        """Never issue twice: ask the provider first whether a previous attempt got through."""
        lookup = await self._call_with_retry(
            invoice,
            AttemptKind.LOOKUP,
            {"invoice_id": str(invoice.id)},
            lambda: provider.lookup(invoice.id),
        )
        if lookup.error is not None:
            # We can't tell whether it was issued, so its status stays as is.
            return IssueInvoiceResult(invoice, error=lookup.error)
        if lookup.result is not None:
            return await self._mark_issued(invoice, lookup.result)
        return await self._issue(invoice, provider)

    async def _issue(self, invoice: Invoice, provider: InvoiceProvider) -> IssueInvoiceResult:
        payload = provider.build_payload(
            ProviderInvoice(
                invoice_id=invoice.id,
                entity_id=invoice.entity_id,
                amount=invoice.amount,
                currency=invoice.currency,
                country_code=invoice.country_code,
            )
        )
        outcome = await self._call_with_retry(
            invoice, AttemptKind.ISSUE, payload, lambda: provider.issue(payload)
        )
        if outcome.result is not None:
            return await self._mark_issued(invoice, outcome.result)
        # After any timeout the provider may have issued it, so the outcome stays unknown;
        # a rejection means it did not.
        unknown = outcome.timed_out and not isinstance(outcome.error, ProviderRejectedError)
        status = InvoiceStatus.PENDING if unknown else InvoiceStatus.FAILED
        invoice = await self.repository.set_status(invoice.id, status)
        return IssueInvoiceResult(invoice, error=outcome.error)

    async def _call_with_retry(
        self,
        invoice: Invoice,
        kind: AttemptKind,
        payload: dict[str, Any],
        call: Callable[[], Awaitable[ProviderResult | None]],
    ) -> _CallOutcome:
        result: ProviderResult | None = None
        timed_out = False
        try:
            async for attempt in retrying(invoice.country_code):
                with attempt:
                    try:
                        result = await self._attempt(invoice, kind, payload, call)
                    except ProviderTimeoutError:
                        timed_out = True
                        raise
        except ProviderError as exc:
            return _CallOutcome(result=None, error=exc, timed_out=timed_out)
        return _CallOutcome(result=result, error=None, timed_out=timed_out)

    async def _attempt(
        self,
        invoice: Invoice,
        kind: AttemptKind,
        payload: dict[str, Any],
        call: Callable[[], Awaitable[ProviderResult | None]],
    ) -> ProviderResult | None:
        """Audit in two steps: in_flight row before the call, outcome after it."""
        attempt = await self.repository.start_attempt(invoice.id, kind, payload)
        started = time.monotonic()
        try:
            result = await call()
        except ProviderError as exc:
            await self.repository.finish_attempt(
                attempt.id,
                outcome=_ATTEMPT_OUTCOMES[type(exc)],
                duration_ms=_elapsed_ms(started),
                response_status=exc.status_code,
                response_body=exc.body,
                error=str(exc),
            )
            raise
        await self.repository.finish_attempt(
            attempt.id,
            outcome=AttemptOutcome.SUCCESS if result is not None else AttemptOutcome.NOT_FOUND,
            duration_ms=_elapsed_ms(started),
            response_status=result.status_code if result is not None else 404,
            response_body=result.body if result is not None else None,
        )
        return result

    async def _mark_issued(self, invoice: Invoice, result: ProviderResult) -> IssueInvoiceResult:
        invoice = await self.repository.set_status(
            invoice.id, InvoiceStatus.ISSUED, external_reference=result.external_reference
        )
        return IssueInvoiceResult(invoice)

    @staticmethod
    def _new_invoice(command: IssueInvoiceCommand, provider: InvoiceProvider) -> Invoice:
        return Invoice(
            idempotency_key=command.idempotency_key,
            entity_id=command.entity_id,
            amount=command.amount,
            currency=command.currency,
            country_code=command.country_code,
            provider=provider.name,
            status=InvoiceStatus.PENDING,
        )


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)

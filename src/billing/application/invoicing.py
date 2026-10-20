import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from billing.domain.invoice import AttemptKind, AttemptOutcome, InvoiceErrorCode, InvoiceStatus
from billing.domain.provider import (
    InvoiceData,
    InvoiceProvider,
    ProviderError,
    ProviderRejectedError,
    ProviderResult,
    ProviderTimeoutError,
    ProviderTransientError,
)
from billing.domain.registry import get_provider
from billing.infra.db.models import Invoice
from billing.infra.db.repository import DuplicateIdempotencyKeyError, InvoiceRepository
from billing.infra.retry import retrying

logger = logging.getLogger(__name__)

_ATTEMPT_OUTCOMES: dict[type[ProviderError], AttemptOutcome] = {
    ProviderTransientError: AttemptOutcome.RETRYABLE_ERROR,
    ProviderTimeoutError: AttemptOutcome.TIMEOUT,
    ProviderRejectedError: AttemptOutcome.REJECTED,
}


class UnsupportedCurrencyError(ValueError):
    def __init__(self, country_code: str, currency: str, supported: frozenset[str]) -> None:
        super().__init__(f"{country_code} does not support {currency}")
        self.supported = sorted(supported)


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

    def request_hash(self) -> str:
        canonical = {
            "entity_id": self.entity_id,
            "amount": f"{self.amount:.2f}",
            "currency": self.currency,
            "country_code": self.country_code,
        }
        return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class IssueResult:
    invoice: Invoice
    replayed: bool = False


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

    async def issue(self, command: IssueInvoiceCommand) -> IssueResult:
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
            self._log("invoice created", invoice)
            return IssueResult(await self._issue(invoice, provider))

        if invoice.request_hash != command.request_hash():
            raise IdempotencyKeyConflictError(invoice.id)
        if invoice.status is InvoiceStatus.ISSUED:
            self._log("invoice replayed", invoice)
            return IssueResult(invoice, replayed=True)

        return IssueResult(await self._resume(invoice, provider))

    async def _resume(self, invoice: Invoice, provider: InvoiceProvider) -> Invoice:
        """Never issue twice: ask the provider first whether a previous attempt got through."""
        self._log("invoice resumed", invoice)
        lookup = await self._call_with_retry(
            invoice,
            AttemptKind.LOOKUP,
            {"invoice_id": str(invoice.id)},
            lambda: provider.lookup(invoice.id),
        )
        if lookup.error is None:
            if lookup.result is not None:
                return await self._mark_issued(invoice, lookup.result)
            return await self._issue(invoice, provider)

        # Lookup failed: we can't tell whether it was issued, so keep its status as is.
        return await self._finalize(invoice, invoice.status, invoice.error_code, str(lookup.error))

    async def _issue(self, invoice: Invoice, provider: InvoiceProvider) -> Invoice:
        data = InvoiceData(
            invoice_id=invoice.id,
            entity_id=invoice.entity_id,
            amount=invoice.amount,
            currency=invoice.currency,
            country_code=invoice.country_code,
        )
        outcome = await self._call_with_retry(
            invoice, AttemptKind.ISSUE, provider.build_payload(data), lambda: provider.issue(data)
        )
        if outcome.result is not None:
            return await self._mark_issued(invoice, outcome.result)
        if isinstance(outcome.error, ProviderRejectedError):
            return await self._finalize(
                invoice,
                InvoiceStatus.FAILED,
                InvoiceErrorCode.PROVIDER_REJECTED,
                str(outcome.error),
            )
        # After any timeout the provider may have issued it: the outcome stays unknown.
        if outcome.timed_out:
            return await self._finalize(
                invoice,
                InvoiceStatus.PENDING,
                InvoiceErrorCode.PROVIDER_TIMEOUT,
                str(outcome.error),
            )
        return await self._finalize(
            invoice,
            InvoiceStatus.FAILED,
            InvoiceErrorCode.PROVIDER_UNAVAILABLE,
            str(outcome.error),
        )

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
        self._log("provider call started", invoice, attempt=attempt.attempt_number, kind=kind)
        started = time.monotonic()
        try:
            result = await call()
        except ProviderError as exc:
            outcome = _ATTEMPT_OUTCOMES[type(exc)]
            await self.repository.finish_attempt(
                attempt.id,
                outcome=outcome,
                duration_ms=_elapsed_ms(started),
                response_status=exc.status_code,
                response_body=exc.body,
                error=str(exc),
            )
            self._log(
                "provider call failed", invoice, attempt=attempt.attempt_number, outcome=outcome
            )
            raise
        outcome = AttemptOutcome.SUCCESS if result is not None else AttemptOutcome.NOT_FOUND
        await self.repository.finish_attempt(
            attempt.id,
            outcome=outcome,
            duration_ms=_elapsed_ms(started),
            response_status=result.status_code if result is not None else 404,
            response_body=result.body if result is not None else None,
        )
        self._log(
            "provider call finished", invoice, attempt=attempt.attempt_number, outcome=outcome
        )
        return result

    async def _mark_issued(self, invoice: Invoice, result: ProviderResult) -> Invoice:
        invoice = await self.repository.finalize(
            invoice.id, status=InvoiceStatus.ISSUED, external_reference=result.external_reference
        )
        self._log("invoice issued", invoice)
        return invoice

    async def _finalize(
        self, invoice: Invoice, status: InvoiceStatus, code: str | None, detail: str
    ) -> Invoice:
        invoice = await self.repository.finalize(
            invoice.id, status=status, error_code=code, error_detail=detail
        )
        self._log("invoice not issued", invoice, error_code=code)
        return invoice

    @staticmethod
    def _new_invoice(command: IssueInvoiceCommand, provider: InvoiceProvider) -> Invoice:
        return Invoice(
            idempotency_key=command.idempotency_key,
            request_hash=command.request_hash(),
            entity_id=command.entity_id,
            amount=command.amount,
            currency=command.currency,
            country_code=command.country_code,
            provider=provider.name,
            status=InvoiceStatus.PENDING,
        )

    @staticmethod
    def _log(message: str, invoice: Invoice, **extra: Any) -> None:
        logger.info(
            message,
            extra={
                "invoice_id": str(invoice.id),
                "provider": invoice.provider,
                "status": invoice.status,
                **extra,
            },
        )


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)

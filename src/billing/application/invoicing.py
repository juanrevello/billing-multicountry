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


class IdempotencyKeyReusedError(Exception):
    pass


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


class InvoiceService:
    def __init__(self, repository: InvoiceRepository) -> None:
        self.repository = repository

    async def get(self, invoice_id: uuid.UUID) -> Invoice | None:
        return await self.repository.get_with_attempts(invoice_id)

    async def issue(self, command: IssueInvoiceCommand) -> Invoice:
        provider = get_provider(command.country_code)
        if command.currency not in provider.supported_currencies:
            raise UnsupportedCurrencyError(
                command.country_code, command.currency, provider.supported_currencies
            )

        # Replay and resume for a reused key come with the idempotency stage.
        if await self.repository.get_by_idempotency_key(command.idempotency_key):
            raise IdempotencyKeyReusedError(str(command.idempotency_key))
        try:
            invoice = await self.repository.create(
                Invoice(
                    idempotency_key=command.idempotency_key,
                    request_hash=command.request_hash(),
                    entity_id=command.entity_id,
                    amount=command.amount,
                    currency=command.currency,
                    country_code=command.country_code,
                    provider=provider.name,
                    status=InvoiceStatus.PENDING,
                )
            )
        except DuplicateIdempotencyKeyError as exc:
            raise IdempotencyKeyReusedError(str(command.idempotency_key)) from exc

        self._log("invoice created", invoice)
        return await self._issue_with_provider(invoice, provider)

    async def _issue_with_provider(self, invoice: Invoice, provider: InvoiceProvider) -> Invoice:
        data = InvoiceData(
            invoice_id=invoice.id,
            entity_id=invoice.entity_id,
            amount=invoice.amount,
            currency=invoice.currency,
            country_code=invoice.country_code,
        )
        payload = provider.build_payload(data)
        result: ProviderResult | None = None
        timed_out = False
        try:
            async for attempt in retrying(invoice.country_code):
                with attempt:
                    try:
                        result = await self._attempt(
                            invoice, AttemptKind.ISSUE, payload, lambda: provider.issue(data)
                        )
                    except ProviderTimeoutError:
                        timed_out = True
                        raise
        except ProviderRejectedError as exc:
            return await self._finalize(
                invoice, InvoiceStatus.FAILED, InvoiceErrorCode.PROVIDER_REJECTED, str(exc)
            )
        except ProviderError as exc:
            # After any timeout the provider may have issued it: the outcome stays unknown.
            if timed_out:
                return await self._finalize(
                    invoice, InvoiceStatus.PENDING, InvoiceErrorCode.PROVIDER_TIMEOUT, str(exc)
                )
            return await self._finalize(
                invoice, InvoiceStatus.FAILED, InvoiceErrorCode.PROVIDER_UNAVAILABLE, str(exc)
            )

        assert result is not None
        invoice = await self.repository.finalize(
            invoice.id, status=InvoiceStatus.ISSUED, external_reference=result.external_reference
        )
        self._log("invoice issued", invoice)
        return invoice

    async def _attempt(
        self,
        invoice: Invoice,
        kind: AttemptKind,
        payload: dict[str, Any],
        call: Callable[[], Awaitable[ProviderResult]],
    ) -> ProviderResult:
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
        await self.repository.finish_attempt(
            attempt.id,
            outcome=AttemptOutcome.SUCCESS,
            duration_ms=_elapsed_ms(started),
            response_status=result.status_code,
            response_body=result.body,
        )
        self._log("provider call succeeded", invoice, attempt=attempt.attempt_number)
        return result

    async def _finalize(
        self, invoice: Invoice, status: InvoiceStatus, code: InvoiceErrorCode, detail: str
    ) -> Invoice:
        invoice = await self.repository.finalize(
            invoice.id, status=status, error_code=code, error_detail=detail
        )
        self._log("invoice not issued", invoice, error_code=code)
        return invoice

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

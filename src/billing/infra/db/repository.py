import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from billing.domain.invoice import AttemptKind, AttemptOutcome, InvoiceStatus
from billing.infra.db.models import Invoice, InvoiceAttempt


class DuplicateIdempotencyKeyError(Exception):
    pass


class InvoiceRepository:
    """Each method runs in its own short transaction; no session outlives a call."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get_by_idempotency_key(self, key: uuid.UUID) -> Invoice | None:
        async with self._sessionmaker() as session:
            return await session.scalar(select(Invoice).where(Invoice.idempotency_key == key))

    async def get_with_attempts(self, invoice_id: uuid.UUID) -> Invoice | None:
        async with self._sessionmaker() as session:
            return await session.scalar(
                select(Invoice)
                .where(Invoice.id == invoice_id)
                .options(selectinload(Invoice.attempts))
            )

    async def create(self, invoice: Invoice) -> Invoice:
        try:
            async with self._sessionmaker.begin() as session:
                session.add(invoice)
        except IntegrityError as exc:
            raise DuplicateIdempotencyKeyError(str(invoice.idempotency_key)) from exc
        return invoice

    async def start_attempt(
        self, invoice_id: uuid.UUID, kind: AttemptKind, request_payload: dict[str, Any]
    ) -> InvoiceAttempt:
        async with self._sessionmaker.begin() as session:
            last = await session.scalar(
                select(func.max(InvoiceAttempt.attempt_number)).where(
                    InvoiceAttempt.invoice_id == invoice_id
                )
            )
            attempt = InvoiceAttempt(
                invoice_id=invoice_id,
                attempt_number=(last or 0) + 1,
                kind=kind,
                request_payload=request_payload,
                outcome=AttemptOutcome.IN_FLIGHT,
            )
            session.add(attempt)
        return attempt

    async def finish_attempt(
        self,
        attempt_id: uuid.UUID,
        *,
        outcome: AttemptOutcome,
        duration_ms: int,
        response_status: int | None = None,
        response_body: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        async with self._sessionmaker.begin() as session:
            await session.execute(
                update(InvoiceAttempt)
                .where(InvoiceAttempt.id == attempt_id)
                .values(
                    outcome=outcome,
                    response_status=response_status,
                    response_body=response_body,
                    error=error,
                    finished_at=func.now(),
                    duration_ms=duration_ms,
                )
            )

    async def finalize(
        self,
        invoice_id: uuid.UUID,
        *,
        status: InvoiceStatus,
        external_reference: str | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> Invoice:
        async with self._sessionmaker.begin() as session:
            result = await session.execute(
                update(Invoice)
                .where(Invoice.id == invoice_id)
                .values(
                    status=status,
                    external_reference=external_reference,
                    error_code=error_code,
                    error_detail=error_detail,
                )
                .returning(Invoice)
            )
            return result.scalar_one()

import uuid
from typing import Any

from sqlalchemy import select, update
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
            attempt = InvoiceAttempt(
                invoice_id=invoice_id,
                kind=kind,
                outcome=AttemptOutcome.IN_FLIGHT,
                request_payload=request_payload,
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
                    duration_ms=duration_ms,
                )
            )

    async def set_status(
        self,
        invoice_id: uuid.UUID,
        status: InvoiceStatus,
        external_reference: str | None = None,
    ) -> Invoice:
        """Never downgrades an issued invoice, e.g. if a concurrent retry already issued it."""
        async with self._sessionmaker.begin() as session:
            invoice = await session.scalar(
                update(Invoice)
                .where(Invoice.id == invoice_id, Invoice.status != InvoiceStatus.ISSUED)
                .values(status=status, external_reference=external_reference)
                .returning(Invoice)
            )
            return invoice or await session.get_one(Invoice, invoice_id)

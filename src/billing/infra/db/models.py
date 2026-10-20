import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from billing.domain.invoice import AttemptKind, AttemptOutcome, InvoiceStatus


class Base(DeclarativeBase):
    pass


def _str_enum(enum_cls: type[StrEnum]) -> Enum:
    # Stored as VARCHAR + CHECK with the enum values, not as a native PG enum.
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(unique=True)
    entity_id: Mapped[str] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    country_code: Mapped[str] = mapped_column(String(2))
    provider: Mapped[str] = mapped_column(String(64))
    status: Mapped[InvoiceStatus] = mapped_column(_str_enum(InvoiceStatus))
    external_reference: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    attempts: Mapped[list[InvoiceAttempt]] = relationship(order_by="InvoiceAttempt.started_at")


class InvoiceAttempt(Base):
    __tablename__ = "invoice_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), index=True)
    kind: Mapped[AttemptKind] = mapped_column(_str_enum(AttemptKind))
    outcome: Mapped[AttemptOutcome] = mapped_column(_str_enum(AttemptOutcome))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response_status: Mapped[int | None]
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    duration_ms: Mapped[int | None]

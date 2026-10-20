import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    MetaData,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from billing.domain.invoice import AttemptKind, AttemptOutcome, InvoiceStatus

# Deterministic constraint names keep Alembic migrations stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    # VARCHAR + CHECK instead of a native PG enum: adding values needs no type migration.
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    country_code: Mapped[str] = mapped_column(String(2))
    provider: Mapped[str] = mapped_column(String(64))
    status: Mapped[InvoiceStatus] = mapped_column(_str_enum(InvoiceStatus, "invoice_status"))
    external_reference: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    attempts: Mapped[list[InvoiceAttempt]] = relationship(
        back_populates="invoice", order_by="InvoiceAttempt.attempt_number"
    )


class InvoiceAttempt(Base):
    __tablename__ = "invoice_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), index=True)
    attempt_number: Mapped[int]
    kind: Mapped[AttemptKind] = mapped_column(_str_enum(AttemptKind, "attempt_kind"))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response_status: Mapped[int | None]
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[AttemptOutcome] = mapped_column(_str_enum(AttemptOutcome, "attempt_outcome"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None]

    invoice: Mapped[Invoice] = relationship(back_populates="attempts")

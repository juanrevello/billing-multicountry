"""create invoices and invoice_attempts

Revision ID: 807d841bed62
Revises:
Create Date: 2026-10-19 19:33:59.514703

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "807d841bed62"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INVOICE_STATUS = ("pending", "issued", "failed")
ATTEMPT_KIND = ("issue", "lookup")
ATTEMPT_OUTCOME = ("in_flight", "success", "retryable_error", "rejected", "timeout", "not_found")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("external_reference", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in("status", INVOICE_STATUS), name=op.f("ck_invoices_invoice_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoices")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_invoices_idempotency_key")),
    )
    op.create_table(
        "invoice_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            _in("kind", ATTEMPT_KIND), name=op.f("ck_invoice_attempts_attempt_kind")
        ),
        sa.CheckConstraint(
            _in("outcome", ATTEMPT_OUTCOME), name=op.f("ck_invoice_attempts_attempt_outcome")
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name=op.f("fk_invoice_attempts_invoice_id_invoices")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_attempts")),
    )
    op.create_index(op.f("ix_invoice_attempts_invoice_id"), "invoice_attempts", ["invoice_id"])


def downgrade() -> None:
    op.drop_table("invoice_attempts")
    op.drop_table("invoices")

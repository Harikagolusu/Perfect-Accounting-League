"""Add invoices table (Invoice Processing foundation).

Invoices are structured accounting data read from, or entered against, a source
document. They are deliberately separate from `documents` (the file) and from
`vendors` (the supplier master).

Money columns are NUMERIC(15,2): invoice totals must never touch binary
floating point.

Revision ID: 0006_add_invoices
Revises: 0005_add_documents
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_add_invoices"
down_revision: str | None = "0005_add_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INVOICE_STATUS_VALUES = ("DRAFT", "PROCESSING", "PROCESSED", "NEEDS_REVIEW", "FAILED")
# MANUAL is the only source that exists today; OCR/AI are added when an
# extraction engine is actually configured.
INVOICE_SOURCE_VALUES = ("MANUAL",)


def upgrade() -> None:
    # The enum types are emitted by create_table itself; creating them
    # separately here would emit a second CREATE TYPE and fail.
    invoice_status = postgresql.ENUM(*INVOICE_STATUS_VALUES, name="invoice_status")
    invoice_source = postgresql.ENUM(*INVOICE_SOURCE_VALUES, name="invoice_source")
    money = sa.Numeric(15, 2)

    op.create_table(
        "invoices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("vendor_id", sa.UUID(), nullable=True),
        sa.Column("invoice_number", sa.String(length=128), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("vendor_name", sa.String(length=255), nullable=True),
        sa.Column("vendor_gstin", sa.String(length=15), nullable=True),
        sa.Column("vendor_pan", sa.String(length=10), nullable=True),
        sa.Column("buyer_gstin", sa.String(length=15), nullable=True),
        sa.Column("po_number", sa.String(length=128), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("taxable_amount", money, nullable=True),
        sa.Column("cgst_amount", money, nullable=True),
        sa.Column("sgst_amount", money, nullable=True),
        sa.Column("igst_amount", money, nullable=True),
        sa.Column("cess_amount", money, nullable=True),
        sa.Column("round_off", money, nullable=True),
        sa.Column("total_amount", money, nullable=True),
        sa.Column(
            "status",
            invoice_status,
            server_default=sa.text("'DRAFT'::invoice_status"),
            nullable=False,
        ),
        sa.Column(
            "source",
            invoice_source,
            server_default=sa.text("'MANUAL'::invoice_source"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_invoices_company_id",
            ondelete="RESTRICT",
        ),
        # RESTRICT: the uploaded file is the source artifact and must outlive
        # any invoice derived from it.
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_invoices_document_id",
            ondelete="RESTRICT",
        ),
        # SET NULL: removing a vendor master record must not delete invoices.
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name="fk_invoices_vendor_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("company_id", "document_id", "vendor_id"):
        op.create_index(f"ix_invoices_{column}", "invoices", [column], unique=False)


def downgrade() -> None:
    for column in ("vendor_id", "document_id", "company_id"):
        op.drop_index(f"ix_invoices_{column}", table_name="invoices")
    op.drop_table("invoices")
    postgresql.ENUM(name="invoice_source").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="invoice_status").drop(op.get_bind(), checkfirst=True)
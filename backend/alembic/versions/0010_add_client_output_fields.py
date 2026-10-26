"""Add canonical fields required by the client output contracts (Step 10).

The client workbooks are output contracts, not database schemas. This migration
adds only the canonical data those contracts need and that PAL did not already
have:

* company-level GST and round-off ledger names;
* vendor address, pincode and MSME registration;
* the invoice's printed place of supply;
* a bank account's ledger name and a transaction's confirmed counterparty
  mapping;
* `invoice_line_items` for item-level invoice output.

Nothing here is named after a spreadsheet column, and no extraction code is
changed. Missing values stay NULL and become export exceptions rather than
being filled with guesses.

Revision ID: 0010_add_client_output_fields
Revises: 0009_add_invoice_vendor_mapping
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_add_client_output_fields"
down_revision: str | None = "0009_add_invoice_vendor_mapping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Company chart-of-accounts names used by voucher generation.
    for column in (
        "cgst_ledger",
        "sgst_ledger",
        "igst_ledger",
        "cess_ledger",
        "round_off_ledger",
    ):
        op.add_column(
            "companies",
            sa.Column(column, sa.String(length=255), nullable=True),
        )

    # Vendor master details printed on the client invoice/voucher templates.
    op.add_column("vendors", sa.Column("address", sa.Text(), nullable=True))
    op.add_column(
        "vendors", sa.Column("pincode", sa.String(length=10), nullable=True)
    )
    op.add_column(
        "vendors",
        sa.Column("msme_registration", sa.String(length=64), nullable=True),
    )

    # Printed on the invoice, not derived from the buyer's GSTIN.
    op.add_column(
        "invoices",
        sa.Column("place_of_supply", sa.String(length=128), nullable=True),
    )

    # The bank account's ledger name and a human-confirmed counterparty.
    op.add_column(
        "bank_accounts",
        sa.Column("ledger_name", sa.String(length=255), nullable=True),
    )
    for column, length in (
        ("counterparty_name", 255),
        ("counterparty_ledger", 255),
        ("counterparty_pincode", 10),
        ("counterparty_gstin", 15),
    ):
        op.add_column(
            "bank_transactions",
            sa.Column(column, sa.String(length=length), nullable=True),
        )
    op.add_column(
        "bank_transactions",
        sa.Column("counterparty_address", sa.Text(), nullable=True),
    )

    op.create_table(
        "invoice_line_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("supply_type", sa.String(length=16), nullable=True),
        sa.Column("hsn_sac", sa.String(length=16), nullable=True),
        sa.Column("quantity", sa.Numeric(15, 3), nullable=True),
        sa.Column("gst_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("taxable_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("igst_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("cgst_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("sgst_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("cess_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=True),
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
            name="fk_invoice_line_items_company_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_line_items_invoice_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invoice_id",
            "line_number",
            name="uq_invoice_line_items_invoice_line_number",
        ),
    )
    op.create_index(
        "ix_invoice_line_items_company_id",
        "invoice_line_items",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_invoice_line_items_invoice_id",
        "invoice_line_items",
        ["invoice_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_invoice_line_items_invoice_id", table_name="invoice_line_items"
    )
    op.drop_index(
        "ix_invoice_line_items_company_id", table_name="invoice_line_items"
    )
    op.drop_table("invoice_line_items")

    op.drop_column("bank_transactions", "counterparty_address")
    for column in (
        "counterparty_gstin",
        "counterparty_pincode",
        "counterparty_ledger",
        "counterparty_name",
    ):
        op.drop_column("bank_transactions", column)
    op.drop_column("bank_accounts", "ledger_name")
    op.drop_column("invoices", "place_of_supply")
    op.drop_column("vendors", "msme_registration")
    op.drop_column("vendors", "pincode")
    op.drop_column("vendors", "address")
    for column in (
        "round_off_ledger",
        "cess_ledger",
        "igst_ledger",
        "sgst_ledger",
        "cgst_ledger",
    ):
        op.drop_column("companies", column)

"""Add bank accounts, statements and transactions (Step 6).

Three separate entities: an account (a company's relationship with a bank), a
statement (one period's document for one account), and the transactions inside
it. No transaction data lives inside `documents`.

Balances and amounts are NUMERIC(15,2) throughout: no float touches bank money.

Revision ID: 0007_add_bank_statements
Revises: 0006_add_invoices
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_add_bank_statements"
down_revision: str | None = "0006_add_invoices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATEMENT_STATUS_VALUES = ("UPLOADED", "PROCESSING", "PROCESSED", "NEEDS_REVIEW", "FAILED")
TRANSACTION_TYPE_VALUES = ("DEBIT", "CREDIT")


def upgrade() -> None:
    # The enum types are emitted by create_table itself; creating them
    # separately here would emit a second CREATE TYPE and fail.
    statement_status = postgresql.ENUM(*STATEMENT_STATUS_VALUES, name="bank_statement_status")
    transaction_type = postgresql.ENUM(*TRANSACTION_TYPE_VALUES, name="transaction_type")
    money = sa.Numeric(15, 2)

    op.create_table(
        "bank_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("bank_name", sa.String(length=128), nullable=False),
        sa.Column("account_name", sa.String(length=128), nullable=True),
        # Stored in full; the API only ever returns a masked form.
        sa.Column("account_number", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_bank_accounts_company_id", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bank_accounts_company_id", "bank_accounts", ["company_id"], unique=False)

    op.create_table(
        "bank_statements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("bank_account_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("statement_start_date", sa.Date(), nullable=True),
        sa.Column("statement_end_date", sa.Date(), nullable=True),
        sa.Column("opening_balance", money, nullable=True),
        sa.Column("closing_balance", money, nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column(
            "status",
            statement_status,
            server_default=sa.text("'UPLOADED'::bank_statement_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_bank_statements_company_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["bank_account_id"], ["bank_accounts.id"], name="fk_bank_statements_account_id", ondelete="RESTRICT"
        ),
        # The uploaded file is the artifact of record and must outlive the
        # statement derived from it.
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_bank_statements_document_id", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("company_id", "bank_account_id", "document_id"):
        op.create_index(f"ix_bank_statements_{column}", "bank_statements", [column], unique=False)

    op.create_table(
        "bank_transactions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("bank_statement_id", sa.UUID(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reference_number", sa.String(length=128), nullable=True),
        sa.Column("debit_amount", money, nullable=False, server_default="0"),
        sa.Column("credit_amount", money, nullable=False, server_default="0"),
        sa.Column("balance", money, nullable=True),
        sa.Column("transaction_type", transaction_type, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_bank_transactions_company_id", ondelete="RESTRICT"
        ),
        # Transactions have no meaning without their statement. The service
        # also removes them explicitly, so the behaviour is tested rather than
        # implied by the constraint.
        sa.ForeignKeyConstraint(
            ["bank_statement_id"],
            ["bank_statements.id"],
            name="fk_bank_transactions_statement_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("company_id", "bank_statement_id", "transaction_date"):
        op.create_index(f"ix_bank_transactions_{column}", "bank_transactions", [column], unique=False)


def downgrade() -> None:
    for column in ("transaction_date", "bank_statement_id", "company_id"):
        op.drop_index(f"ix_bank_transactions_{column}", table_name="bank_transactions")
    op.drop_table("bank_transactions")

    for column in ("document_id", "bank_account_id", "company_id"):
        op.drop_index(f"ix_bank_statements_{column}", table_name="bank_statements")
    op.drop_table("bank_statements")

    op.drop_index("ix_bank_accounts_company_id", table_name="bank_accounts")
    op.drop_table("bank_accounts")

    postgresql.ENUM(name="transaction_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="bank_statement_status").drop(op.get_bind(), checkfirst=True)
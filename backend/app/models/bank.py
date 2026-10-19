"""Bank domain models — Bank Account, Bank Statement and Bank Transaction.

Domain separation
-----------------
* A `BankAccount` is a company's account at a bank. It outlives any statement.
* A `BankStatement` is one statement covering a period, for one account,
  optionally linked to the uploaded `Document` it came from.
* A `BankTransaction` is one line within a statement. It has no meaning
  outside its statement, so the statement owns it.

None of these are documents, and none of them carry transaction data inside
`documents`. Every row carries `company_id`, and every read filters on it.

Money is `NUMERIC` everywhere and `Decimal` in Python. No float touches a
balance.

Scope discipline
----------------
Narration cleaning, counterparty detection, ledger mapping, confidence
scoring and categorisation are all later steps. None of those columns exist
here, because nothing populates them yet.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 15 total digits, 2 decimal places, matching the invoice money columns.
MONEY = Numeric(15, 2)


class BankStatementStatus(str, enum.Enum):
    """Where a statement sits in the review pipeline.

    A statement is only `PROCESSED` once a parser actually ran over it, which
    cannot happen yet, so nothing sets it today.
    """

    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class TransactionType(str, enum.Enum):
    """Direction of a transaction as the bank recorded it.

    This says nothing about accounting meaning: a bank debit is not
    necessarily an expense, and a credit is not necessarily income. Deriving
    that is a later mapping step.
    """

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


BANK_STATEMENT_STATUS_ENUM = SAEnum(
    BankStatementStatus,
    name="bank_statement_status",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

TRANSACTION_TYPE_ENUM = SAEnum(
    TransactionType,
    name="transaction_type",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class BankAccount(Base):
    """A company's account at a bank."""

    __tablename__ = "bank_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    bank_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Optional: several accounts can sit at the same bank.
    account_name: Mapped[str | None] = mapped_column(String(128))
    # Sensitive. Stored in full, never returned in full, never logged.
    account_number: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        # The account number is deliberately absent: this string can end up in
        # logs and tracebacks.
        return f"<BankAccount id={self.id} bank={self.bank_name!r} active={self.is_active}>"


class BankStatement(Base):
    """One statement for one account, covering one period."""

    __tablename__ = "bank_statements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # The uploaded file this came from. Optional, because a statement can be
    # entered by hand, and deliberately never cascaded: the document is the
    # artifact of record.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    statement_start_date: Mapped[date | None] = mapped_column(Date)
    statement_end_date: Mapped[date | None] = mapped_column(Date)
    opening_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    closing_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    status: Mapped[BankStatementStatus] = mapped_column(
        BANK_STATEMENT_STATUS_ENUM,
        nullable=False,
        default=BankStatementStatus.UPLOADED,
        server_default=BankStatementStatus.UPLOADED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<BankStatement id={self.id} company_id={self.company_id} "
            f"account_id={self.bank_account_id} status={self.status.value}>"
        )


class BankTransaction(Base):
    """One line item inside a bank statement."""

    __tablename__ = "bank_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Transactions are meaningless without their statement, so deletion of a
    # statement removes them. Handled in the service rather than by a database
    # cascade, so the behaviour is explicit and tested.
    bank_statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_statements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    value_date: Mapped[date | None] = mapped_column(Date)
    # The narration exactly as the bank printed it. Cleaning it is a parsing
    # step and is not implemented.
    description: Mapped[str | None] = mapped_column(Text)
    reference_number: Mapped[str | None] = mapped_column(String(128))

    debit_amount: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, server_default="0"
    )
    credit_amount: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, server_default="0"
    )
    balance: Mapped[Decimal | None] = mapped_column(MONEY)
    # Derived from the two amount columns and validated against them, so it
    # can never disagree with them.
    transaction_type: Mapped[TransactionType] = mapped_column(
        TRANSACTION_TYPE_ENUM, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<BankTransaction id={self.id} statement_id={self.bank_statement_id} "
            f"type={self.transaction_type.value} debit={self.debit_amount} "
            f"credit={self.credit_amount}>"
        )
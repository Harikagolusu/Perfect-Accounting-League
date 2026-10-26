"""Pydantic v2 schemas for the Bank Statements foundation.

Account numbers are write-only from the API's perspective: a request may carry
the real number, but a response returns only a masked form. Money is `Decimal`
in Python and a string on the wire, so no float is involved at any point.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.models.bank import BankStatementStatus, TransactionType
from app.schemas.common import TimestampedResponse, clean_optional

# 15 total digits, 2 decimal places, mirroring the NUMERIC columns.
Money = Annotated[Decimal, Field(max_digits=15, decimal_places=2)]
#: Emit as a string so the browser never reconstructs a float.
MoneyOut = Annotated[Decimal, PlainSerializer(lambda v: str(v), return_type=str)]

#: Characters kept visible at the end of a masked account number.
ACCOUNT_MASK_VISIBLE = 4


def mask_account_number(value: str | None) -> str | None:
    """Show only the last few digits of an account number.

    Applied by the schemas so the full number cannot leak through a response
    by accident.
    """
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) <= ACCOUNT_MASK_VISIBLE:
        return "X" * len(digits)
    return "X" * (len(digits) - ACCOUNT_MASK_VISIBLE) + digits[-ACCOUNT_MASK_VISIBLE:]


class BankAccountCreate(BaseModel):
    """Payload for POST /api/v1/companies/{company_id}/bank-accounts."""

    model_config = ConfigDict(extra="forbid")

    bank_name: str = Field(max_length=128)
    account_name: str | None = Field(default=None, max_length=128)
    # The only place a full account number is ever accepted.
    account_number: str = Field(max_length=64)
    ledger_name: str | None = Field(default=None, max_length=255)
    currency: str = Field(default="INR", min_length=3, max_length=3)

    @field_validator("bank_name", "account_name", "ledger_name", mode="before")
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)

    @field_validator("account_number", mode="before")
    @classmethod
    def _clean_account_number(cls, value: Any) -> str:
        cleaned = (clean_optional(value) or "").replace(" ", "")
        if not cleaned:
            raise ValueError("Account number is required")
        return cleaned

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: Any) -> str:
        return (clean_optional(value) or "INR").upper()


class BankAccountUpdate(BaseModel):
    """Payload for PATCH on a bank account. Only supplied fields are applied."""

    model_config = ConfigDict(extra="forbid")

    bank_name: str | None = Field(default=None, max_length=128)
    account_name: str | None = Field(default=None, max_length=128)
    account_number: str | None = Field(default=None, max_length=64)
    ledger_name: str | None = Field(default=None, max_length=255)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_active: bool | None = None

    @field_validator("bank_name", "account_name", "ledger_name", mode="before")
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)

    @field_validator("account_number", mode="before")
    @classmethod
    def _clean_account_number(cls, value: Any) -> str | None:
        cleaned = clean_optional(value)
        return cleaned.replace(" ", "") if cleaned else None

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: Any) -> str | None:
        cleaned = clean_optional(value)
        return cleaned.upper() if cleaned else None


class BankAccountResponse(TimestampedResponse):
    """A bank account. The account number is always masked."""

    id: uuid.UUID
    company_id: uuid.UUID
    bank_name: str
    account_name: str | None
    #: Masked, e.g. "XXXX1234". The full number is never returned.
    masked_account_number: str
    ledger_name: str | None
    currency: str
    is_active: bool


class BankStatementCreate(BaseModel):
    """Payload for POST on bank statements."""

    model_config = ConfigDict(extra="forbid")

    bank_account_id: uuid.UUID
    document_id: uuid.UUID | None = None
    statement_start_date: date | None = None
    statement_end_date: date | None = None
    opening_balance: Money | None = None
    closing_balance: Money | None = None
    currency: str = Field(default="INR", min_length=3, max_length=3)

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: Any) -> str:
        return (clean_optional(value) or "INR").upper()


class BankStatementUpdate(BaseModel):
    """Payload for PATCH on a bank statement. Only supplied fields apply."""

    model_config = ConfigDict(extra="forbid")

    bank_account_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    statement_start_date: date | None = None
    statement_end_date: date | None = None
    opening_balance: Money | None = None
    closing_balance: Money | None = None
    currency: str | None = None
    status: BankStatementStatus | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: Any) -> str | None:
        cleaned = clean_optional(value)
        return cleaned.upper() if cleaned else None


class BankStatementResponse(TimestampedResponse):
    """A bank statement. Never exposes the underlying account's number."""

    id: uuid.UUID
    company_id: uuid.UUID
    bank_account_id: uuid.UUID
    document_id: uuid.UUID | None
    statement_start_date: date | None
    statement_end_date: date | None
    opening_balance: MoneyOut | None
    closing_balance: MoneyOut | None
    currency: str
    status: BankStatementStatus


class BankTransactionCreate(BaseModel):
    """Payload for POST on a statement's transactions.

    Exactly one of `debit_amount` / `credit_amount` must be positive; the
    direction is derived, not trusted from the client.
    """

    model_config = ConfigDict(extra="forbid")

    transaction_date: date
    value_date: date | None = None
    description: str | None = None
    reference_number: str | None = Field(default=None, max_length=128)
    debit_amount: Money | None = None
    credit_amount: Money | None = None
    balance: Money | None = None

    # Human-confirmed ledger mapping. These are assertions about the
    # counterparty, not values read from the bank line.
    counterparty_name: str | None = Field(default=None, max_length=255)
    counterparty_ledger: str | None = Field(default=None, max_length=255)
    counterparty_address: str | None = None
    counterparty_pincode: str | None = Field(default=None, max_length=10)
    counterparty_gstin: str | None = Field(default=None, max_length=15)
    is_inter_account_transfer: bool = False

    @field_validator(
        "description",
        "reference_number",
        "counterparty_name",
        "counterparty_ledger",
        "counterparty_address",
        "counterparty_pincode",
        mode="before",
    )
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)

    @field_validator("counterparty_gstin", mode="before")
    @classmethod
    def _normalise_gstin(cls, value: Any) -> str | None:
        cleaned = clean_optional(value)
        return cleaned.upper() if cleaned else None


class BankTransactionMappingUpdate(BaseModel):
    """Payload for confirming a bank transaction's counterparty ledger.

    Only mapping fields are accepted. Amounts, dates and narration are bank
    facts and must be corrected through their own facts, not a mapping edit.
    """

    model_config = ConfigDict(extra="forbid")

    counterparty_name: str | None = Field(default=None, max_length=255)
    counterparty_ledger: str | None = Field(default=None, max_length=255)
    counterparty_address: str | None = None
    counterparty_pincode: str | None = Field(default=None, max_length=10)
    counterparty_gstin: str | None = Field(default=None, max_length=15)
    #: A reviewer may flag an inter-account transfer; PAL never infers it.
    is_inter_account_transfer: bool | None = None

    @field_validator(
        "counterparty_name",
        "counterparty_ledger",
        "counterparty_address",
        "counterparty_pincode",
        mode="before",
    )
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)

    @field_validator("counterparty_gstin", mode="before")
    @classmethod
    def _normalise_gstin(cls, value: Any) -> str | None:
        cleaned = clean_optional(value)
        return cleaned.upper() if cleaned else None


class BankTransactionResponse(TimestampedResponse):
    """One statement line. Money is returned as an exact decimal string."""

    id: uuid.UUID
    company_id: uuid.UUID
    bank_statement_id: uuid.UUID
    transaction_date: date
    value_date: date | None
    description: str | None
    reference_number: str | None
    debit_amount: MoneyOut
    credit_amount: MoneyOut
    balance: MoneyOut | None
    counterparty_name: str | None
    counterparty_ledger: str | None
    counterparty_address: str | None
    counterparty_pincode: str | None
    counterparty_gstin: str | None
    is_inter_account_transfer: bool
    transaction_type: TransactionType


class ProcessStatementResponse(BaseModel):
    """Result of asking the system to parse a statement automatically."""

    statement_id: uuid.UUID
    configured: bool
    reason: str | None = None
    message: str
    issues: list[str] = Field(default_factory=list)
    transaction_count: int | None = None


__all__ = [
    "BankAccountCreate",
    "BankAccountResponse",
    "BankAccountUpdate",
    "BankStatementCreate",
    "BankStatementResponse",
    "BankStatementUpdate",
    "BankTransactionCreate",
    "BankTransactionMappingUpdate",
    "BankTransactionResponse",
    "ProcessStatementResponse",
    "mask_account_number",
]
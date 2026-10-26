"""Exact column contract for `bank upload reference.xlsx`.

One data sheet, `Accounting Voucher`, with fifteen columns. The Read Me sheet
is preserved from the bundled client template.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.exports.common.models import Voucher

BANK_TEMPLATE = "bank_upload_reference.xlsx"
BANK_SHEET = "Accounting Voucher"

#: `Change Mode ` intentionally keeps the client's trailing space.
BANK_COLUMNS: tuple[str, ...] = (
    "Voucher Date",
    "Voucher Type Name",
    "Voucher Number",
    "Buyer/Supplier - Address",
    "Buyer/Supplier - Pincode",
    "Ledger Name",
    "Ledger Amount",
    "Ledger Amount Dr/Cr",
    "Item Name",
    "Billed Quantity",
    "Item Rate",
    "Item Rate per",
    "Item Amount",
    "Change Mode ",
    "Voucher Narration",
)


@dataclass(frozen=True)
class BankVoucherRow:
    voucher_date: str
    voucher_type: str
    voucher_number: int
    party_address: str | None
    party_pincode: str | None
    ledger_name: str
    ledger_amount: Decimal
    side: str
    item_name: str | None
    billed_quantity: Decimal | None
    item_rate: Decimal | None
    item_rate_per: str | None
    item_amount: Decimal | None
    change_mode: str
    voucher_narration: str | None

    def as_values(self) -> tuple[object, ...]:
        return (
            self.voucher_date,
            self.voucher_type,
            self.voucher_number,
            self.party_address,
            self.party_pincode,
            self.ledger_name,
            self.ledger_amount,
            self.side,
            self.item_name,
            self.billed_quantity,
            self.item_rate,
            self.item_rate_per,
            self.item_amount,
            self.change_mode,
            self.voucher_narration,
        )


def voucher_rows(voucher: Voucher) -> list[BankVoucherRow]:
    """Flatten one voucher's ledger rows into the bank sheet's row shape."""
    rows: list[BankVoucherRow] = []
    for line in voucher.lines:
        rows.append(
            BankVoucherRow(
                voucher_date=voucher.voucher_date.strftime("%d %b %Y"),
                voucher_type=voucher.voucher_type,
                voucher_number=voucher.voucher_number,
                party_address=line.party_address,
                party_pincode=line.party_pincode,
                ledger_name=line.ledger_name,
                ledger_amount=line.amount,
                side=line.side,
                item_name=line.item_name,
                billed_quantity=line.billed_quantity,
                item_rate=line.item_rate,
                item_rate_per=line.item_rate_per,
                item_amount=line.item_amount,
                change_mode=voucher.change_mode,
                voucher_narration=voucher.narration,
            )
        )
    return rows


__all__ = [
    "BANK_COLUMNS",
    "BANK_SHEET",
    "BANK_TEMPLATE",
    "BankVoucherRow",
    "voucher_rows",
]

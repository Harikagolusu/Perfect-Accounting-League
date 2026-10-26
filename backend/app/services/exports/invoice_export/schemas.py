"""Exact column contract for `Invoice Requirements v.00.xlsx`.

The client's spelling ("Item Discription") is preserved verbatim. The template
has one sheet, `Sheet1`, and one header row of eighteen columns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

INVOICE_TEMPLATE = "invoice_requirements.xlsx"
INVOICE_SHEET = "Sheet1"

#: Exact order and exact spelling from the client workbook.
INVOICE_COLUMNS: tuple[str, ...] = (
    "Inv Date",
    "Inv Number",
    "Name",
    "MSME Reg. No",
    "GST Reg No",
    "PAN",
    "Place of Supply",
    "Address",
    "Item Discription",
    "Service/Goods",
    "HSN",
    "Quantity",
    "GST Rate",
    "Taxable Amount",
    "IGST",
    "CGST",
    "SGST",
    "Invoice Amount",
)

SUPPLY_TYPE_LABELS = {"GOODS": "Goods", "SERVICE": "Service"}


@dataclass(frozen=True)
class InvoiceExportRow:
    invoice_date: date
    invoice_number: str
    name: str
    msme_registration: str | None
    gstin: str
    pan: str | None
    place_of_supply: str
    address: str | None
    item_description: str
    supply_type: str
    hsn_sac: str
    quantity: Decimal
    gst_rate: Decimal
    taxable_amount: Decimal
    igst_amount: Decimal | None
    cgst_amount: Decimal | None
    sgst_amount: Decimal | None
    invoice_amount: Decimal

    def as_values(self) -> tuple[object, ...]:
        return (
            self.invoice_date,
            self.invoice_number,
            self.name,
            self.msme_registration,
            self.gstin,
            self.pan,
            self.place_of_supply,
            self.address,
            self.item_description,
            self.supply_type,
            self.hsn_sac,
            self.quantity,
            self.gst_rate,
            self.taxable_amount,
            self.igst_amount,
            self.cgst_amount,
            self.sgst_amount,
            self.invoice_amount,
        )


__all__ = [
    "INVOICE_COLUMNS",
    "INVOICE_SHEET",
    "INVOICE_TEMPLATE",
    "SUPPLY_TYPE_LABELS",
    "InvoiceExportRow",
]

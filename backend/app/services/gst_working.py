"""Compute GSTR-1 and GSTR-3B workings from PAL's own processed invoices.

Read-only projection of recorded facts, exactly like the accounting-voucher
export. Nothing is written back. The numbers are derived from the invoices
stored for the company in the chosen period, so a reload re-derives the same
working — the persistence is the invoice table itself.

Classification is deterministic and uses only what the invoice already carries:

* direction (SALES feeds GSTR-1 outward, PURCHASE feeds GSTR-3B ITC);
* buyer GSTIN (present => B2B registered);
* the printed tax components (IGST present => inter-state, the only signal that
  is the printed truth rather than a guess);
* place of supply (export heads only).

Heads that need a flag PAL does not record — reverse charge, ineligible ITC,
the GSTR-1 HSN summary — are left at zero for a reviewer to fill. Money is
Decimal throughout; no float ever enters a tax total.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.invoice import Invoice, InvoiceDirection, InvoiceStatus
from app.services.company import get_company

ZERO = Decimal("0")

# GSTR-1 outward categories, in the order the UI renders them.
R1_B2B = "B2B - Registered"
R1_B2C_LARGE = "B2C Large (Inter-state)"
R1_B2C_OTHERS = "B2C Others"
R1_EXPORTS = "Exports (with payment)"
R1_NIL = "Nil-rated / Exempt"
R1_CATEGORIES = [R1_B2B, R1_B2C_LARGE, R1_B2C_OTHERS, R1_EXPORTS, R1_NIL]

# GSTR-3B categories, in UI order.
R3_OUTWARD = "3.1(a) Outward taxable supplies"
R3_RCM = "3.1(d) Inward supplies - reverse charge"
R3_ITC = "4(A)(5) All other ITC - Eligible"
R3_ITC_REVERSED = "4(B) ITC Reversed / Ineligible"
R3_CATEGORIES = [R3_OUTWARD, R3_RCM, R3_ITC, R3_ITC_REVERSED]

# shortcut: legacy ₹2.5L inter-state B2C-large threshold; revisit if the client
# follows a different cut-off for a given period.
B2C_LARGE_THRESHOLD = Decimal("250000")


@dataclass
class _Totals:
    """Mutable accumulator for one working row."""

    taxable: Decimal = ZERO
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO

    def add(self, inv: Invoice) -> None:
        self.taxable += inv.taxable_amount or ZERO
        self.igst += inv.igst_amount or ZERO
        self.cgst += inv.cgst_amount or ZERO
        self.sgst += inv.sgst_amount or ZERO


@dataclass
class GstWorkingRow:
    """One labelled row of a working."""

    category: str
    totals: _Totals = field(default_factory=_Totals)


@dataclass(frozen=True)
class GstWorkingResult:
    """GSTR-1 and GSTR-3B workings derived from stored invoices."""

    company_id: uuid.UUID
    from_date: date | None
    to_date: date | None
    invoices_used: int
    gstr1: list[GstWorkingRow]
    gstr3b: list[GstWorkingRow]


def _state_code(value: str | None) -> str | None:
    """Leading two-digit state code of a GSTIN or place-of-supply string."""
    if not value:
        return None
    digits = value.strip()[:2]
    return digits if digits.isdigit() else None


def _is_export(invoice: Invoice) -> bool:
    """True when the place of supply names an export / overseas supply.

    Export place-of-supply carries state code 96 (other country); the words
    cover sales registers that spell it out instead of using the code.
    """
    pos = (invoice.place_of_supply or "").lower()
    if _state_code(invoice.place_of_supply) == "96":
        return True
    return any(word in pos for word in ("export", "overseas", "sez"))


def _has_tax(invoice: Invoice) -> bool:
    return bool((invoice.igst_amount or ZERO) or (invoice.cgst_amount or ZERO) or (invoice.sgst_amount or ZERO))


def _is_inter_state(invoice: Invoice, home_code: str | None) -> bool:
    """Inter-state when IGST was charged; fall back to state codes for nil tax."""
    if (invoice.igst_amount or ZERO) > ZERO:
        return True
    if _has_tax(invoice):  # CGST/SGST charged => intra-state
        return False
    pos_code = _state_code(invoice.place_of_supply)
    return bool(pos_code and home_code and pos_code != home_code)


def _classify_sale(invoice: Invoice, home_code: str | None) -> str:
    """GSTR-1 category for one sales invoice."""
    if _is_export(invoice):
        return R1_EXPORTS
    if not _has_tax(invoice) and (invoice.taxable_amount or ZERO) > ZERO:
        return R1_NIL
    if invoice.buyer_gstin:
        return R1_B2B
    if _is_inter_state(invoice, home_code) and (invoice.total_amount or ZERO) > B2C_LARGE_THRESHOLD:
        return R1_B2C_LARGE
    return R1_B2C_OTHERS


def compute_invoice_gst_working(
    db: Session,
    company_id: uuid.UUID,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> GstWorkingResult:
    """Build GSTR-1 and GSTR-3B workings from a company's processed invoices.

    Only ``PROCESSED`` invoices with an ``invoice_date`` inside the period are
    counted; an invoice without a date cannot be placed in a tax period and is
    skipped. Raises ``CompanyNotFoundError`` when the company is not in PAL.
    """
    company = get_company(db, company_id)
    home_code = _state_code(company.gstin)

    stmt = select(Invoice).where(
        Invoice.company_id == company_id,
        Invoice.status == InvoiceStatus.PROCESSED,
    )
    if from_date is not None:
        stmt = stmt.where(Invoice.invoice_date >= from_date)
    if to_date is not None:
        stmt = stmt.where(Invoice.invoice_date <= to_date)

    r1 = {c: GstWorkingRow(c) for c in R1_CATEGORIES}
    r3 = {c: GstWorkingRow(c) for c in R3_CATEGORIES}
    used = 0

    for inv in db.scalars(stmt).all():
        if inv.invoice_date is None:
            continue
        used += 1
        if inv.direction == InvoiceDirection.SALES:
            category = _classify_sale(inv, home_code)
            r1[category].totals.add(inv)
            # Every taxable/zero-rated outward supply rolls into 3.1(a);
            # nil-rated/exempt belongs to 3.1(c), which has no row here.
            if category != R1_NIL:
                r3[R3_OUTWARD].totals.add(inv)
        else:  # PURCHASE -> eligible ITC. RCM and ineligible need a flag we
            # do not record, so they stay at zero for a reviewer.
            r3[R3_ITC].totals.add(inv)

    return GstWorkingResult(
        company_id=company_id,
        from_date=from_date,
        to_date=to_date,
        invoices_used=used,
        gstr1=[r1[c] for c in R1_CATEGORIES],
        gstr3b=[r3[c] for c in R3_CATEGORIES],
    )

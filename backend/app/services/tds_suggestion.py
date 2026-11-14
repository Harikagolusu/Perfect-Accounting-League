"""Suggest the TDS deduction on a purchase invoice's voucher.

Client example: Professional charges Dr 100, GST Dr 18, TDS Cr 10, party Cr
108. TDS is on the taxable value, never the GST. The section comes from the
expense ledger or line descriptions when they name it ("Audit Fees" -> 194J),
otherwise from the AI. The rate comes from Settings -> TDS Rule Configuration.
Every suggestion carries a WARNING, so the voucher stays in review: annual
thresholds, lower-deduction certificates and 206AB are not checked here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.invoice import Invoice, InvoiceDirection
from app.models.invoice_line_item import InvoiceLineItem
from app.models.ledger import Ledger
from app.schemas.voucher import VoucherIssueResponse
from app.services.company_settings import get_company_settings
from app.services.ledger import list_ledgers
from app.services.ledger_ai_suggestion import suggest_tds_section_with_ai
from app.services.tally_sync import _TDS_RATES, _match_tds_section

ZERO = Decimal("0")


@dataclass(frozen=True)
class TdsSuggestion:
    section: str
    rate: Decimal
    amount: Decimal
    ledger: Ledger | None
    source: str  # "LEDGER_NAME" or "AI"
    reason: str

    @property
    def narration(self) -> str:
        return f"TDS u/s {self.section} @ {self.rate.normalize():f}%"


def _rate(db: Session, company_id: uuid.UUID, section: str, pan: str | None) -> Decimal:
    settings = get_company_settings(db, company_id)
    configured = ((settings.data if settings else {}).get("fields") or {}).get(f"tds{section}")
    try:
        rate = Decimal(str(configured)) if configured not in (None, "") else _TDS_RATES[section]
    except InvalidOperation:
        rate = _TDS_RATES[section]
    # 194C is 1% for an individual or HUF (4th PAN letter P or H), 2% otherwise.
    if section == "194C" and pan and len(pan) >= 4 and pan[3].upper() in "PH":
        return min(rate, Decimal("1"))
    return rate


def _tds_ledger(db: Session, company_id: uuid.UUID, section: str) -> Ledger | None:
    """The company's TDS payable ledger for this section, or None."""
    candidates = [
        ledger
        for ledger in list_ledgers(db, company_id)
        if "tds" in ledger.name.lower()
        and not any(word in ledger.name.lower() for word in ("receivable", "salary", "192", "195"))
    ]
    for ledger in candidates:
        if section in ledger.name.upper():
            return ledger
    by_keyword = [ledger for ledger in candidates if _match_tds_section(ledger.name) == section]
    if by_keyword:
        # Several fit ("TDS on Contract" and "TDS on Contract Section 393(1)",
        # or "TDS on Professional" and "TDS on Technical Services"): take the
        # plainest name. The voucher stays in review, so the user can switch.
        return min(by_keyword, key=lambda ledger: (len(ledger.name), ledger.name))
    generic = [ledger for ledger in candidates if _match_tds_section(ledger.name) is None]
    return generic[0] if len(generic) == 1 else None


def suggest_tds(
    db: Session,
    company_id: uuid.UUID,
    invoice: Invoice,
    *,
    items: list[InvoiceLineItem],
    vendor_name: str | None,
    expense_ledger: str | None,
) -> tuple[TdsSuggestion | None, list[VoucherIssueResponse]]:
    if invoice.direction is InvoiceDirection.SALES:
        return None, []
    if invoice.taxable_amount is None or invoice.taxable_amount <= ZERO:
        return None, []
    if (invoice.currency or "INR").upper() != "INR":
        # A foreign supplier is a non-resident: sections 194C/J/I/H do not
        # apply. Section 195 depends on the DTAA and the supplier's documents.
        return None, [
            VoucherIssueResponse(
                code="TDS_NON_RESIDENT",
                message=(
                    "Foreign-currency invoice: no 194 TDS suggested. Check whether "
                    "section 195 TDS applies under the DTAA before approval."
                ),
                severity="WARNING",
                field="TDS",
            )
        ]

    section = _match_tds_section(expense_ledger or "") or _match_tds_section(
        " ".join(item.description for item in items)
    )
    source, reason = "LEDGER_NAME", f"Expense '{expense_ledger}' attracts this section."
    if section is None:
        section, reason = suggest_tds_section_with_ai(
            invoice=invoice, items=items, vendor_name=vendor_name, expense_ledger=expense_ledger
        )
        source = "AI"
    if section is None or section not in _TDS_RATES:
        return None, []

    pan = invoice.vendor_pan or (invoice.vendor_gstin[2:12] if invoice.vendor_gstin else None)
    rate = _rate(db, company_id, section, pan)
    # Section 288B: TDS is rounded to the nearest rupee.
    amount = (invoice.taxable_amount * rate / 100).quantize(Decimal("1"), ROUND_HALF_UP)
    if amount <= ZERO:
        return None, []

    ledger = _tds_ledger(db, company_id, section)
    suggestion = TdsSuggestion(section, rate, amount, ledger, source, reason)
    issues = [
        VoucherIssueResponse(
            code="TDS_SUGGESTED",
            message=(
                f"{suggestion.narration} = ₹{amount} on taxable ₹{invoice.taxable_amount} "
                f"({'AI' if source == 'AI' else 'from the expense ledger'}). Confirm the "
                "section, rate and annual threshold before approval."
            ),
            severity="WARNING",
            field="TDS",
        )
    ]
    if ledger is None:
        issues.append(
            VoucherIssueResponse(
                code="MISSING_LEDGER",
                message=f"No TDS ledger for {section} in the Ledger Master. Select one.",
                field="TDS Ledger",
            )
        )
    return suggestion, issues


__all__ = ["TdsSuggestion", "suggest_tds"]

"""Tests for GST workings computed from PAL's own processed invoices."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.document import Document
from app.models.invoice import Invoice, InvoiceDirection, InvoiceStatus
from app.services.gst_working import (
    R1_B2B,
    R1_B2C_LARGE,
    R1_B2C_OTHERS,
    R1_EXPORTS,
    R1_NIL,
    R3_ITC,
    R3_OUTWARD,
    compute_invoice_gst_working,
)


def _company(db: Session) -> Company:
    # GSTIN state code 27 (Maharashtra) is this company's home state.
    company = Company(name="Home Co", gstin="27AAJCS4821K1Z5")
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def _doc(db: Session, company_id) -> Document:
    doc = Document(
        company_id=company_id,
        original_filename="x.pdf",
        stored_filename="x.pdf",
        file_path="x.pdf",
        file_type="application/pdf",
        file_size=1,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _inv(db: Session, company_id, doc_id, **kw) -> Invoice:
    defaults = dict(
        company_id=company_id,
        document_id=doc_id,
        invoice_date=date(2026, 7, 15),
        status=InvoiceStatus.PROCESSED,
        direction=InvoiceDirection.SALES,
    )
    inv = Invoice(**{**defaults, **kw})
    db.add(inv)
    db.commit()
    return inv


def _row(rows, category):
    return next(r for r in rows if r.category == category)


def test_classifies_outward_and_itc(db: Session):
    co = _company(db)
    doc = _doc(db, co.id)
    # B2B intra-state sale: buyer has a GSTIN, CGST+SGST charged.
    _inv(db, co.id, doc.id, buyer_gstin="27AAJCS4821K1Z5",
         taxable_amount=Decimal("1000"), cgst_amount=Decimal("90"), sgst_amount=Decimal("90"),
         total_amount=Decimal("1180"))
    # B2C large inter-state: no buyer GSTIN, IGST charged, value over threshold.
    _inv(db, co.id, doc.id, taxable_amount=Decimal("300000"), igst_amount=Decimal("54000"),
         total_amount=Decimal("354000"))
    # B2C small: no GSTIN, intra-state, under threshold.
    _inv(db, co.id, doc.id, taxable_amount=Decimal("500"), cgst_amount=Decimal("45"),
         sgst_amount=Decimal("45"), total_amount=Decimal("590"))
    # Nil-rated: taxable value but no tax.
    _inv(db, co.id, doc.id, taxable_amount=Decimal("200"), total_amount=Decimal("200"))
    # Export with payment.
    _inv(db, co.id, doc.id, place_of_supply="96-Other Country",
         taxable_amount=Decimal("700"), igst_amount=Decimal("126"), total_amount=Decimal("826"))
    # Purchase feeds eligible ITC, never GSTR-1.
    _inv(db, co.id, doc.id, direction=InvoiceDirection.PURCHASE,
         taxable_amount=Decimal("400"), igst_amount=Decimal("72"), total_amount=Decimal("472"))

    result = compute_invoice_gst_working(db, co.id)

    assert result.invoices_used == 6
    assert _row(result.gstr1, R1_B2B).totals.taxable == Decimal("1000")
    assert _row(result.gstr1, R1_B2C_LARGE).totals.igst == Decimal("54000")
    assert _row(result.gstr1, R1_B2C_OTHERS).totals.taxable == Decimal("500")
    assert _row(result.gstr1, R1_NIL).totals.taxable == Decimal("200")
    assert _row(result.gstr1, R1_EXPORTS).totals.igst == Decimal("126")
    # 3.1(a) sums every outward supply except nil-rated: 1000+300000+500+700.
    assert _row(result.gstr3b, R3_OUTWARD).totals.taxable == Decimal("302200")
    # Eligible ITC comes only from the purchase.
    assert _row(result.gstr3b, R3_ITC).totals.igst == Decimal("72")


def test_period_and_status_filter(db: Session):
    co = _company(db)
    doc = _doc(db, co.id)
    # Outside the period.
    _inv(db, co.id, doc.id, invoice_date=date(2026, 5, 1), taxable_amount=Decimal("1000"),
         cgst_amount=Decimal("90"), sgst_amount=Decimal("90"), buyer_gstin="27X")
    # In period but still a draft — must not be counted.
    _inv(db, co.id, doc.id, status=InvoiceStatus.DRAFT, taxable_amount=Decimal("1000"),
         cgst_amount=Decimal("90"), sgst_amount=Decimal("90"), buyer_gstin="27X")
    # In period and processed.
    _inv(db, co.id, doc.id, taxable_amount=Decimal("2000"), cgst_amount=Decimal("180"),
         sgst_amount=Decimal("180"), buyer_gstin="27X")

    result = compute_invoice_gst_working(
        db, co.id, from_date=date(2026, 7, 1), to_date=date(2026, 7, 31)
    )

    assert result.invoices_used == 1
    assert _row(result.gstr1, R1_B2B).totals.taxable == Decimal("2000")

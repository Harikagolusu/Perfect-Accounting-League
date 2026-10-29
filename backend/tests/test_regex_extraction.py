"""Regex candidate extraction.

Candidates are hints for the text model, so the tests focus on precision:
identifiers, dates and amounts that are actually printed, plus the common false
positives that must not become candidates.
"""

from __future__ import annotations

from app.services.regex_extraction import (
    CANDIDATE_FIELDS,
    RegexExtractor,
    render_candidates_for_prompt,
)
from app.services.text_quality import PageText

INVOICE_TEXT = """\
TAX INVOICE
Deccan Freight Carriers
GSTIN: 27AACCD1234F1Z2
PAN: AACCD1234F
Invoice No: DFC/2026/0042
Invoice Date: 15/03/2026
Due Date: 14/04/2026
Buyer GSTIN: 27AAJCS4821K1Z5
PO No: PO-8891
Taxable Amount: 1,000.00
CGST @ 9%: 90.00
SGST @ 9%: 90.00
Round Off: 0.00
Grand Total: 1,180.00
Currency: INR
"""


def _extract(text: str = INVOICE_TEXT):
    return RegexExtractor().extract((PageText(1, text),))


def test_identifiers_are_found_from_printed_labels() -> None:
    candidates = _extract()

    assert [c.value for c in candidates.get("invoice_number")] == [
        "DFC/2026/0042"
    ]
    assert [c.value for c in candidates.get("po_number")] == ["PO-8891"]
    assert [c.value for c in candidates.get("vendor_gstin")] == [
        "27AACCD1234F1Z2"
    ]
    assert [c.value for c in candidates.get("buyer_gstin")] == [
        "27AAJCS4821K1Z5"
    ]
    assert [c.value for c in candidates.get("vendor_pan")] == ["AACCD1234F"]


def test_dates_use_their_printed_labels() -> None:
    candidates = _extract()

    assert [c.value for c in candidates.get("invoice_date")] == ["15/03/2026"]
    assert [c.value for c in candidates.get("due_date")] == ["14/04/2026"]


def test_invoice_date_is_found_under_alternate_labels() -> None:
    for label in (
        "Invoice Date",
        "Bill Date",
        "Dated",
        "Date",
        "Dt.",
        "Invoice Dt.",
        "Date of Invoice",
        "Date of Issue",
        "Issue Date",
        "Billing Date",
        "Voucher Date",
        "InvoiceDate",
    ):
        candidates = _extract(f"TAX INVOICE\n{label}: 15/03/2026\n")
        assert [c.value for c in candidates.get("invoice_date")] == [
            "15/03/2026"
        ], label


def test_one_line_holding_both_dates_yields_both() -> None:
    candidates = _extract(
        "TAX INVOICE\nInvoice Date: 15/03/2026   Due Date: 14/04/2026\n"
    )

    assert [c.value for c in candidates.get("invoice_date")] == ["15/03/2026"]
    assert [c.value for c in candidates.get("due_date")] == ["14/04/2026"]


def test_a_date_label_inside_a_longer_word_is_not_a_label() -> None:
    candidates = _extract("TAX INVOICE\nLast Updated 15/03/2026\n")

    assert candidates.get("invoice_date") == ()


def test_amounts_are_kept_separate_and_rates_are_not_amounts() -> None:
    candidates = _extract()

    assert [c.value for c in candidates.get("taxable_amount")] == ["1,000.00"]
    assert [c.value for c in candidates.get("cgst_amount")] == ["90.00"]
    assert [c.value for c in candidates.get("sgst_amount")] == ["90.00"]
    assert [c.value for c in candidates.get("igst_amount")] == []
    assert [c.value for c in candidates.get("round_off")] == ["0.00"]
    assert [c.value for c in candidates.get("total_amount")] == ["1,180.00"]


def test_currency_is_found_as_an_iso_code() -> None:
    candidates = _extract()

    assert [c.value for c in candidates.get("currency")] == ["INR"]


def test_vendor_name_comes_from_a_printed_label_or_the_header() -> None:
    candidates = _extract()

    assert [c.value for c in candidates.get("vendor_name")] == [
        "Deccan Freight Carriers"
    ]


def test_label_without_a_value_does_not_capture_the_next_label() -> None:
    candidates = _extract("Invoice No:\nDate: 15/03/2026\nTotal: 100.00")

    assert candidates.get("invoice_number") == ()
    assert [c.value for c in candidates.get("invoice_date")] == ["15/03/2026"]


def test_a_pan_inside_a_gstin_is_not_double_counted() -> None:
    candidates = _extract("GSTIN 27AACCD1234F1Z2\nTotal: 100.00")

    # The PAN is derived from the vendor GSTIN, but only one vendor-pan
    # candidate exists and it is not repeated as an explicit printed PAN.
    assert [c.value for c in candidates.get("vendor_pan")] == ["AACCD1234F"]
    assert len(candidates.get("vendor_pan")) == 1


def test_a_random_identifier_is_not_a_gstin_or_pan() -> None:
    candidates = _extract("Reference: ABC123456789XYZ\nTotal: 100.00")

    assert candidates.get("vendor_gstin") == ()
    assert candidates.get("buyer_gstin") == ()
    assert candidates.get("vendor_pan") == ()


def test_a_word_starting_with_po_is_not_a_purchase_order() -> None:
    candidates = _extract("POLICY123 reference\nTotal: 100.00")

    assert candidates.get("po_number") == ()


def test_purchase_order_heading_is_not_a_vendor_name() -> None:
    candidates = _extract("PURCHASE ORDER\nTotal: 100.00")

    assert candidates.get("vendor_name") == ()


def test_bare_tax_rate_is_not_taken_as_an_amount() -> None:
    candidates = _extract("GST @ 18%\nGrand Total: 1,180.00")

    assert candidates.get("cgst_amount") == ()
    assert [c.value for c in candidates.get("total_amount")] == ["1,180.00"]


def test_alternate_date_and_amount_formats() -> None:
    candidates = _extract(
        "Invoice Date: 15-Mar-2026\n"
        "Due Date: 14 Apr 2026\n"
        "Taxable Value: 1,18,000.50\n"
        "IGST: 21,240.09\n"
        "Total: 1,39,240.59\n"
    )

    assert [c.value for c in candidates.get("invoice_date")] == ["15-Mar-2026"]
    assert [c.value for c in candidates.get("due_date")] == ["14 Apr 2026"]
    assert [c.value for c in candidates.get("taxable_amount")] == ["1,18,000.50"]
    assert [c.value for c in candidates.get("igst_amount")] == ["21,240.09"]
    assert [c.value for c in candidates.get("total_amount")] == ["1,39,240.59"]


def test_prompt_rendering_lists_every_field_and_marks_missing_ones() -> None:
    rendered = render_candidates_for_prompt(_extract())

    for field_name in CANDIDATE_FIELDS:
        assert f"{field_name}:" in rendered
    assert "hints only" in rendered
    assert "never use a candidate" in rendered
    assert "igst_amount: none found" in rendered


def test_candidates_keep_page_numbers() -> None:
    candidates = RegexExtractor().extract(
        (
            PageText(1, "Invoice No: A-1"),
            PageText(2, "Total: 100.00"),
        )
    )

    assert candidates.get("invoice_number")[0].page == 1
    assert candidates.get("total_amount")[0].page == 2

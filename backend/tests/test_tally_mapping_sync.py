"""Learning ledger mappings from TallyPrime's historical vouchers.

The point of this sync is that a company's own Tally books already state which
expense head each party is booked to. These tests pin that the pairing is read
correctly, that tax and bank lines never masquerade as the expense head, and
that an ambiguous party is skipped rather than guessed.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

import httpx
from sqlalchemy.orm import Session

from app.integrations.tally import TallyClient, TallyService
from app.models.accounting_mapping import (
    AccountingMapping,
    AccountingMappingMatchType,
    AccountingMappingSourceType,
)
from app.models.company import Company
from app.models.ledger import Ledger, LedgerSource
from app.models.vendor import Vendor
from app.services.tally_mapping_sync import sync_tally_accounting_mappings


def _voucher(
    vtype: str, number: str, lines: list[tuple[str, str, str]]
) -> str:
    """Build one VOUCHER element. Each line is (ledger, amount, deemed)."""
    entries = "".join(
        f"<ALLLEDGERENTRIES.LIST>"
        f"<LEDGERNAME>{escape(name)}</LEDGERNAME>"
        f"<AMOUNT>{amount}</AMOUNT>"
        f"<ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE>"
        f"</ALLLEDGERENTRIES.LIST>"
        for name, amount, deemed in lines
    )
    return (
        f"<VOUCHER><DATE>20260401</DATE>"
        f"<VOUCHERTYPENAME>{vtype}</VOUCHERTYPENAME>"
        f"<VOUCHERNUMBER>{number}</VOUCHERNUMBER>"
        f"<GUID>guid-{number}</GUID>{entries}</VOUCHER>"
    )


def _envelope(vouchers: list[str]) -> str:
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
        "<BODY><DATA><COLLECTION>"
        + "".join(vouchers)
        + "</COLLECTION></DATA></BODY></ENVELOPE>"
    )


def _service(xml: str) -> TallyService:
    return TallyService(
        TallyClient(
            base_url="http://tally.test:9000",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text=xml)),
        )
    )


def _company(db: Session) -> Company:
    company = Company(name="Mapping Co")
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def _ledger(db: Session, company: Company, name: str, group: str) -> Ledger:
    ledger = Ledger(
        company_id=company.id,
        name=name,
        parent_group=group,
        source=LedgerSource.TALLY,
        is_active=True,
    )
    db.add(ledger)
    db.commit()
    return ledger


def _books(db: Session, company: Company) -> None:
    _ledger(db, company, "RADHIKA DWCON PVT LTD", "Sundry Creditors")
    _ledger(db, company, "Rent", "Indirect Expenses")
    _ledger(db, company, "Professional Charges", "Indirect Expenses")
    _ledger(db, company, "CGST Input", "Duties & Taxes")
    _ledger(db, company, "SGST Input", "Duties & Taxes")
    _ledger(db, company, "HDFC Current Account", "Bank Accounts")


# A purchase books the expense as a debit and the creditor as a credit.
def _purchase(number: str, party: str, head: str) -> str:
    return _voucher(
        "Purchase",
        number,
        [
            (head, "-14000", "Yes"),
            ("CGST Input", "-1260", "Yes"),
            ("SGST Input", "-1260", "Yes"),
            (party, "16520", "No"),
        ],
    )


def test_party_expense_pairing_is_learned(db: Session) -> None:
    company = _company(db)
    _books(db, company)
    xml = _envelope(
        [_purchase(str(n), "RADHIKA DWCON PVT LTD", "Rent") for n in range(1, 4)]
    )

    result = sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    assert result.success
    assert result.vouchers_fetched == 3
    assert result.vouchers_used == 3
    assert result.mappings_created == 1
    mapping = db.query(AccountingMapping).one()
    rent = db.query(Ledger).filter(Ledger.name == "Rent").one()
    assert mapping.ledger_id == rent.id
    assert mapping.counterparty_name == "RADHIKA DWCON PVT LTD"
    assert mapping.voucher_type == "PURCHASE"
    # The reason has to stand on its own in the review panel.
    assert "3 of 3" in mapping.reason and "Rent" in mapping.reason


def test_tax_and_bank_lines_are_never_the_expense_head(db: Session) -> None:
    company = _company(db)
    _books(db, company)
    xml = _envelope([_purchase("1", "RADHIKA DWCON PVT LTD", "Rent")])

    sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    mapping = db.query(AccountingMapping).one()
    chosen = db.get(Ledger, mapping.ledger_id)
    assert chosen is not None and chosen.name == "Rent"


def test_party_split_across_heads_is_skipped(db: Session) -> None:
    company = _company(db)
    _books(db, company)
    xml = _envelope(
        [
            _purchase("1", "RADHIKA DWCON PVT LTD", "Rent"),
            _purchase("2", "RADHIKA DWCON PVT LTD", "Professional Charges"),
        ]
    )

    result = sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    assert result.mappings_created == 0
    assert result.mappings_skipped == 1
    assert db.query(AccountingMapping).count() == 0


def test_dominant_head_wins_over_a_one_off(db: Session) -> None:
    company = _company(db)
    _books(db, company)
    xml = _envelope(
        [_purchase(str(n), "RADHIKA DWCON PVT LTD", "Rent") for n in range(1, 5)]
        + [_purchase("9", "RADHIKA DWCON PVT LTD", "Professional Charges")]
    )

    result = sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    assert result.mappings_created == 1
    mapping = db.query(AccountingMapping).one()
    assert db.get(Ledger, mapping.ledger_id).name == "Rent"
    assert "4 of 5" in mapping.reason


def test_mapping_links_to_the_pal_vendor_when_one_matches(db: Session) -> None:
    company = _company(db)
    _books(db, company)
    vendor = Vendor(
        company_id=company.id,
        name="RADHIKA DEVCON PRIVATE LIMITED",
        creditor_ledger="RADHIKA DWCON PVT LTD",
    )
    db.add(vendor)
    db.commit()
    xml = _envelope([_purchase("1", "RADHIKA DWCON PVT LTD", "Rent")])

    sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    mapping = db.query(AccountingMapping).one()
    assert mapping.vendor_id == vendor.id
    assert mapping.match_type is AccountingMappingMatchType.VENDOR


def test_ledgers_missing_from_the_master_contribute_nothing(db: Session) -> None:
    company = _company(db)
    # Ledger master not synced, so no voucher line resolves.
    xml = _envelope([_purchase("1", "RADHIKA DWCON PVT LTD", "Rent")])

    result = sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    assert result.vouchers_fetched == 1
    assert result.vouchers_used == 0
    assert result.mappings_created == 0


def test_rerun_updates_rather_than_duplicates(db: Session) -> None:
    company = _company(db)
    _books(db, company)
    first = _envelope([_purchase("1", "RADHIKA DWCON PVT LTD", "Rent")])
    sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(first)
    )

    changed = _envelope(
        [
            _purchase(str(n), "RADHIKA DWCON PVT LTD", "Professional Charges")
            for n in range(2, 5)
        ]
    )
    result = sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(changed)
    )

    assert result.mappings_created == 0
    assert result.mappings_updated == 1
    mapping = db.query(AccountingMapping).one()
    assert db.get(Ledger, mapping.ledger_id).name == "Professional Charges"


def test_tax_tds_and_round_off_are_excluded_by_name_not_group(
    db: Session,
) -> None:
    """Real charts of accounts do not name the tax group "Duties & Taxes".

    This company files GST under "gst_mp" and TDS under "tds", and books
    Round Off to an expenses group. Treating those as expense heads is what
    made 336 of 414 real purchase vouchers unusable.
    """
    company = _company(db)
    _ledger(db, company, "Shivakumara Swamy & Associates", "Sundry Creditors")
    _ledger(db, company, "Legal & Professional Charges", "Indirect Expenses")
    _ledger(db, company, "IGST Input", "gst_mp")
    _ledger(db, company, "TDS on Professional", "tds")
    _ledger(db, company, "Round Off", "General and Administration Expenses")
    # A genuine expense whose name merely contains "tax"-like letters.
    _ledger(db, company, "Taxi Fare", "Indirect Expenses")

    xml = _envelope(
        [
            _voucher(
                "Purchase",
                "1",
                [
                    ("Legal & Professional Charges", "-35000", "Yes"),
                    ("IGST Input", "-6300", "Yes"),
                    ("TDS on Professional", "-3500", "Yes"),
                    ("Round Off", "-0.20", "Yes"),
                    ("Shivakumara Swamy & Associates", "41300", "No"),
                ],
            )
        ]
    )
    result = sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    assert result.vouchers_used == 1
    mapping = db.query(AccountingMapping).one()
    assert db.get(Ledger, mapping.ledger_id).name == "Legal & Professional Charges"


def test_a_taxi_expense_is_still_a_valid_head(db: Session) -> None:
    """"Taxi" must not be word-matched as "tax"."""
    company = _company(db)
    _ledger(db, company, "Kalash Travels", "Sundry Creditors")
    _ledger(db, company, "Taxi Fare", "Indirect Expenses")
    _ledger(db, company, "CGST Input", "gst_mp")

    xml = _envelope(
        [
            _voucher(
                "Purchase",
                "1",
                [
                    ("Taxi Fare", "-1000", "Yes"),
                    ("CGST Input", "-90", "Yes"),
                    ("Kalash Travels", "1090", "No"),
                ],
            )
        ]
    )
    sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    mapping = db.query(AccountingMapping).one()
    assert db.get(Ledger, mapping.ledger_id).name == "Taxi Fare"


def test_sales_voucher_maps_customer_to_its_income_head(db: Session) -> None:
    company = _company(db)
    _ledger(db, company, "Go Digit General Insurance Limited", "Sundry Debtors")
    _ledger(db, company, "Brokerage", "Sales Accounts")
    _ledger(db, company, "IGST 18%", "gst_mp")

    xml = _envelope(
        [
            _voucher(
                "Sales",
                "1",
                [
                    ("Go Digit General Insurance Limited", "-11800", "Yes"),
                    ("Brokerage", "10000", "No"),
                    ("IGST 18%", "1800", "No"),
                ],
            )
        ]
    )
    result = sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    assert result.vouchers_used == 1
    mapping = db.query(AccountingMapping).one()
    assert db.get(Ledger, mapping.ledger_id).name == "Brokerage"
    assert mapping.voucher_type == "SALES"


def test_manual_mapping_is_never_overwritten(db: Session) -> None:
    company = _company(db)
    _books(db, company)
    professional = db.query(Ledger).filter(
        Ledger.name == "Professional Charges"
    ).one()
    manual = AccountingMapping(
        company_id=company.id,
        source_type=AccountingMappingSourceType.INVOICE,
        match_type=AccountingMappingMatchType.COUNTERPARTY,
        counterparty_name="RADHIKA DWCON PVT LTD",
        voucher_type="PURCHASE",
        ledger_id=professional.id,
        reason="Partner confirmed this head.",
        approved_by="partner",
        is_active=True,
    )
    db.add(manual)
    db.commit()

    xml = _envelope(
        [_purchase(str(n), "RADHIKA DWCON PVT LTD", "Rent") for n in range(1, 4)]
    )
    result = sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    assert result.mappings_preserved == 1
    assert result.mappings_created == 0
    assert result.mappings_updated == 0
    # Exactly one mapping, still the partner's choice and reason.
    kept = db.query(AccountingMapping).one()
    assert kept.ledger_id == professional.id
    assert kept.reason == "Partner confirmed this head."


def test_manual_mapping_identified_only_by_vendor_is_preserved(db: Session) -> None:
    """A manual row may carry vendor_id and no party name; it still wins."""
    company = _company(db)
    _books(db, company)
    vendor = Vendor(
        company_id=company.id,
        name="RADHIKA DEVCON PRIVATE LIMITED",
        creditor_ledger="RADHIKA DWCON PVT LTD",
    )
    db.add(vendor)
    db.commit()
    professional = db.query(Ledger).filter(
        Ledger.name == "Professional Charges"
    ).one()
    db.add(
        AccountingMapping(
            company_id=company.id,
            source_type=AccountingMappingSourceType.INVOICE,
            match_type=AccountingMappingMatchType.VENDOR,
            vendor_id=vendor.id,
            voucher_type="PURCHASE",
            ledger_id=professional.id,
            reason="Partner confirmed this head.",
            approved_by="partner",
            is_active=True,
        )
    )
    db.commit()

    xml = _envelope([_purchase("1", "RADHIKA DWCON PVT LTD", "Rent")])
    result = sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    assert result.mappings_preserved == 1
    # A second active mapping here would make the suggestion AMBIGUOUS.
    assert db.query(AccountingMapping).count() == 1


def test_mappings_do_not_cross_company_boundaries(db: Session) -> None:
    own = _company(db)
    _books(db, own)
    other = Company(name="Other Mapping Co")
    db.add(other)
    db.commit()
    db.refresh(other)

    xml = _envelope([_purchase("1", "RADHIKA DWCON PVT LTD", "Rent")])
    sync_tally_accounting_mappings(
        db, own.id, "Mapping Co", actor="test", service=_service(xml)
    )

    mappings = db.query(AccountingMapping).all()
    assert len(mappings) == 1
    assert mappings[0].company_id == own.id
    assert (
        db.query(AccountingMapping)
        .filter(AccountingMapping.company_id == other.id)
        .count()
        == 0
    )


def test_other_company_ledgers_are_not_used_as_heads(db: Session) -> None:
    """The same party name in another company must not leak a ledger across."""
    own = _company(db)
    other = Company(name="Other Mapping Co")
    db.add(other)
    db.commit()
    db.refresh(other)
    # Only the OTHER company has the books; `own` has an empty master.
    _books(db, other)

    xml = _envelope([_purchase("1", "RADHIKA DWCON PVT LTD", "Rent")])
    result = sync_tally_accounting_mappings(
        db, own.id, "Mapping Co", actor="test", service=_service(xml)
    )

    assert result.vouchers_used == 0
    assert db.query(AccountingMapping).count() == 0


def test_non_purchase_vouchers_are_ignored(db: Session) -> None:
    company = _company(db)
    _books(db, company)
    xml = _envelope(
        [
            _voucher(
                "Payment",
                "1",
                [
                    ("RADHIKA DWCON PVT LTD", "-16520", "Yes"),
                    ("HDFC Current Account", "16520", "No"),
                ],
            )
        ]
    )

    result = sync_tally_accounting_mappings(
        db, company.id, "Mapping Co", actor="test", service=_service(xml)
    )

    assert result.vouchers_used == 0
    assert db.query(AccountingMapping).count() == 0

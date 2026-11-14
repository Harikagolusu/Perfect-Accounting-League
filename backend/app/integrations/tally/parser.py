"""Safe parsers for TallyPrime XML responses.

TallyPrime's XML is not always strictly valid XML 1.0: for example, primary
groups can be returned as ``&#4; Primary``. The parser replaces invalid
character references before handing the document to ElementTree, then extracts
only the fields PAL needs. It never returns raw XML to a caller.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.integrations.tally.exceptions import TallyResponseError

logger = logging.getLogger(__name__)

_INVALID_CHAR_REFERENCE = re.compile(r"&#([xX]?[0-9A-Fa-f]+);")
#: TallyPrime emits ``<UDF:USERDESCRIPTION.LIST>`` without declaring the
#: ``UDF`` namespace, which makes the document not well-formed. The prefix is
#: folded into the tag name so ElementTree can parse the response.
_UNDECLARED_PREFIX = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9_.\-]*):")


@dataclass(frozen=True)
class TallyCompany:
    """One company loaded in TallyPrime."""

    name: str


@dataclass(frozen=True)
class TallyLedger:
    """One ledger read from TallyPrime."""

    name: str
    parent: str | None = None
    #: Tally's reported closing balance magnitude (sign dropped; Tally's Dr/Cr
    #: convention is applied by the caller using the ledger's group).
    closing_balance: Decimal | None = None
    #: Party GSTIN from the ledger master (Tally's PARTYGSTIN), when registered.
    gstin: str | None = None
    #: Party PAN from the ledger master (Tally's INCOMETAXNUMBER), when present.
    pan: str | None = None


@dataclass(frozen=True)
class TallyLedgerResult:
    """Parsed ledger collection plus the company it belongs to."""

    company: str | None
    ledgers: list[TallyLedger] = field(default_factory=list)
    message: str | None = None


@dataclass(frozen=True)
class TallyVoucherEntry:
    """One ledger line inside a Tally voucher.

    ``amount`` keeps Tally's sign: positive for a credit, negative for a debit.
    ``is_deemed_positive`` is Tally's own "this ledger is debited" flag, kept
    so the caller never has to infer direction from the sign alone.
    """

    ledger_name: str
    amount: Decimal
    is_deemed_positive: bool


@dataclass(frozen=True)
class TallyInventoryLine:
    """One inventory (stock item) line inside a Tally voucher.

    ``amount`` is the line's taxable value keeping Tally's sign; ``quantity`` and
    ``unit`` come from the billed quantity (e.g. ``2.00 NOS``); ``rate`` is the
    GST percentage parsed from the line's sales ledger name (e.g. ``IGST Sales
    18%``), None when the ledger carries no rate."""

    item: str
    amount: Decimal | None
    quantity: Decimal | None
    unit: str | None
    rate: Decimal | None


@dataclass(frozen=True)
class TallyVoucher:
    """One accounting voucher read from TallyPrime."""

    date: date | None
    voucher_type: str | None
    voucher_number: str | None
    narration: str | None
    guid: str | None
    party_name: str | None = None
    entries: tuple[TallyVoucherEntry, ...] = ()
    #: Inventory lines from the voucher, when the request fetched them (used to
    #: build the HSN summary). Empty otherwise.
    inventory: tuple[TallyInventoryLine, ...] = ()


@dataclass(frozen=True)
class TallyVoucherResult:
    """Parsed voucher collection plus the company it belongs to."""

    company: str | None
    vouchers: list[TallyVoucher] = field(default_factory=list)
    message: str | None = None


def _is_valid_xml_char(code: int) -> bool:
    return (
        code in (0x09, 0x0A, 0x0D)
        or 0x20 <= code <= 0xD7FF
        or 0xE000 <= code <= 0xFFFD
        or 0x10000 <= code <= 0x10FFFF
    )


def _replace_invalid_char_reference(match: re.Match[str]) -> str:
    raw = match.group(1)
    base = 16 if raw.lower().startswith("x") else 10
    digits = raw[1:] if base == 16 else raw
    try:
        code = int(digits, base)
    except ValueError:
        return " "
    return match.group(0) if _is_valid_xml_char(code) else " "


def _sanitise_xml(text: str) -> str:
    """Make Tally's near-XML safe for ElementTree without changing values."""
    text = text.lstrip("\ufeff \t\r\n\x00")
    text = _INVALID_CHAR_REFERENCE.sub(_replace_invalid_char_reference, text)
    text = _UNDECLARED_PREFIX.sub(r"<\1\2_", text)
    return "".join(char if _is_valid_xml_char(ord(char)) else " " for char in text)


def _local_name(tag: str) -> str:
    """Return an element name without an optional XML namespace."""
    return tag.rsplit("}", 1)[-1]


def _first_text(element: ET.Element, tag: str) -> str | None:
    """Return the first non-blank descendant text for ``tag``."""
    wanted = tag.upper()
    for child in element.iter():
        if _local_name(child.tag).upper() == wanted:
            value = (child.text or "").strip()
            if value:
                return value
    return None


def _raise_for_tally_error(root: ET.Element) -> None:
    header = next(
        (element for element in root.iter() if _local_name(element.tag).upper() == "HEADER"),
        None,
    )
    if header is None:
        return
    status = next(
        (element for element in header.iter() if _local_name(element.tag).upper() == "STATUS"),
        None,
    )
    if status is None or (status.text or "").strip() != "0":
        return
    detail = (
        _first_text(root, "ERROR")
        or _first_text(root, "TALLYERROR")
        or _first_text(root, "LINEERROR")
    )
    if detail:
        logger.warning("TallyPrime error response: %s", detail)
    # The commonest, most actionable Tally error: the requested company is not
    # open in TallyPrime. Naming it is safe (no XML leaks) and saves the user
    # guessing. Any other detail stays behind the generic message.
    if detail and "svcurrentcompany" in detail.lower():
        raise TallyResponseError(
            "TallyPrime has no open company matching that name. "
            "Open the company in TallyPrime, or use a name that matches it exactly.",
            detail=detail,
        )
    raise TallyResponseError("TallyPrime reported an error.", detail=detail)


def _parse_envelope(text: str) -> ET.Element:
    if not text or not text.strip():
        raise TallyResponseError("TallyPrime returned an empty response.")
    try:
        root = ET.fromstring(_sanitise_xml(text))
    except (ET.ParseError, ValueError):
        raise TallyResponseError("TallyPrime returned malformed XML.") from None
    if _local_name(root.tag).upper() != "ENVELOPE":
        raise TallyResponseError("TallyPrime returned an unexpected response.")
    _raise_for_tally_error(root)
    return root


def parse_companies(text: str) -> list[TallyCompany]:
    """Parse a TallyPrime company collection response."""
    root = _parse_envelope(text)
    companies: list[TallyCompany] = []
    seen: set[str] = set()
    for element in root.iter():
        if _local_name(element.tag).upper() != "COMPANY":
            continue
        name = _first_text(element, "NAME") or (element.get("NAME") or "").strip()
        if not name:
            # <CMPINFO> also contains <COMPANY>0</COMPANY> counters.
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        companies.append(TallyCompany(name=name))
    return companies


def parse_ledgers(text: str, *, company: str | None = None) -> TallyLedgerResult:
    """Parse a TallyPrime ledger collection response."""
    root = _parse_envelope(text)
    resolved_company = company or _first_text(root, "SVCURRENTCOMPANY")
    ledgers: list[TallyLedger] = []
    seen: set[tuple[str, str]] = set()
    for element in root.iter():
        if _local_name(element.tag).upper() != "LEDGER":
            continue
        name = _first_text(element, "NAME") or (element.get("NAME") or "").strip()
        if not name:
            continue
        parent = (
            _first_text(element, "PARENT")
            or (element.get("PARENT") or "").strip()
            or None
        )
        key = (name.casefold(), (parent or "").casefold())
        if key in seen:
            continue
        seen.add(key)
        raw_balance = _parse_decimal(_first_text(element, "CLOSINGBALANCE"))
        # GSTIN lives in LEDGSTREGDETAILS.LIST (one per registration period); take
        # the latest by APPLICABLEFROM. PARTYGSTIN is a rarely-set legacy fallback.
        gstin: str | None = None
        best_from = ""
        for child in element:
            if _local_name(child.tag).upper() != "LEDGSTREGDETAILS.LIST":
                continue
            value = (_first_text(child, "GSTIN") or "").strip()
            applicable = _first_text(child, "APPLICABLEFROM") or ""
            if value and applicable >= best_from:
                best_from = applicable
                gstin = value
        if not gstin:
            gstin = (_first_text(element, "PARTYGSTIN") or "").strip() or None
        pan = (
            _first_text(element, "INCOMETAXNUMBER")
            or _first_text(element, "LEDGERPANNO")
            or ""
        ).strip() or None
        ledgers.append(
            TallyLedger(
                name=name,
                parent=parent,
                closing_balance=abs(raw_balance) if raw_balance is not None else None,
                gstin=gstin,
                pan=pan,
            )
        )
    return TallyLedgerResult(company=resolved_company, ledgers=ledgers)


def _parse_tally_date(value: str | None) -> date | None:
    """Parse Tally's ``YYYYMMDD`` date (or a printed ``D-Mon-YYYY``) safely."""
    text = (value or "").strip()
    if not text:
        return None
    for pattern in ("%Y%m%d", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _parse_decimal(value: str | None) -> Decimal | None:
    text = (value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


@dataclass(frozen=True)
class TallyImportResult:
    """TallyPrime's own report on one voucher import.

    Tally answers an import with counts rather than an HTTP error, so a 200
    response with `created == 0` and a LINEERROR is still a failure. The caller
    decides; this only reports what Tally said.

    `exceptions` matters as much as `errors`: a rejected voucher comes back as
    EXCEPTIONS=1 with ERRORS=0, so judging on errors alone reads a refusal as a
    success.
    """

    created: int
    altered: int
    ignored: int
    errors: int
    exceptions: int
    cancelled: int
    last_voucher_id: str | None
    message: str | None

    @property
    def accepted(self) -> bool:
        """True only when Tally stored the voucher and raised nothing."""
        if self.errors or self.exceptions or self.cancelled or self.ignored:
            return False
        return (self.created + self.altered) > 0


def _import_count(root: ET.Element, tag: str) -> int:
    raw = _first_text(root, tag)
    if raw is None:
        return 0
    try:
        return int(raw.strip())
    except ValueError:
        return 0


def parse_import_result(text: str) -> TallyImportResult:
    """Parse the response to a voucher import request.

    An import answers with a bare `<RESPONSE>` root, not the `<ENVELOPE>` the
    collection exports use, so this does not go through `_parse_envelope`.
    Observed shape from a live TallyPrime 6.x instance:

        <RESPONSE>
          <LINEERROR>Ledger 'X' does not exist!</LINEERROR>
          <CREATED>0</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED>
          <LASTVCHID>0</LASTVCHID><LASTMID>0</LASTMID><COMBINED>0</COMBINED>
          <IGNORED>0</IGNORED><ERRORS>0</ERRORS><CANCELLED>0</CANCELLED>
          <EXCEPTIONS>1</EXCEPTIONS>
        </RESPONSE>

    `ENVELOPE` is still accepted, in case a build wraps the same counts.
    """
    if not text or not text.strip():
        raise TallyResponseError("TallyPrime returned an empty response.")
    try:
        root = ET.fromstring(_sanitise_xml(text))
    except (ET.ParseError, ValueError):
        raise TallyResponseError("TallyPrime returned malformed XML.") from None
    if _local_name(root.tag).upper() not in ("RESPONSE", "ENVELOPE"):
        raise TallyResponseError("TallyPrime returned an unexpected response.")

    message = (
        _first_text(root, "LINEERROR")
        or _first_text(root, "EXCEPTION")
        or _first_text(root, "ERRORS_DESC")
        or _first_text(root, "DESC")
    )
    last_id = _first_text(root, "LASTVCHID")
    # Tally prints 0 when it stored nothing; that is not a usable reference.
    if last_id is not None and last_id.strip() in ("", "0"):
        last_id = None
    return TallyImportResult(
        created=_import_count(root, "CREATED"),
        altered=_import_count(root, "ALTERED"),
        ignored=_import_count(root, "IGNORED"),
        errors=_import_count(root, "ERRORS"),
        exceptions=_import_count(root, "EXCEPTIONS"),
        cancelled=_import_count(root, "CANCELLED"),
        last_voucher_id=last_id,
        message=message,
    )


def parse_vouchers(text: str, *, company: str | None = None) -> TallyVoucherResult:
    """Parse a TallyPrime voucher collection response.

    Only accounting ledger lines are read. Vouchers flagged as deleted in
    Tally are skipped, and a voucher with no ledger entries is dropped rather
    than returned as an empty shell.
    """
    root = _parse_envelope(text)
    resolved_company = company or _first_text(root, "SVCURRENTCOMPANY")
    vouchers: list[TallyVoucher] = []

    for element in root.iter():
        if _local_name(element.tag).upper() != "VOUCHER":
            continue

        if (_first_text(element, "ISDELETED") or "").strip().lower() == "yes":
            continue

        entries: list[TallyVoucherEntry] = []
        inventory: list[TallyInventoryLine] = []
        for child in element:
            if _local_name(child.tag).upper() == "ALLINVENTORYENTRIES.LIST":
                item = (_first_text(child, "STOCKITEMNAME") or "").strip()
                if item:
                    qty, unit = _parse_quantity(_first_text(child, "BILLEDQTY")
                                                or _first_text(child, "ACTUALQTY"))
                    inventory.append(TallyInventoryLine(
                        item=item,
                        amount=_parse_decimal(_first_text(child, "AMOUNT")),
                        quantity=qty,
                        unit=unit,
                        rate=_rate_from_ledger(child),
                    ))
                continue
            if _local_name(child.tag).upper() != "ALLLEDGERENTRIES.LIST":
                continue
            ledger_name = (_first_text(child, "LEDGERNAME") or "").strip()
            amount = _parse_decimal(_first_text(child, "AMOUNT"))
            if not ledger_name or amount is None:
                continue
            deemed = (
                (_first_text(child, "ISDEEMEDPOSITIVE") or "").strip().lower()
                == "yes"
            )
            entries.append(
                TallyVoucherEntry(
                    ledger_name=ledger_name,
                    amount=amount,
                    is_deemed_positive=deemed,
                )
            )

        if not entries:
            continue

        vouchers.append(
            TallyVoucher(
                date=_parse_tally_date(_first_text(element, "DATE")),
                voucher_type=(_first_text(element, "VOUCHERTYPENAME") or None),
                voucher_number=(_first_text(element, "VOUCHERNUMBER") or None),
                narration=(_first_text(element, "NARRATION") or None),
                guid=(_first_text(element, "GUID") or element.get("REMOTEID")),
                party_name=(_first_text(element, "PARTYLEDGERNAME") or None),
                entries=tuple(entries),
                inventory=tuple(inventory),
            )
        )

    return TallyVoucherResult(company=resolved_company, vouchers=vouchers)


_HSN_CODE = re.compile(r"\d{4,8}")
_QUANTITY = re.compile(r"(-?[\d,]+(?:\.\d+)?)\s*([A-Za-z].*)?")
_RATE_IN_NAME = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _rate_from_ledger(inventory: ET.Element) -> Decimal | None:
    """GST percentage from an inventory line's sales ledger name, e.g. the 18 in
    'IGST Sales 18%'. None when no ledger name carries a rate."""
    for alloc in inventory.iter():
        if _local_name(alloc.tag).upper() != "ACCOUNTINGALLOCATIONS.LIST":
            continue
        match = _RATE_IN_NAME.search(_first_text(alloc, "LEDGERNAME") or "")
        if match:
            return _parse_decimal(match.group(1))
    return None


def _parse_quantity(text: str | None) -> tuple[Decimal | None, str | None]:
    """Split Tally's '2.00 NOS' into (Decimal quantity, unit)."""
    if not text:
        return None, None
    match = _QUANTITY.search(text.strip())
    if not match:
        return None, None
    qty = _parse_decimal(match.group(1))
    unit = (match.group(2) or "").strip() or None
    return qty, unit


def parse_stock_items(text: str) -> dict[str, str]:
    """Map stock item name (casefolded) -> HSN/SAC code from a StockItem export.

    HSN lives under ``HSNDETAILS.LIST``; an item can carry several, dated by
    ``APPLICABLEFROM``, so the latest valid code wins. Only numeric codes are
    kept (Tally returns 'Not Found' sentinels otherwise). Keyed on the item name
    as the voucher's STOCKITEMNAME gives it, so the two join after strip +
    casefold.
    """
    root = _parse_envelope(text)
    mapping: dict[str, str] = {}
    for element in root.iter():
        if _local_name(element.tag).upper() != "STOCKITEM":
            continue
        name = (element.get("NAME") or "").strip()
        if not name:
            continue
        hsn = _latest_dated(element, "HSNDETAILS.LIST", _hsn_code)
        if hsn:
            mapping[name.casefold()] = hsn
    return mapping


def _latest_dated(stockitem: ET.Element, list_tag: str, extract):
    """Return ``extract(detail)`` for the latest-dated ``list_tag`` that yields a
    non-None value, using ``APPLICABLEFROM`` to order the dated masters."""
    best = None
    best_from: date | None = None
    for detail in stockitem.iter():
        if _local_name(detail.tag).upper() != list_tag:
            continue
        value = extract(detail)
        if value is None:
            continue
        applicable = _parse_tally_date(_first_text(detail, "APPLICABLEFROM"))
        if best is None or (
            applicable is not None and (best_from is None or applicable >= best_from)
        ):
            best, best_from = value, applicable
    return best


def _hsn_code(detail: ET.Element) -> str | None:
    """A numeric HSN/SAC code inside one HSNDETAILS.LIST, or None."""
    code = (_first_text(detail, "HSNCODE") or "").strip()
    return code if _HSN_CODE.fullmatch(code) else None


def parse_voucher_references(text: str) -> dict[str, str]:
    """Map voucher GUID -> REFERENCE (Supplier Invoice No.) from a Voucher Register.

    Only vouchers that carry a non-empty REFERENCE are included. The GUID is the
    stable key shared with the voucher collection export.
    """
    root = _parse_envelope(text)
    references: dict[str, str] = {}
    for element in root.iter():
        if _local_name(element.tag).upper() != "VOUCHER":
            continue
        guid = (_first_text(element, "GUID") or element.get("REMOTEID") or "").strip()
        reference = (_first_text(element, "REFERENCE") or "").strip()
        if guid and reference:
            references[guid] = reference
    return references

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
class TallyVoucher:
    """One accounting voucher read from TallyPrime."""

    date: date | None
    voucher_type: str | None
    voucher_number: str | None
    narration: str | None
    guid: str | None
    entries: tuple[TallyVoucherEntry, ...] = ()


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
        ledgers.append(TallyLedger(name=name, parent=parent))
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
        for child in element:
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
                entries=tuple(entries),
            )
        )

    return TallyVoucherResult(company=resolved_company, vouchers=vouchers)

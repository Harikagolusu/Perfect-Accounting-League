"""Deterministic regex candidates for invoice header fields.

Regexes are good at *finding* printed patterns and bad at *deciding* what they
mean. This module therefore produces candidates only:

* it never fills an extraction result;
* it never overrides or reconciles a model value;
* it never calculates tax, totals or dates.

The candidates are attached to the text prompt so the model can verify them
against the source. A value that is not printed is still absent: the model is
told to ignore unsupported candidates and return null.

Everything here is pure and offline, which also makes the behaviour easy to
test without a provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.text_quality import PageText

#: The header fields the prompt asks for. Kept in one place so prompt rendering
#: and tests cannot drift apart.
CANDIDATE_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "vendor_name",
    "vendor_gstin",
    "vendor_pan",
    "buyer_gstin",
    "po_number",
    "currency",
    "taxable_amount",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
    "cess_amount",
    "round_off",
    "total_amount",
)

#: One candidate value with just enough context to verify it.
@dataclass(frozen=True)
class FieldCandidate:
    field: str
    value: str
    snippet: str
    page: int


@dataclass(frozen=True)
class ExtractionCandidates:
    """Candidates grouped by field, in document order."""

    by_field: dict[str, tuple[FieldCandidate, ...]] = field(default_factory=dict)

    def get(self, field_name: str) -> tuple[FieldCandidate, ...]:
        return self.by_field.get(field_name, ())

    @property
    def is_empty(self) -> bool:
        return not any(self.by_field.values())


#: Deliberately strict: 2-digit state code, 5 letters, 4 digits, letter,
#: entity digit, "Z", checksum. This rejects most random identifiers.
_GSTIN_RE = re.compile(
    r"(?<![A-Z0-9])(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])(?![A-Z0-9])",
    re.IGNORECASE,
)
_PAN_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{5}\d{4}[A-Z])(?![A-Z0-9])", re.IGNORECASE)
_DATE_RE = re.compile(
    r"(?<!\d)("
    r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
    r"|\d{1,2}[\s./-]+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"[a-z]*\.?[\s./-]+\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r")(?!\d)",
    re.IGNORECASE,
)
#: An amount token. Indian grouping (1,18,000.00) and Western grouping both
#: match; a trailing percent sign is filtered out separately so tax rates do not
#: become amounts.
_AMOUNT_RE = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?")
_CURRENCY_RE = re.compile(
    r"(?<![A-Z])("
    r"INR|USD|EUR|GBP|AED|SAR|QAR|KWD|OMR|BHD|JPY|SGD|HKD|CAD|AUD|CHF|CNY|KRW|"
    r"THB|MYR|IDR|PHP|VND|BDT|LKR|NPR|PKR"
    r")(?![A-Z])"
)
_INVOICE_NUMBER_RE = re.compile(
    r"(?:tax\s+invoice|invoice|inv|bill|tax\s+bill)(?![A-Za-z])\s*"
    r"(?:no\.?|number|num|#)\s*[:#\-]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9/\-_.]{0,63})",
    re.IGNORECASE,
)
_PO_NUMBER_RE = re.compile(
    r"(?:purchase\s+order|p\.?\s*o\.?)(?![A-Za-z])\s*"
    r"(?:no\.?|number|num|#)?\s*[:#\-]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9/\-_.]{0,63})",
    re.IGNORECASE,
)

_VENDOR_LABELS = (
    "vendor",
    "supplier",
    "seller",
    "bill from",
    "billed by",
    "sold by",
)
_BUYER_LABELS = (
    "buyer",
    "customer",
    "bill to",
    "ship to",
    "billed to",
    "recipient",
)

_RESERVED_NUMBER_VALUES = {
    "date",
    "no",
    "number",
    "invoice",
    "bill",
    "gstin",
    "pan",
    "total",
    "amount",
}

_PAGE_MAX_SNIPPET = 180
_MAX_CANDIDATES_PER_FIELD = 6


class RegexExtractor:
    """Find printed candidate values in already-extracted text."""

    def extract(
        self, pages: tuple[PageText, ...] | list[PageText]
    ) -> ExtractionCandidates:
        lines = _lines(pages)
        collected: dict[str, list[FieldCandidate]] = {
            name: [] for name in CANDIDATE_FIELDS
        }

        self._identifiers(lines, collected)
        self._dates(lines, collected)
        self._amounts(lines, collected)
        self._currency(lines, collected)
        self._vendor_name(lines, collected)
        self._derive_vendor_pan(collected)

        return ExtractionCandidates(
            by_field={
                name: tuple(_dedupe(values))
                for name, values in collected.items()
                if values
            }
        )

    def _identifiers(
        self,
        lines: list[tuple[int, str]],
        collected: dict[str, list[FieldCandidate]],
    ) -> None:
        gstin_spans: list[tuple[int, int, int]] = []
        unlabeled_gstins: list[FieldCandidate] = []

        for page, line in lines:
            for match in _GSTIN_RE.finditer(line):
                value = match.group(1).upper()
                gstin_spans.append((page, match.start(1), match.end(1)))
                candidate = _candidate("buyer_gstin", value, page, line)
                # Prefer the label immediately before this identifier, so a
                # line holding both parties does not assign both to one side.
                context = line[max(0, match.start() - 40) : match.start()]
                lowered = (context or line).casefold()
                if _contains_any(lowered, _BUYER_LABELS):
                    collected["buyer_gstin"].append(candidate)
                elif _contains_any(lowered, _VENDOR_LABELS):
                    collected["vendor_gstin"].append(
                        _candidate("vendor_gstin", value, page, line)
                    )
                else:
                    unlabeled_gstins.append(candidate)

            for match in _INVOICE_NUMBER_RE.finditer(line):
                value = match.group(1).strip(" .,;:-")
                if _plausible_identifier(value):
                    collected["invoice_number"].append(
                        _candidate("invoice_number", value, page, line)
                    )

            for match in _PO_NUMBER_RE.finditer(line):
                value = match.group(1).strip(" .,;:-")
                if _plausible_identifier(value):
                    collected["po_number"].append(
                        _candidate("po_number", value, page, line)
                    )

        # Unlabelled GSTINs are common in a two-column header. The seller is
        # conventionally printed first; still candidates only, never assigned.
        if unlabeled_gstins:
            collected["vendor_gstin"].append(
                _candidate(
                    "vendor_gstin",
                    unlabeled_gstins[0].value,
                    unlabeled_gstins[0].page,
                    unlabeled_gstins[0].snippet,
                )
            )
            for extra in unlabeled_gstins[1:]:
                collected["buyer_gstin"].append(
                    _candidate(
                        "buyer_gstin", extra.value, extra.page, extra.snippet
                    )
                )

        # Explicit PAN lines, skipping a PAN embedded inside a GSTIN span.
        for page, line in lines:
            for match in _PAN_RE.finditer(line):
                if any(
                    page == span_page
                    and start <= match.start(1)
                    and match.end(1) <= end
                    for span_page, start, end in gstin_spans
                ):
                    continue
                lowered = line.casefold()
                if "pan" in lowered or _contains_any(lowered, _VENDOR_LABELS):
                    collected["vendor_pan"].append(
                        _candidate("vendor_pan", match.group(1).upper(), page, line)
                    )

    def _dates(
        self,
        lines: list[tuple[int, str]],
        collected: dict[str, list[FieldCandidate]],
    ) -> None:
        for page, line in lines:
            matches = list(_DATE_RE.finditer(line))
            if not matches:
                continue
            lowered = line.casefold()
            value = matches[-1].group(1)
            if _contains_any(lowered, ("due date", "payment due", "due on")):
                collected["due_date"].append(
                    _candidate("due_date", value, page, line)
                )
            elif _contains_any(
                lowered, ("invoice date", "bill date", "date of invoice", "dated")
            ):
                collected["invoice_date"].append(
                    _candidate("invoice_date", value, page, line)
                )
            elif re.search(r"\bdate\b", lowered):
                collected["invoice_date"].append(
                    _candidate("invoice_date", value, page, line)
                )
            # An unlabelled date is weak evidence; only labelled header lines
            # contribute, so item-table dates do not masquerade as invoice dates.

    def _amounts(
        self,
        lines: list[tuple[int, str]],
        collected: dict[str, list[FieldCandidate]],
    ) -> None:
        for page, line in lines:
            field_name = _amount_field(line)
            if field_name is None:
                continue
            amounts = [
                (match.group(0), match.end())
                for match in _AMOUNT_RE.finditer(line)
                if not _is_percentage(line, match.start(), match.end())
                and _looks_monetary(match.group(0))
            ]
            if not amounts:
                continue
            value, _ = amounts[-1]
            collected[field_name].append(
                _candidate(field_name, value.replace(" ", ""), page, line)
            )

    def _currency(
        self,
        lines: list[tuple[int, str]],
        collected: dict[str, list[FieldCandidate]],
    ) -> None:
        for page, line in lines:
            for match in _CURRENCY_RE.finditer(line):
                collected["currency"].append(
                    _candidate("currency", match.group(1).upper(), page, line)
                )
                return
            if "₹" in line or re.search(r"\bRs\.?\b", line, re.IGNORECASE):
                collected["currency"].append(
                    _candidate("currency", "INR", page, line)
                )
                return

    def _vendor_name(
        self,
        lines: list[tuple[int, str]],
        collected: dict[str, list[FieldCandidate]],
    ) -> None:
        for page, line in lines:
            lowered = line.casefold().strip()
            for label in _VENDOR_LABELS:
                if not lowered.startswith(label):
                    continue
                remainder = lowered[len(label) :].lstrip(" :-")
                original = line.strip()[len(label) :].lstrip(" :-")
                if _plausible_vendor_name(remainder, original):
                    collected["vendor_name"].append(
                        _candidate("vendor_name", original.strip(), page, line)
                    )
                break

        if collected["vendor_name"]:
            return

        # Header fallback: the first letter-bearing line that is not an
        # identifier, address fragment, title or table heading.
        for page, line in lines:
            stripped = line.strip()
            if not (3 <= len(stripped) <= 80):
                continue
            if not re.search(r"[A-Za-z]", stripped):
                continue
            if not _plausible_vendor_name(stripped.casefold(), stripped):
                continue
            if re.match(r"^(tax\s+)?invoice\b", stripped, re.IGNORECASE):
                continue
            collected["vendor_name"].append(
                _candidate("vendor_name", stripped, page, line)
            )
            break

    def _derive_vendor_pan(
        self, collected: dict[str, list[FieldCandidate]]
    ) -> None:
        """Add the PAN that a printed GSTIN already contains.

        This is a deterministic reading of the printed identifier, not an
        inferred taxpayer identity: characters 3-12 of an Indian GSTIN are the
        PAN. It remains a candidate and the model may still return null.
        """
        if collected["vendor_pan"] or not collected["vendor_gstin"]:
            return
        gstin = collected["vendor_gstin"][0]
        if len(gstin.value) == 15:
            collected["vendor_pan"].append(
                _candidate(
                    "vendor_pan",
                    gstin.value[2:12],
                    gstin.page,
                    gstin.snippet,
                )
            )


def render_candidates_for_prompt(candidates: ExtractionCandidates) -> str:
    """Render candidates as a compact block for the text prompt."""
    lines: list[str] = [
        "Deterministic candidates found in the document text.",
        "They are hints only. Verify each one against the text. Ignore any",
        "candidate that is not clearly printed, and never use a candidate to",
        "fill a field that the document does not show.",
        "",
    ]
    for name in CANDIDATE_FIELDS:
        values = candidates.get(name)
        if not values:
            lines.append(f"{name}: none found")
            continue
        rendered = "; ".join(
            f"{candidate.value!r} (page {candidate.page}: {candidate.snippet!r})"
            for candidate in values
        )
        lines.append(f"{name}: {rendered}")
    return "\n".join(lines)


def _lines(
    pages: tuple[PageText, ...] | list[PageText],
) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for page in pages:
        for line in page.text.splitlines():
            if line.strip():
                lines.append((page.page_number, line.strip()))
    return lines


def _candidate(
    field_name: str, value: str, page: int, line: str
) -> FieldCandidate:
    return FieldCandidate(
        field=field_name,
        value=value.strip(),
        snippet=line.strip()[:_PAGE_MAX_SNIPPET],
        page=page,
    )


def _dedupe(values: list[FieldCandidate]) -> list[FieldCandidate]:
    seen: set[tuple[str, str]] = set()
    result: list[FieldCandidate] = []
    for candidate in values:
        key = (candidate.field, candidate.value.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
        if len(result) >= _MAX_CANDIDATES_PER_FIELD:
            break
    return result


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _plausible_identifier(value: str) -> bool:
    if not value or value.casefold() in _RESERVED_NUMBER_VALUES:
        return False
    # A lone word such as "DATE" is a label, not an identifier.
    return any(character.isdigit() for character in value) or len(value) >= 4


def _plausible_vendor_name(lowered: str, original: str) -> bool:
    if len(original.strip()) < 3 or len(original.strip()) > 120:
        return False
    if not re.search(r"[A-Za-z]{3}", original):
        return False
    blocked = (
        "gstin",
        "pan ",
        "pan:",
        "invoice",
        "bill no",
        "date",
        "phone",
        "email",
        "www.",
        "address",
        "terms",
        "description",
        "hsn",
        "sac",
        "qty",
        "rate",
        "amount",
        "total",
        "subtotal",
        "page ",
        "purchase order",
        "delivery note",
        "credit note",
        "debit note",
        "statement",
    )
    return not any(token in lowered for token in blocked)


def _amount_field(line: str) -> str | None:
    lowered = line.casefold()
    checks: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("cgst_amount", ("cgst", "central tax")),
        ("sgst_amount", ("sgst", "state tax", "utgst")),
        ("igst_amount", ("igst", "integrated tax")),
        ("cess_amount", ("cess",)),
        ("round_off", ("round off", "round-off", "rounding")),
        (
            "taxable_amount",
            ("taxable", "sub total", "subtotal", "sub-total", "base amount"),
        ),
        (
            "total_amount",
            ("grand total", "invoice total", "total amount", "amount due", "total"),
        ),
    )
    for field_name, tokens in checks:
        if _contains_any(lowered, tokens):
            return field_name
    return None


def _is_percentage(line: str, start: int, end: int) -> bool:
    after = line[end:].lstrip()
    return after.startswith("%") or line[max(0, start - 1) : start] == "%"


def _looks_monetary(value: str) -> bool:
    """Reject bare small integers (item counts, quantities, HSN codes)."""
    if "." in value or "," in value:
        return True
    try:
        return abs(float(value)) >= 100
    except ValueError:  # pragma: no cover - regex guarantees numeric input
        return False


__all__ = [
    "CANDIDATE_FIELDS",
    "ExtractionCandidates",
    "FieldCandidate",
    "RegexExtractor",
    "render_candidates_for_prompt",
]

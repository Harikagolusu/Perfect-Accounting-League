"""AI-assisted ledger selection, used only as a last resort.

The deterministic chain in `app.services.ledger_suggestion` is always tried
first: approved mappings, approved voucher history, then configured vendor and
company ledgers. When none of those yield a ledger the reviewer would otherwise
face an empty dropdown, so this module asks the configured AI provider to pick
one.

Two rules keep the result honest:

* the provider chooses from the company's existing active ledgers by exact name
  and nothing else, so no ledger master record is ever invented; and
* the choice is returned with `source="AI_SUGGESTION"` and always carries a
  review exception, so the voucher lands in `NEEDS_REVIEW` and a person
  confirms or changes it before approval.

A missing API key, an unparseable answer or a name that is not in the master
list all resolve to "no suggestion" rather than a guess.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.invoice import Invoice
from app.models.invoice_line_item import InvoiceLineItem
from app.models.ledger import Ledger
from app.schemas.voucher import LedgerSuggestion
from app.services.ai import AIProviderError, get_ai_provider
from app.services.ledger import is_posting_head_candidate

logger = logging.getLogger(__name__)

#: Upper bound on ledger names sent to the provider. A company with more active
#: ledgers than this gets no AI suggestion rather than a silently truncated
#: choice.
# shortcut: companies with >300 active ledgers get no AI suggestion, upgrade to
# a retrieval step that pre-filters candidates by parent group if that happens.
_MAX_CANDIDATES = 300

#: Upper bound on invoice line descriptions quoted into the prompt.
_MAX_ITEMS = 20

_SYSTEM_PROMPT = (
    "You are an Indian accounting assistant. You map a purchase or sales "
    "invoice to exactly one ledger from a company's Tally ledger master. "
    "You never invent a ledger name: you may only return a name that appears "
    "verbatim in the supplied list, or null when none of them fits."
)


def _candidate_lines(ledgers: list[Ledger]) -> str:
    rows = []
    for ledger in ledgers:
        detail = " · ".join(
            part
            for part in (ledger.parent_group, ledger.ledger_type)
            if part and part.strip()
        )
        rows.append(f"- {ledger.name}" + (f" ({detail})" if detail else ""))
    return "\n".join(rows)


def _invoice_context(
    invoice: Invoice, items: list[InvoiceLineItem], vendor_name: str | None
) -> str:
    parts = [f"Vendor/customer: {vendor_name or invoice.vendor_name or 'unknown'}"]
    if invoice.invoice_number:
        parts.append(f"Invoice number: {invoice.invoice_number}")
    if invoice.taxable_amount is not None:
        parts.append(f"Taxable amount: {invoice.taxable_amount}")
    if items:
        parts.append("Line items:")
        for item in items[:_MAX_ITEMS]:
            bits = [item.description.strip()]
            if item.hsn_sac:
                bits.append(f"HSN/SAC {item.hsn_sac}")
            if item.taxable_amount is not None:
                bits.append(str(item.taxable_amount))
            parts.append("  - " + " · ".join(bits))
    return "\n".join(parts)


def _resolve_choice(name: str, candidates: list[Ledger]) -> Ledger | None:
    """Map the model's answer onto one of the candidates, or None.

    The candidate list shows each ledger's group in brackets, so the model
    sometimes echoes "Office Rent_Mumbai (Rent Expenses)" back as the name.
    A trailing bracket and separator differences are tolerated; anything that
    does not land on a supplied candidate is refused rather than created.
    """
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    if not cleaned:
        return None
    exact = {ledger.name.strip().lower(): ledger for ledger in candidates}
    hit = exact.get(cleaned.lower())
    if hit is not None:
        return hit
    squashed = {
        re.sub(r"[^a-z0-9]+", "", ledger.name.lower()): ledger
        for ledger in candidates
    }
    return squashed.get(re.sub(r"[^a-z0-9]+", "", cleaned.lower()))


def _parse_choice(text: str) -> tuple[str | None, str]:
    """Return the chosen ledger name and the model's stated reason."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if not cleaned:
        return None, ""
    try:
        data = json.loads(cleaned, parse_float=Decimal)
    except (json.JSONDecodeError, ValueError):
        return None, ""
    if not isinstance(data, dict):
        return None, ""
    name = data.get("ledger_name")
    reason = data.get("reason")
    return (
        name.strip() if isinstance(name, str) and name.strip() else None,
        reason.strip() if isinstance(reason, str) else "",
    )


def suggest_ledger_with_ai(
    db: Session,
    company_id: uuid.UUID,
    *,
    purpose: str,
    invoice: Invoice,
    items: list[InvoiceLineItem],
    vendor_name: str | None,
    ledgers: list[Ledger],
) -> LedgerSuggestion | None:
    """Pick one active ledger for `purpose`, or return None.

    `ledgers` must already be the company's active ledger master. The returned
    suggestion always resolves to a real ledger id.
    """
    # A real chart of accounts is mostly party ledgers: of 682 active ledgers
    # on a live company, ~440 are creditors/debtors that can never be the head.
    # Narrowing to plausible heads keeps the choice inside the prompt budget
    # and stops a creditor being offered as an expense head.
    candidates = [ledger for ledger in ledgers if is_posting_head_candidate(ledger)]
    if not candidates or len(candidates) > _MAX_CANDIDATES:
        return None

    provider = get_ai_provider()
    if not provider.is_configured():
        return None

    prompt = (
        f"Pick the single best ledger for the {purpose.lower()} side of this "
        "invoice.\n\n"
        f"{_invoice_context(invoice, items, vendor_name)}\n\n"
        "Available ledgers. Each line is '- NAME (group)'; the group is context "
        "only, never part of the name:\n"
        f"{_candidate_lines(candidates)}\n\n"
        'Reply with JSON only: {"ledger_name": "<one NAME copied exactly, '
        'without its (group), or null>", "reason": "<one short sentence>"}'
    )

    try:
        response = provider.generate_from_text(
            prompt,
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.0,
            json_mode=True,
        )
    except AIProviderError as exc:
        # The deterministic chain already recorded why no ledger was found; an
        # AI outage must not turn into a failed voucher creation.
        logger.info("AI ledger suggestion unavailable: %s", exc.code)
        return None

    name, reason = _parse_choice(response.text)
    if not name:
        return None

    # The provider is told to copy a name verbatim; anything that does not land
    # on a supplied candidate is discarded, never created as a new ledger.
    ledger = _resolve_choice(name, candidates)
    if ledger is None:
        logger.info("AI ledger suggestion rejected, not a candidate: %r", name)
        return None

    return LedgerSuggestion(
        purpose=purpose,
        ledger_id=ledger.id,
        ledger_name=ledger.name,
        source="AI_SUGGESTION",
        reason=reason or "Selected by AI from the company ledger master.",
        code="AI_SUGGESTED_LEDGER",
    )


__all__ = ["suggest_ledger_with_ai"]

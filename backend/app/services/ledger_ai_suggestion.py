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
from collections.abc import Callable
from decimal import Decimal
from difflib import SequenceMatcher
from typing import TypeVar

from sqlalchemy.orm import Session

from app.models.bank import BankTransaction
from app.models.invoice import Invoice
from app.models.invoice_line_item import InvoiceLineItem
from app.models.ledger import Ledger
from app.schemas.voucher import LedgerSuggestion
from app.services.ai import AIProviderError, get_ai_provider, get_reasoning_provider
from app.services.ledger import _comparison_key, is_posting_head_candidate

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Upper bound on ledger names sent to the provider. A company with more active
#: ledgers than this gets no AI suggestion rather than a silently truncated
#: choice.
# shortcut: companies with >300 active ledgers get no AI suggestion, upgrade to
# a retrieval step that pre-filters candidates by parent group if that happens.
_MAX_CANDIDATES = 300

#: Upper bound on invoice line descriptions quoted into the prompt.
_MAX_ITEMS = 20

#: Group-name fragments that mark a plausible head, used to narrow a chart of
#: accounts too large for one prompt.
_HEAD_GROUP_HINTS = {
    "EXPENSE": ("expens", "purchase", "asset", "cost"),
    "SALES": ("sales", "income", "revenue"),
}

#: Party candidates sent to the provider: the names closest to the vendor's.
_MAX_PARTY_CANDIDATES = 25

_SYSTEM_PROMPT = (
    "You are an Indian accounting assistant. You map a purchase or sales "
    "invoice to exactly one ledger from a company's Tally ledger master. "
    "You never invent a ledger name: you may only return a name that appears "
    "verbatim in the supplied list. Every invoice must be booked, so always "
    "return the closest ledger; a reviewer confirms it before posting."
)

_PARTY_SYSTEM_PROMPT = (
    "You are an Indian accounting assistant. You find the Tally party ledger "
    "for a vendor or customer whose printed name may be abbreviated, "
    "misspelt or missing its legal suffix. You may only return a name that "
    "appears verbatim in the supplied list, or null when no ledger is clearly "
    "the same business. Never pick a different business with a similar name."
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


def _ask_for_ledger(
    prompt: str, system_prompt: str, candidates: list[Ledger]
) -> tuple[Ledger | None, str]:
    # The provider is told to copy a name verbatim; anything that does not
    # land on a supplied candidate is discarded, never created.
    return _ask(prompt, system_prompt, lambda name: _resolve_choice(name, candidates))


def _ask(
    prompt: str,
    system_prompt: str,
    resolve: Callable[[str], T | None],
    *,
    null_is_final: bool = False,
) -> tuple[T | None, str]:
    """Ask the chat model, then the reasoning model when the first gives no
    usable answer. Outages and unconfigured providers resolve to None.

    `null_is_final` accepts an explicit null as the answer ("no TDS") instead
    of asking the slower reasoning model again.
    """
    for provider in (get_ai_provider(), get_reasoning_provider()):
        if not provider.is_configured():
            continue
        try:
            response = provider.generate_from_text(
                prompt,
                system_prompt=system_prompt,
                temperature=0.0,
                json_mode=True,
            )
        except AIProviderError as exc:
            logger.info("AI ledger suggestion unavailable: %s", exc.code)
            continue
        name, reason = _parse_choice(response.text)
        if not name:
            if null_is_final and response.text.strip():
                return None, reason
            continue
        choice = resolve(name)
        if choice is not None:
            return choice, reason
        logger.info("AI suggestion rejected, not a candidate: %r", name)
    return None, ""


_TDS_SYSTEM_PROMPT = (
    "You are an Indian TDS assistant. Given a purchase invoice, you name the "
    "income-tax TDS section the buyer must deduct under, or null when the "
    "invoice is for goods or otherwise attracts no TDS. Sections: 194C "
    "(contract work, transport, advertising, labour, printing, catering), 194J "
    "(professional or technical services, consultancy, audit, legal, software "
    "services), 194I (rent of land, building, machinery or equipment), 194H "
    "(commission or brokerage)."
)

_TDS_AI_SECTIONS = ("194C", "194J", "194I", "194H")


def suggest_tds_section_with_ai(
    *,
    invoice: Invoice,
    items: list[InvoiceLineItem],
    vendor_name: str | None,
    expense_ledger: str | None,
) -> tuple[str | None, str]:
    """(section, reason) for a purchase invoice, or (None, "").

    194Q is never offered: it turns on the buyer's turnover and yearly
    purchases from the seller, which one invoice cannot show.
    """
    prompt = (
        f"{_invoice_context(invoice, items, vendor_name)}\n"
        f"Expense ledger: {expense_ledger or 'not chosen yet'}\n\n"
        'Reply with JSON only: {"ledger_name": "<194C, 194J, 194I, 194H or '
        'null>", "reason": "<one short sentence>"}'
    )
    return _ask(
        prompt,
        _TDS_SYSTEM_PROMPT,
        lambda name: name.strip().upper() if name.strip().upper() in _TDS_AI_SECTIONS else None,
        null_is_final=True,
    )


def _narrow_heads(candidates: list[Ledger], purpose: str) -> list[Ledger]:
    """Fit a large chart of accounts into one prompt by keeping head groups."""
    if len(candidates) <= _MAX_CANDIDATES:
        return candidates
    hints = _HEAD_GROUP_HINTS.get(purpose, ())
    return [
        ledger
        for ledger in candidates
        if any(hint in (ledger.parent_group or "").lower() for hint in hints)
    ]


def suggest_party_ledger_with_ai(
    *,
    party_name: str | None,
    party_group: str,
    ledgers: list[Ledger],
) -> LedgerSuggestion | None:
    """Match a printed party name to its Tally ledger, or return None.

    Used after exact and suffix-free matching fail. Only ledgers in the party
    group are offered, closest names first, so the answer is always a real
    party ledger.
    """
    if not party_name or not party_name.strip():
        return None
    target = _comparison_key(party_name)
    group = party_group.lower()
    candidates = sorted(
        (
            ledger
            for ledger in ledgers
            if (ledger.parent_group or "").strip().lower() == group
        ),
        key=lambda ledger: SequenceMatcher(
            None, target, _comparison_key(ledger.name)
        ).ratio(),
        reverse=True,
    )[:_MAX_PARTY_CANDIDATES]
    if not candidates:
        return None
    prompt = (
        f"Printed party name on the invoice: {party_name.strip()}\n\n"
        f"Candidate {party_group} ledgers:\n"
        + "\n".join(f"- {ledger.name}" for ledger in candidates)
        + '\n\nReply with JSON only: {"ledger_name": "<one NAME copied exactly, '
        'or null>", "reason": "<one short sentence>"}'
    )
    ledger, reason = _ask_for_ledger(prompt, _PARTY_SYSTEM_PROMPT, candidates)
    if ledger is None:
        return None
    return LedgerSuggestion(
        purpose="PARTY",
        ledger_id=ledger.id,
        ledger_name=ledger.name,
        source="AI_SUGGESTION",
        reason=reason or "Matched by AI to the party's Tally ledger.",
        code="AI_SUGGESTED_LEDGER",
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
    candidates = _narrow_heads(
        [ledger for ledger in ledgers if is_posting_head_candidate(ledger)], purpose
    )
    # shortcut: a chart with >300 heads even after group narrowing gets no AI
    # pick; add a retrieval pre-filter on line descriptions if one appears.
    if not candidates or len(candidates) > _MAX_CANDIDATES:
        return None

    prompt = (
        f"Pick the single best ledger for the {purpose.lower()} side of this "
        "invoice. Always pick one: choose the closest head even when none is "
        "an exact fit.\n\n"
        f"{_invoice_context(invoice, items, vendor_name)}\n\n"
        "Available ledgers. Each line is '- NAME (group)'; the group is context "
        "only, never part of the name:\n"
        f"{_candidate_lines(candidates)}\n\n"
        'Reply with JSON only: {"ledger_name": "<one NAME copied exactly, '
        'without its (group)>", "reason": "<one short sentence>"}'
    )
    # An AI outage leaves the line for the reviewer; the deterministic chain
    # already recorded why no ledger was found.
    ledger, reason = _ask_for_ledger(prompt, _SYSTEM_PROMPT, candidates)
    if ledger is None:
        return None

    return LedgerSuggestion(
        purpose=purpose,
        ledger_id=ledger.id,
        ledger_name=ledger.name,
        source="AI_SUGGESTION",
        reason=reason or "Selected by AI from the company ledger master.",
        code="AI_SUGGESTED_LEDGER",
    )


_BANK_SYSTEM_PROMPT = (
    "You are an Indian accounting assistant. You map one bank transaction to "
    "exactly one counterparty ledger from a company's Tally ledger master. You "
    "never invent a ledger name: you may only return a name that appears "
    "verbatim in the supplied list, or null when none of them fits."
)


def _bank_context(transaction: BankTransaction) -> str:
    is_credit = transaction.credit_amount > transaction.debit_amount
    amount = transaction.credit_amount if is_credit else transaction.debit_amount
    direction = (
        "Money received into the bank account"
        if is_credit
        else "Money paid out of the bank account"
    )
    parts = [direction, f"Amount: {amount}"]
    if transaction.description:
        parts.append(f"Narration: {transaction.description}")
    if transaction.reference_number:
        parts.append(f"Reference/UTR: {transaction.reference_number}")
    if transaction.counterparty_name:
        parts.append(f"Counterparty: {transaction.counterparty_name}")
    return "\n".join(parts)


def suggest_bank_ledger_with_ai(
    db: Session,
    company_id: uuid.UUID,
    *,
    transaction: BankTransaction,
    ledgers: list[Ledger],
) -> LedgerSuggestion | None:
    """Pick one active ledger for a bank transaction's counterparty side, or None.

    The counterparty side is the accounting head the money came from or went to
    — the mirror of the invoice expense/sales head — so it reuses the same
    posting-head filtering, resolution and honesty rules.
    """
    candidates = [ledger for ledger in ledgers if is_posting_head_candidate(ledger)]
    if not candidates or len(candidates) > _MAX_CANDIDATES:
        return None

    # Reasoning model: on real statements deepseek-chat returns null here (empty
    # dropdown), while the reasoner picks a usable, reviewer-confirmed ledger.
    provider = get_reasoning_provider()
    if not provider.is_configured():
        return None

    prompt = (
        "Pick the single best counterparty ledger for this bank transaction.\n\n"
        f"{_bank_context(transaction)}\n\n"
        "Available ledgers. Each line is '- NAME (group)'; the group is context "
        "only, never part of the name:\n"
        f"{_candidate_lines(candidates)}\n\n"
        'Reply with JSON only: {"ledger_name": "<one NAME copied exactly, '
        'without its (group), or null>", "reason": "<one short sentence>"}'
    )

    try:
        response = provider.generate_from_text(
            prompt,
            system_prompt=_BANK_SYSTEM_PROMPT,
            temperature=0.0,
            json_mode=True,
        )
    except AIProviderError as exc:
        logger.info("AI bank ledger suggestion unavailable: %s", exc.code)
        return None

    name, reason = _parse_choice(response.text)
    if not name:
        return None

    ledger = _resolve_choice(name, candidates)
    if ledger is None:
        logger.info("AI bank ledger suggestion rejected, not a candidate: %r", name)
        return None

    return LedgerSuggestion(
        purpose="COUNTERPARTY",
        ledger_id=ledger.id,
        ledger_name=ledger.name,
        source="AI_SUGGESTION",
        reason=reason or "Selected by AI from the company ledger master.",
        code="AI_SUGGESTED_LEDGER",
    )


__all__ = [
    "suggest_bank_ledger_with_ai",
    "suggest_ledger_with_ai",
    "suggest_party_ledger_with_ai",
    "suggest_tds_section_with_ai",
]

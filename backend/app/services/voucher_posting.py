"""Post one approved voucher directly into TallyPrime.

This is PAL's only write into Tally. The rules it enforces matter more than the
transport:

* only an APPROVED voucher may be posted, and a POSTING_FAILED one retried;
  nothing else reaches Tally, so approval can never be bypassed;
* a voucher already POSTED returns its stored reference without calling Tally,
  so a double click or a retry after a timeout cannot post twice;
* PAL's voucher id travels as Tally's REMOTEID, so even a retry Tally does see
  matches the voucher it already holds rather than creating a second one; and
* amounts and ledger names are passed through exactly as approved. Nothing is
  recalculated, balanced or substituted at posting time.

A Tally import answers with counts, not an HTTP error, so a 200 response that
Tally rejected is treated as a failure and the voucher stays retryable.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.integrations.tally import (
    TallyError,
    TallyService,
    VoucherImportLine,
    get_tally_service,
)
from app.models.voucher import (
    AccountingEntityType,
    Voucher,
    VoucherSide,
    VoucherStatus,
)
from app.services.accounting_audit import record_event
from app.services.company import get_company
from app.services.posting_period import resolve_tally_posting_date
from app.services.voucher import get_voucher
from app.services.voucher_validation import (
    VoucherTransitionError,
    VoucherValidationError,
    validate_voucher,
)

logger = logging.getLogger(__name__)

#: Statuses a post may start from. APPROVED is the normal path; POSTING_FAILED
#: is an explicit retry of a post Tally refused.
_POSTABLE = (VoucherStatus.APPROVED, VoucherStatus.POSTING_FAILED)


@dataclass(frozen=True)
class VoucherPostResult:
    """Outcome of one posting attempt."""

    success: bool
    voucher_id: uuid.UUID
    status: str
    tally_reference: str | None
    already_posted: bool
    message: str


def _tally_date(value) -> str:
    return value.strftime("%Y%m%d")


def _import_lines(voucher: Voucher) -> list[VoucherImportLine]:
    """Map the approved voucher lines onto Tally import lines, unchanged."""
    lines = []
    for line in sorted(voucher.lines, key=lambda item: item.line_number):
        if not line.ledger_name or not line.ledger_name.strip():
            raise VoucherValidationError(
                "MISSING_LEDGER",
                f"Ledger line {line.line_number} has no ledger name to post.",
            )
        lines.append(
            VoucherImportLine(
                ledger_name=line.ledger_name.strip(),
                amount=line.amount,
                is_debit=line.side is VoucherSide.DR,
            )
        )
    return lines


def post_voucher_to_tally(
    db: Session,
    company_id: uuid.UUID,
    voucher_id: uuid.UUID,
    *,
    actor: str = "local-user",
    tally_company_name: str | None = None,
    posting_month: date | None = None,
    service: TallyService | None = None,
) -> VoucherPostResult:
    """Send one approved voucher to TallyPrime and record the outcome.

    Raises VoucherTransitionError when the voucher is not in a postable state,
    and VoucherValidationError when it is not safe to post (unbalanced, or a
    line without a ledger). Neither leaves the voucher changed.
    """
    company = get_company(db, company_id)
    voucher = get_voucher(db, company_id, voucher_id)

    # Idempotency: the posted reference is the answer, Tally is not called.
    if voucher.status is VoucherStatus.POSTED:
        return VoucherPostResult(
            success=True,
            voucher_id=voucher.id,
            status=voucher.status.value,
            tally_reference=voucher.posting_reference,
            already_posted=True,
            message="This voucher is already posted to Tally.",
        )

    if voucher.status not in _POSTABLE:
        raise VoucherTransitionError(
            f"A voucher in {voucher.status.value} cannot be posted. "
            "Only an approved voucher may be posted to Tally."
        )

    # Re-validate against the stored lines: approval could predate a ledger
    # being deactivated, and an unbalanced voucher must never reach Tally.
    issues = validate_voucher(db, voucher)
    blocking = [issue for issue in issues if issue.severity == "ERROR"]
    if blocking:
        raise VoucherValidationError(
            blocking[0].code,
            f"{blocking[0].message} The voucher was not sent to Tally.",
        )

    lines = _import_lines(voucher)
    target_company = (tally_company_name or company.legal_name or company.name or "").strip()

    previous = voucher.status
    voucher.status = VoucherStatus.POSTING_IN_PROGRESS
    voucher.posting_error = None
    record_event(
        db,
        company_id=company_id,
        entity_type=AccountingEntityType.VOUCHER,
        entity_id=voucher.id,
        voucher_id=voucher.id,
        event_type="VOUCHER_POSTING_STARTED",
        actor=actor,
        previous_status=previous.value,
        new_status=voucher.status.value,
        metadata={
            "tally_company_name": target_company,
            "remote_id": str(voucher.id),
            "line_count": len(lines),
        },
    )
    db.commit()

    owned_service = service is None
    svc: TallyService = service or get_tally_service()
    failure: str | None = None
    result = None
    try:
        result = svc.post_voucher(
            company=target_company or None,
            voucher_date=_tally_date(
                resolve_tally_posting_date(voucher.voucher_date, posting_month)
            ),
            voucher_type=voucher.voucher_type.value.title(),
            voucher_number=voucher.voucher_number,
            narration=voucher.narration,
            lines=lines,
            remote_id=str(voucher.id),
        )
    except TallyError as exc:
        # A transport failure is not evidence the voucher was rejected, but it
        # is also not evidence it was stored. The voucher stays retryable and
        # REMOTEID keeps the retry from duplicating.
        failure = str(exc)
    finally:
        if owned_service:
            svc.close()

    if result is not None and not result.accepted:
        failure = result.message or (
            "TallyPrime did not store the voucher "
            f"(errors {result.errors}, exceptions {result.exceptions}, "
            f"ignored {result.ignored}, cancelled {result.cancelled})."
        )

    if failure is not None:
        voucher.status = VoucherStatus.POSTING_FAILED
        voucher.posting_error = failure
        record_event(
            db,
            company_id=company_id,
            entity_type=AccountingEntityType.VOUCHER,
            entity_id=voucher.id,
            voucher_id=voucher.id,
            event_type="VOUCHER_POSTING_FAILED",
            actor=actor,
            previous_status=VoucherStatus.POSTING_IN_PROGRESS.value,
            new_status=voucher.status.value,
            reason=failure,
            metadata={"remote_id": str(voucher.id)},
        )
        db.commit()
        db.refresh(voucher)
        logger.info("Voucher %s posting failed", voucher.voucher_number)
        return VoucherPostResult(
            success=False,
            voucher_id=voucher.id,
            status=voucher.status.value,
            tally_reference=None,
            already_posted=False,
            message=failure,
        )

    assert result is not None
    reference = result.last_voucher_id or voucher.voucher_number
    voucher.status = VoucherStatus.POSTED
    voucher.posting_reference = reference
    voucher.posted_at = datetime.now(timezone.utc)
    voucher.posting_error = None
    record_event(
        db,
        company_id=company_id,
        entity_type=AccountingEntityType.VOUCHER,
        entity_id=voucher.id,
        voucher_id=voucher.id,
        event_type="VOUCHER_POSTED_TO_TALLY",
        actor=actor,
        previous_status=VoucherStatus.POSTING_IN_PROGRESS.value,
        new_status=voucher.status.value,
        reason=f"Posted to TallyPrime as {reference}.",
        metadata={
            "tally_reference": reference,
            "remote_id": str(voucher.id),
            "created": result.created,
            "altered": result.altered,
        },
    )
    db.commit()
    db.refresh(voucher)
    return VoucherPostResult(
        success=True,
        voucher_id=voucher.id,
        status=voucher.status.value,
        tally_reference=reference,
        already_posted=False,
        message=f"Posted to TallyPrime (reference {reference}).",
    )


__all__ = ["VoucherPostResult", "post_voucher_to_tally"]

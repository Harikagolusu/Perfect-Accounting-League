"""Remove the draft vouchers built from records that are being deleted.

Vouchers point at their source (an invoice or a bank transaction) by id only,
with no foreign key, so deleting the source would leave the voucher behind with
nothing to explain it. Callers delete the source's vouchers here first.

A voucher that has gone (or is going) to Tally is never removed: deleting its
source would leave Tally with an entry PAL no longer knows about. The caller
gets `VoucherInTallyError` and must refuse the whole delete.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.voucher import (
    AccountingEntityType,
    Voucher,
    VoucherSourceType,
    VoucherStatus,
)
from app.services.accounting_audit import record_event

#: States that mean the entry reached, or is reaching, Tally.
IN_TALLY_STATUSES = frozenset(
    {
        VoucherStatus.POSTING_IN_PROGRESS,
        VoucherStatus.POSTED,
        VoucherStatus.AWAITING_IMPORT_CONFIRMATION,
    }
)


class VoucherInTallyError(Exception):
    """A source can't be deleted because one of its vouchers is in Tally."""


def delete_source_vouchers(
    db: Session,
    company_id: uuid.UUID,
    source_type: VoucherSourceType,
    source_ids: Iterable[uuid.UUID],
    *,
    reason: str,
) -> int:
    """Delete every voucher built from these sources; returns how many.

    Checks all of them before deleting any, so a refusal changes nothing.
    Does not commit: the caller deletes the sources and commits once.
    """
    ids = list(source_ids)
    if not ids:
        return 0
    vouchers = list(
        db.scalars(
            select(Voucher).where(
                Voucher.company_id == company_id,
                Voucher.source_type == source_type,
                Voucher.source_id.in_(ids),
            )
        ).all()
    )
    if any(v.status in IN_TALLY_STATUSES for v in vouchers):
        raise VoucherInTallyError(
            "A voucher made from this is already posted to Tally, so it can't be "
            "deleted here. Delete or reverse that voucher in Tally first."
        )
    for voucher in vouchers:
        record_event(
            db,
            company_id=company_id,
            entity_type=AccountingEntityType.VOUCHER,
            entity_id=voucher.id,
            event_type="VOUCHER_DELETED",
            previous_status=voucher.status.value,
            reason=reason,
            metadata={
                "voucher_number": voucher.voucher_number,
                "source_type": source_type.value,
                "source_id": str(voucher.source_id),
            },
        )
        db.delete(voucher)
    return len(vouchers)

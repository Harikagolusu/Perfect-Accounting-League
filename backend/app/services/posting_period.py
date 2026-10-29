"""The date a voucher carries into TallyPrime.

A firm books a period at a time. An invoice dated before the period being
posted must still land inside that period in Tally, so a month filter in Tally
shows every document processed for the month. An invoice already dated within
the period (or later) keeps its own date.

Only the date sent to Tally is affected. The voucher's stored `voucher_date`
remains the real document date in PAL.
"""

from __future__ import annotations

from datetime import date


def resolve_tally_posting_date(voucher_date: date, posting_month: date | None) -> date:
    """Clamp a voucher's date up to the first of the posting month when earlier.

    `posting_month` is any day in the selected month (the first is used). With
    no posting month the voucher's own date is used unchanged.
    """
    if posting_month is None:
        return voucher_date
    month_start = posting_month.replace(day=1)
    return month_start if voucher_date < month_start else voucher_date

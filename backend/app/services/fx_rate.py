"""INR exchange rate for a foreign-currency invoice.

Uses the European Central Bank reference rate for the invoice date, served by
frankfurter.dev (free, no key). Only the currency code and date are sent. The
rate is a starting point for the reviewer, never final: Indian books often use
the RBI or customs rate, so the voucher carries a warning to confirm it.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

logger = logging.getLogger(__name__)

_URL = "https://api.frankfurter.dev/v1/{day}"


def fetch_inr_rate(currency: str, on: date) -> Decimal | None:
    """INR per one unit of `currency` on `on`, or None when unavailable."""
    try:
        response = httpx.get(
            _URL.format(day=on.isoformat()),
            params={"from": currency.upper(), "to": "INR"},
            timeout=10,
        )
        response.raise_for_status()
        rate = response.json()["rates"]["INR"]
        return Decimal(str(rate)).quantize(Decimal("0.000001"))
    except (httpx.HTTPError, KeyError, ValueError, InvalidOperation) as exc:
        logger.info("No INR rate for %s on %s: %s", currency, on, type(exc).__name__)
        return None


__all__ = ["fetch_inr_rate"]

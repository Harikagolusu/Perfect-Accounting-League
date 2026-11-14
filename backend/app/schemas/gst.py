"""Response contracts for GST workings computed from PAL invoices."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class GstWorkingRowResponse(BaseModel):
    """One labelled working row. Amounts serialize as decimal strings."""

    category: str
    taxable: Decimal = Decimal("0")
    igst: Decimal = Decimal("0")
    cgst: Decimal = Decimal("0")
    sgst: Decimal = Decimal("0")


class GstWorkingResponse(BaseModel):
    """GSTR-1 and GSTR-3B workings derived from stored invoices for a period."""

    company_id: uuid.UUID
    from_date: date | None = None
    to_date: date | None = None
    invoices_used: int = 0
    gstr1: list[GstWorkingRowResponse] = []
    gstr3b: list[GstWorkingRowResponse] = []

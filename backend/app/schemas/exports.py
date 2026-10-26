"""API schemas for client-format exports.

An export is deterministic: the same canonical records produce the same rows.
Anything that cannot be mapped becomes an issue here and is excluded from the
workbook, so a downloaded file never contains a guessed ledger, tax rate or
zero standing in for an unknown amount.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

IssueSeverity = Literal["ERROR", "WARNING"]


class ExportIssueResponse(BaseModel):
    """One reason a record or field could not be exported."""

    code: str
    message: str
    severity: IssueSeverity
    record_type: str
    record_id: str | None = None
    field: str | None = None


class ExportPreviewResponse(BaseModel):
    """What a client-format export would contain, without generating a file."""

    rows: int = Field(ge=0, description="Rows that would be written to the data sheet.")
    vouchers: int = Field(
        default=0,
        ge=0,
        description="Accounting vouchers represented by those rows.",
    )
    excluded_records: int = Field(
        default=0,
        ge=0,
        description="Distinct source records excluded because of an error.",
    )
    exceptions: list[ExportIssueResponse] = Field(default_factory=list)
    warnings: list[ExportIssueResponse] = Field(default_factory=list)


__all__ = [
    "ExportIssueResponse",
    "ExportPreviewResponse",
]

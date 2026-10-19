"""Bank statement extraction abstraction.

The seam a future parser plugs into. No parser exists yet — no OCR, no
spreadsheet reader, no LLM, no bank API — and none may be faked.

So the only registered implementation reports that automatic extraction is
not configured. Later steps add `ExcelBankStatementExtractor`,
`PDFBankStatementExtractor` or `OCRBankStatementExtractor` behind the same
`BankStatementExtractor` protocol without touching the domain model or the API.

This module never produces a transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.models.bank import BankStatement

#: Stable machine-readable reason, so callers never match on prose.
EXTRACTION_NOT_CONFIGURED = "EXTRACTION_NOT_CONFIGURED"

NOT_CONFIGURED_MESSAGE = (
    "Automatic bank statement extraction is not configured yet. "
    "Add transactions manually."
)


@dataclass(frozen=True)
class StatementExtractionResult:
    """Outcome of an extraction attempt.

    `configured` is False whenever no parser could run. An unconfigured result
    never carries transactions: there is nothing to report.
    """

    configured: bool
    reason: str | None = None
    message: str | None = None
    transaction_count: int | None = None


@runtime_checkable
class BankStatementExtractor(Protocol):
    """Reads transactions out of a stored statement document."""

    name: str

    def is_configured(self) -> bool:
        """Whether this extractor can actually run right now."""
        ...

    def extract(
        self, statement: BankStatement, raw_bytes: bytes
    ) -> StatementExtractionResult:
        """Attempt to read transactions from the statement."""
        ...


class UnconfiguredBankStatementExtractor:
    """The only extractor registered today.

    It performs no work and invents nothing, so the processing endpoint always
    has an honest answer to give.
    """

    name = "unconfigured"

    def is_configured(self) -> bool:
        return False

    def extract(
        self, statement: BankStatement, raw_bytes: bytes
    ) -> StatementExtractionResult:
        return StatementExtractionResult(
            configured=False,
            reason=EXTRACTION_NOT_CONFIGURED,
            message=NOT_CONFIGURED_MESSAGE,
        )


def get_statement_extractor() -> BankStatementExtractor:
    """Return the extractor to use.

    A single indirection point: when a real parser is configured, this is the
    only function that changes.
    """
    return UnconfiguredBankStatementExtractor()
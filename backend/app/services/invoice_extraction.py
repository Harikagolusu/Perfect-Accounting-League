"""Invoice extraction abstraction.

The processing endpoint needs *some* way to ask "can this document be read?".
Today the truthful answer is always no: no OCR engine, no local vision model
and no LLM are configured, and none may be faked.

So this module defines the seam a future extractor plugs into, plus the single
honest implementation available now, which reports that automatic extraction
is not configured. Later steps add `LocalOCRExtractor`, `LocalVisionExtractor`
or `LocalLLMExtractor` behind the same `InvoiceExtractor` protocol without any
change to the domain model, the API or the database.

No invoice value is ever produced by this module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.models.document import Document

#: Stable machine-readable reason, so a caller never has to match on prose.
EXTRACTION_NOT_CONFIGURED = "EXTRACTION_NOT_CONFIGURED"

NOT_CONFIGURED_MESSAGE = (
    "Automatic extraction is not configured yet. Enter the invoice fields manually."
)


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of an extraction attempt.

    `configured` is False whenever no engine could run. A result that is not
    configured never carries values: there is nothing to report.
    """

    configured: bool
    reason: str | None = None
    message: str | None = None
    # Populated only by a real extractor. Intentionally untyped in shape terms:
    # each engine decides which fields it can honestly read.
    values: dict[str, object] | None = None


@runtime_checkable
class InvoiceExtractor(Protocol):
    """Reads invoice fields out of a stored document."""

    name: str

    def is_configured(self) -> bool:
        """Whether this extractor can actually run right now."""
        ...

    def extract(self, document: Document, raw_bytes: bytes) -> ExtractionResult:
        """Attempt to read an invoice from the document."""
        ...


class UnconfiguredExtractor:
    """The only extractor registered today.

    It performs no work and invents nothing. It exists so the processing
    endpoint has a real answer to give instead of pretending.
    """

    name = "unconfigured"

    def is_configured(self) -> bool:
        return False

    def extract(self, document: Document, raw_bytes: bytes) -> ExtractionResult:
        return ExtractionResult(
            configured=False,
            reason=EXTRACTION_NOT_CONFIGURED,
            message=NOT_CONFIGURED_MESSAGE,
        )


def get_extractor() -> InvoiceExtractor:
    """Return the extractor to use.

    A single indirection point: when a real engine is configured, this is the
    only function that needs to change.
    """
    return UnconfiguredExtractor()
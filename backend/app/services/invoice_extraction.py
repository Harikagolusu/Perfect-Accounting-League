"""Invoice extraction over the AI provider abstraction.

This module owns the seam between PAL and an AI provider:

    Document -> preparation -> AIProvider -> strict schema -> validation

It depends on `AIProvider`, never on DeepSeek directly. A local vision model
can replace the provider without changing this module, the service, the API or
the database.

The extractor does not write to the database. It returns an `ExtractionResult`
and lets the invoice service apply the lifecycle and persistence rules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from app.core.config import get_settings
from app.models.document import Document
from app.services.ai import AIProvider, AIProviderError, get_ai_provider
from app.services.document_preparation import (
    DocumentPreparationError,
    prepare_document,
)
from app.services.invoice_extraction_validation import (
    InvalidExtractionOutput,
    parse_extraction_payload,
    validate_extraction,
)
from app.services.invoice_prompt import (
    INVOICE_EXTRACTION_PROMPT,
    INVOICE_EXTRACTION_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

#: Stable machine-readable reasons, so callers never match on prose.
EXTRACTION_NOT_CONFIGURED = "EXTRACTION_NOT_CONFIGURED"
EXTRACTION_INVALID_OUTPUT = "EXTRACTION_INVALID_OUTPUT"
EXTRACTION_NEEDS_REVIEW = "EXTRACTION_NEEDS_REVIEW"
EXTRACTION_INTERNAL_ERROR = "EXTRACTION_INTERNAL_ERROR"
NOT_AN_INVOICE = "NOT_AN_INVOICE"

AI_TIMEOUT = "AI_TIMEOUT"
AI_RATE_LIMIT = "AI_RATE_LIMIT"
AI_CONNECTION_ERROR = "AI_CONNECTION_ERROR"
AI_PROVIDER_ERROR = "AI_PROVIDER_ERROR"

NOT_CONFIGURED_MESSAGE = (
    "Automatic extraction is not configured yet. Enter the invoice fields manually."
)

#: A header-only JSON object is small; this cap keeps spend bounded.
EXTRACTION_MAX_TOKENS = 2048


class ExtractionOutcome(str, Enum):
    """What the extraction attempt implies for the invoice lifecycle."""

    PROCESSED = "PROCESSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of an extraction attempt.

    `configured` is False only when no provider could run at all. A configured
    result always carries an outcome, and may carry values and review issues.
    Neither raw provider responses nor document bytes are retained here.
    """

    configured: bool
    outcome: ExtractionOutcome
    reason: str | None
    message: str
    values: dict[str, object] | None = None
    issues: tuple[str, ...] = ()
    is_invoice: bool | None = None
    provider: str | None = None
    model: str | None = None
    usage: dict | None = None


@runtime_checkable
class InvoiceExtractor(Protocol):
    """Reads invoice fields out of a stored document."""

    name: str

    def is_configured(self) -> bool:
        """Whether this extractor can actually run right now."""
        ...

    def extract(
        self, document: Document, raw_bytes: bytes
    ) -> ExtractionResult:
        """Attempt to read an invoice from the document."""
        ...


class AIVisionInvoiceExtractor:
    """Invoice extractor backed by the configured vision AI provider."""

    name = "ai-vision"

    def __init__(self, provider: AIProvider | None = None) -> None:
        self._provider = provider if provider is not None else get_ai_provider()

    def is_configured(self) -> bool:
        return self._provider.is_configured()

    def extract(
        self, document: Document, raw_bytes: bytes
    ) -> ExtractionResult:
        if not self._provider.is_configured():
            return ExtractionResult(
                configured=False,
                outcome=ExtractionOutcome.NOT_CONFIGURED,
                reason=EXTRACTION_NOT_CONFIGURED,
                message=NOT_CONFIGURED_MESSAGE,
            )

        try:
            images = prepare_document(
                document,
                raw_bytes,
                max_pages=get_settings().invoice_max_pages,
            )
        except DocumentPreparationError as exc:
            return self._failed(exc.code, exc.message)

        try:
            response = self._provider.generate_from_images(
                INVOICE_EXTRACTION_PROMPT,
                images,
                system_prompt=INVOICE_EXTRACTION_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=EXTRACTION_MAX_TOKENS,
                json_mode=True,
            )
        except AIProviderError as exc:
            if exc.code == AIProviderError.NOT_CONFIGURED:
                # Configuration disappeared mid-flight; report it as such and
                # let the service leave the invoice untouched.
                return ExtractionResult(
                    configured=False,
                    outcome=ExtractionOutcome.NOT_CONFIGURED,
                    reason=EXTRACTION_NOT_CONFIGURED,
                    message=NOT_CONFIGURED_MESSAGE,
                )
            # Use a fixed, safe message per code. Never echo `str(exc)`: a
            # future provider could put request details or credentials there.
            return self._failed(
                _PROVIDER_REASON.get(exc.code, AI_PROVIDER_ERROR),
                _PROVIDER_MESSAGE.get(
                    exc.code, "The AI provider returned an error."
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.warning(
                "Unexpected extraction failure (%s)", type(exc).__name__
            )
            return self._failed(
                EXTRACTION_INTERNAL_ERROR,
                "Automatic extraction failed unexpectedly.",
            )

        try:
            payload = parse_extraction_payload(response.text)
        except InvalidExtractionOutput:
            return self._failed(
                EXTRACTION_INVALID_OUTPUT,
                InvalidExtractionOutput.message,
                usage=response.usage,
            )

        validated = validate_extraction(payload)

        if not payload.is_invoice:
            return ExtractionResult(
                configured=True,
                outcome=ExtractionOutcome.NEEDS_REVIEW,
                reason=NOT_AN_INVOICE,
                message=(
                    "The document does not appear to be an invoice. "
                    "Review it before continuing."
                ),
                issues=("Document does not appear to be an invoice.",),
                is_invoice=False,
                provider=self._provider.name,
                model=response.model,
                usage=response.usage,
            )

        if validated.has_issues:
            return ExtractionResult(
                configured=True,
                outcome=ExtractionOutcome.NEEDS_REVIEW,
                reason=EXTRACTION_NEEDS_REVIEW,
                message=(
                    f"Extraction completed with {len(validated.issues)} "
                    "issue(s) to review."
                ),
                values=validated.values,
                issues=validated.issues,
                is_invoice=True,
                provider=self._provider.name,
                model=response.model,
                usage=response.usage,
            )

        return ExtractionResult(
            configured=True,
            outcome=ExtractionOutcome.PROCESSED,
            reason=None,
            message="Invoice extracted and processed.",
            values=validated.values,
            is_invoice=True,
            provider=self._provider.name,
            model=response.model,
            usage=response.usage,
        )

    def _failed(
        self,
        reason: str,
        message: str,
        *,
        usage: dict | None = None,
    ) -> ExtractionResult:
        return ExtractionResult(
            configured=True,
            outcome=ExtractionOutcome.FAILED,
            reason=reason,
            message=message,
            provider=self._provider.name,
            model=self._provider.model,
            usage=usage,
        )


_PROVIDER_REASON = {
    AIProviderError.TIMEOUT: AI_TIMEOUT,
    AIProviderError.RATE_LIMIT: AI_RATE_LIMIT,
    AIProviderError.CONNECTION_ERROR: AI_CONNECTION_ERROR,
    AIProviderError.PROVIDER_ERROR: AI_PROVIDER_ERROR,
}

_PROVIDER_MESSAGE = {
    AIProviderError.TIMEOUT: "The AI provider timed out. Try again.",
    AIProviderError.RATE_LIMIT: "The AI provider is rate limiting requests. Try again later.",
    AIProviderError.CONNECTION_ERROR: "Could not connect to the AI provider.",
    AIProviderError.PROVIDER_ERROR: "The AI provider returned an error.",
}


def get_extractor() -> InvoiceExtractor:
    """Return the extractor to use.

    A single indirection point: swapping DeepSeek for a local vision provider
    changes the factory, not the invoice workflow.
    """
    return AIVisionInvoiceExtractor()


__all__ = [
    "AIVisionInvoiceExtractor",
    "AI_CONNECTION_ERROR",
    "AI_PROVIDER_ERROR",
    "AI_RATE_LIMIT",
    "AI_TIMEOUT",
    "EXTRACTION_INTERNAL_ERROR",
    "EXTRACTION_INVALID_OUTPUT",
    "EXTRACTION_NEEDS_REVIEW",
    "EXTRACTION_NOT_CONFIGURED",
    "ExtractionOutcome",
    "ExtractionResult",
    "InvoiceExtractor",
    "NOT_AN_INVOICE",
    "NOT_CONFIGURED_MESSAGE",
    "get_extractor",
]

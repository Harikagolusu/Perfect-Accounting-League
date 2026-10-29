"""Invoice extraction over a provider-agnostic, text-first pipeline.

The extractor owns the seam between PAL and an AI provider:

    Document -> routing -> text / OCR / structured / vision -> AIProvider
             -> strict schema -> deterministic validation

Routing is deliberate:

* digital PDFs are read from their native text layer and never rasterised;
* scanned PDFs go native text -> OCR -> bounded page images (vision);
* JPEG/PNG go straight to vision;
* XLSX/XLS/DOCX are read as structured text and sent to a text model.

The provider contract is split into text and vision capabilities, so a
text-only provider can process digital documents and a vision-only provider can
still handle images. No provider-specific exception, key, prompt, local path or
raw response leaves this module.

The extractor does not write to the database. It returns an `ExtractionResult`
and lets the invoice service apply the lifecycle and persistence rules.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from app.core.config import get_settings
from app.models.document import Document
from app.services.ai import AIProvider, AIProviderError, get_ai_provider
from app.services.document_errors import DocumentPreparationError
from app.services.document_preparation import (
    DetectedDocument,
    DocumentKind,
    prepare_vision_images_for_extension,
    route_document,
)
from app.services.invoice_extraction_validation import (
    InvalidExtractionOutput,
    parse_extraction_payload,
    validate_extraction,
)
from app.services.invoice_prompt import (
    INVOICE_EXTRACTION_PROMPT,
    INVOICE_EXTRACTION_SYSTEM_PROMPT,
    INVOICE_TEXT_SYSTEM_PROMPT,
    build_text_extraction_prompt,
)
from app.services.ocr import OCRProcessor, OCRUnavailableError, get_ocr_processor
from app.services.pdf_text import combine_pages, read_pdf_text
from app.services.regex_extraction import RegexExtractor, render_candidates_for_prompt
from app.services.structured_documents import extract_structured_text
from app.services.text_quality import PageText, assess_text_quality

logger = logging.getLogger(__name__)

#: Stable machine-readable reasons, so callers never match on prose.
EXTRACTION_NOT_CONFIGURED = "EXTRACTION_NOT_CONFIGURED"
EXTRACTION_INVALID_OUTPUT = "EXTRACTION_INVALID_OUTPUT"
EXTRACTION_NEEDS_REVIEW = "EXTRACTION_NEEDS_REVIEW"
EXTRACTION_INTERNAL_ERROR = "EXTRACTION_INTERNAL_ERROR"
EXTRACTION_NO_TEXT = "EXTRACTION_NO_TEXT"
EXTRACTION_TEXT_TOO_LARGE = "EXTRACTION_TEXT_TOO_LARGE"
NOT_AN_INVOICE = "NOT_AN_INVOICE"

AI_TIMEOUT = "AI_TIMEOUT"
AI_RATE_LIMIT = "AI_RATE_LIMIT"
AI_CONNECTION_ERROR = "AI_CONNECTION_ERROR"
AI_PROVIDER_ERROR = "AI_PROVIDER_ERROR"
AI_UNSUPPORTED_CAPABILITY = "AI_UNSUPPORTED_CAPABILITY"

NOT_CONFIGURED_MESSAGE = (
    "Automatic extraction is not configured yet. Enter the invoice fields manually."
)
NO_TEXT_MESSAGE = (
    "No readable text was found in the document. Upload a clearer copy or enter "
    "the invoice fields manually."
)
TEXT_TOO_LARGE_MESSAGE = (
    "The document contains too much text to process safely. Split it into "
    "smaller files or enter the invoice fields manually."
)

#: Reasoning models (e.g. deepseek-flash) consume reasoning tokens before
#: writing output. 2048 is exhausted entirely by chain-of-thought, leaving
#: nothing for the JSON response. 16384 gives ample room for both.
EXTRACTION_MAX_TOKENS = 16384
#: Hard cap on document text sent to a text model. Ten ordinary invoice pages
#: sit far below this. A larger payload is rejected rather than truncated: the
#: missing tail could contain the invoice total, and a partial prompt is not a
#: safe basis for an accounting entry.
MAX_TEXT_PROMPT_CHARACTERS = 120_000


class ExtractionOutcome(str, Enum):
    """What the extraction attempt implies for the invoice lifecycle."""

    PROCESSED = "PROCESSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class ExtractionMethod(str, Enum):
    """Which processing path produced the result.

    This is safe operational metadata, not accounting data. It lets an operator
    see why a document took a particular path without storing document content.
    """

    NATIVE_TEXT = "NATIVE_TEXT"
    OCR_TEXT = "OCR_TEXT"
    IMAGE_VISION = "IMAGE_VISION"
    EXCEL_TEXT = "EXCEL_TEXT"
    DOCX_TEXT = "DOCX_TEXT"


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
    extraction_method: str | None = None


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


class AIDocumentInvoiceExtractor:
    """Text-first invoice extractor backed by the configured AI provider."""

    name = "ai-document"

    def __init__(
        self,
        provider: AIProvider | None = None,
        *,
        ocr_processor: OCRProcessor | None = None,
    ) -> None:
        self._provider = provider if provider is not None else get_ai_provider()
        self._ocr = (
            ocr_processor if ocr_processor is not None else get_ocr_processor()
        )

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
            detected = route_document(
                document,
                raw_bytes,
                max_pages=get_settings().invoice_max_pages,
            )
            return self._extract_detected(detected, raw_bytes)
        except DocumentPreparationError as exc:
            return self._failed(exc.code, exc.message)
        except AIProviderError as exc:
            return self._provider_failure(exc)
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.warning(
                "Unexpected extraction failure (%s)", type(exc).__name__
            )
            return self._failed(
                EXTRACTION_INTERNAL_ERROR,
                "Automatic extraction failed unexpectedly.",
            )

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _extract_detected(
        self, detected: DetectedDocument, raw_bytes: bytes
    ) -> ExtractionResult:
        if detected.kind is DocumentKind.IMAGE:
            images = prepare_vision_images_for_extension(
                detected.extension,
                raw_bytes,
                max_pages=get_settings().invoice_max_pages,
            )
            return self._extract_with_vision(images, ExtractionMethod.IMAGE_VISION)

        if detected.kind is DocumentKind.PDF:
            return self._extract_pdf(detected, raw_bytes)

        method = (
            ExtractionMethod.EXCEL_TEXT
            if detected.kind in {DocumentKind.XLSX, DocumentKind.XLS}
            else ExtractionMethod.DOCX_TEXT
        )
        structured = extract_structured_text(detected.extension, raw_bytes)
        if not structured.text.strip():
            return self._failed(
                EXTRACTION_NO_TEXT,
                NO_TEXT_MESSAGE,
                extraction_method=method,
            )
        pages = (PageText(page_number=1, text=structured.text),)
        # Spreadsheet/DOCX headers can be short, so use a lower size threshold
        # while still rejecting empty, noise-only or content-free files.
        quality = assess_text_quality(pages, min_meaningful_characters=20)
        if not quality.usable:
            logger.info(
                "Structured document text is not usable (%s)", quality.reason
            )
            return self._failed(
                EXTRACTION_NO_TEXT,
                NO_TEXT_MESSAGE,
                extraction_method=method,
            )
        return self._extract_text_pages(pages, method)

    def _extract_pdf(
        self, detected: DetectedDocument, raw_bytes: bytes
    ) -> ExtractionResult:
        max_pages = get_settings().invoice_max_pages
        pdf_text = read_pdf_text(raw_bytes, max_pages=max_pages)
        text_quality = assess_text_quality(pdf_text.pages)

        if text_quality.usable:
            return self._extract_text_pages(
                pdf_text.pages, ExtractionMethod.NATIVE_TEXT
            )

        logger.info(
            "PDF native text is not usable (%s); trying OCR", text_quality.reason
        )
        ocr_pages = self._try_ocr(raw_bytes, max_pages=max_pages)
        if ocr_pages is not None:
            ocr_quality = assess_text_quality(ocr_pages)
            if ocr_quality.usable:
                return self._extract_text_pages(
                    ocr_pages, ExtractionMethod.OCR_TEXT
                )
            logger.info(
                "OCR text is not usable either (%s); falling back to vision",
                ocr_quality.reason,
            )

        # Vision is the last resort for a PDF: only now are pages rendered.
        images = prepare_vision_images_for_extension(
            detected.extension,
            raw_bytes,
            max_pages=max_pages,
        )
        return self._extract_with_vision(images, ExtractionMethod.IMAGE_VISION)

    def _try_ocr(
        self, raw_bytes: bytes, *, max_pages: int
    ) -> tuple[PageText, ...] | None:
        if self._ocr is None:
            return None
        try:
            if not self._ocr.is_available():
                return None
            pages = self._ocr.extract_text(raw_bytes, max_pages=max_pages)
        except OCRUnavailableError:
            logger.info("OCR is not available; using the vision fallback")
            return None
        except Exception as exc:
            # OCR is a fallback: never let its failure mask the vision path.
            logger.warning("OCR fallback failed (%s)", type(exc).__name__)
            return None
        return pages or None

    # ------------------------------------------------------------------
    # Provider calls
    # ------------------------------------------------------------------

    def _extract_text_pages(
        self,
        pages: tuple[PageText, ...],
        method: ExtractionMethod,
    ) -> ExtractionResult:
        text = combine_pages(pages)
        if not text.strip():
            return self._failed(
                EXTRACTION_NO_TEXT, NO_TEXT_MESSAGE, extraction_method=method
            )
        if len(text) > MAX_TEXT_PROMPT_CHARACTERS:
            logger.info(
                "Extracted text exceeds the safe prompt limit (%d characters)",
                len(text),
            )
            return self._failed(
                EXTRACTION_TEXT_TOO_LARGE,
                TEXT_TOO_LARGE_MESSAGE,
                extraction_method=method,
            )

        candidates = RegexExtractor().extract(pages)
        prompt = build_text_extraction_prompt(
            text, render_candidates_for_prompt(candidates)
        )

        # Prefer the explicit text capability, but keep a legacy provider that
        # only implements `generate_text` usable for digital documents.
        generator = getattr(self._provider, "generate_from_text", None)
        if generator is None:
            generator = getattr(self._provider, "generate_text", None)
        if generator is None:
            return self._failed(
                AI_UNSUPPORTED_CAPABILITY,
                "The configured AI provider does not support text extraction.",
                extraction_method=method,
            )
        request: dict[str, object] = {
            "system_prompt": INVOICE_TEXT_SYSTEM_PROMPT,
            "temperature": 0.0,
            "max_tokens": EXTRACTION_MAX_TOKENS,
        }
        if _accepts_keyword(generator, "json_mode"):
            request["json_mode"] = True
        try:
            response = generator(prompt, **request)
        except NotImplementedError:
            return self._failed(
                AI_UNSUPPORTED_CAPABILITY,
                "The configured AI provider does not support text extraction.",
                extraction_method=method,
            )
        except AIProviderError as exc:
            return self._provider_failure(exc, method)

        return self._interpret_response(response, method)

    def _extract_with_vision(
        self,
        images: tuple,
        method: ExtractionMethod,
    ) -> ExtractionResult:
        generator = getattr(self._provider, "generate_from_images", None)
        if generator is None:
            return self._failed(
                AI_UNSUPPORTED_CAPABILITY,
                "The configured AI provider does not support image extraction.",
                extraction_method=method,
            )

        request: dict[str, object] = {
            "system_prompt": INVOICE_EXTRACTION_SYSTEM_PROMPT,
            "temperature": 0.0,
            "max_tokens": EXTRACTION_MAX_TOKENS,
        }
        # Legacy providers may predate JSON mode. The prompt still pins the
        # schema; omitting the API hint keeps the provider usable.
        if _accepts_keyword(generator, "json_mode"):
            request["json_mode"] = True

        try:
            response = generator(INVOICE_EXTRACTION_PROMPT, images, **request)
        except NotImplementedError:
            return self._failed(
                AI_UNSUPPORTED_CAPABILITY,
                "The configured AI provider does not support image extraction.",
                extraction_method=method,
            )
        except AIProviderError as exc:
            return self._provider_failure(exc, method)
        return self._interpret_response(response, method)

    def _interpret_response(self, response, method: ExtractionMethod) -> ExtractionResult:
        try:
            payload = parse_extraction_payload(response.text)
        except InvalidExtractionOutput as exc:
            # Log the stable rejection reason server-side only. The client
            # keeps the fixed, safe message; provider payloads and raw output
            # never leave this boundary.
            logger.warning(
                "Invoice extraction output rejected: reason=%s model=%s method=%s",
                exc.reason,
                response.model,
                method.value,
            )
            return self._failed(
                EXTRACTION_INVALID_OUTPUT,
                InvalidExtractionOutput.message,
                usage=response.usage,
                extraction_method=method,
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
                extraction_method=method.value,
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
                extraction_method=method.value,
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
            extraction_method=method.value,
        )

    # ------------------------------------------------------------------
    # Failure mapping
    # ------------------------------------------------------------------

    def _provider_failure(
        self,
        exc: AIProviderError,
        method: ExtractionMethod | None = None,
    ) -> ExtractionResult:
        if exc.code == AIProviderError.NOT_CONFIGURED:
            # Configuration disappeared mid-flight; report it as such and let
            # the service leave the invoice untouched.
            return ExtractionResult(
                configured=False,
                outcome=ExtractionOutcome.NOT_CONFIGURED,
                reason=EXTRACTION_NOT_CONFIGURED,
                message=NOT_CONFIGURED_MESSAGE,
                extraction_method=(
                    method.value if method is not None else None
                ),
            )
        # Use a fixed, safe message per code. Never echo `str(exc)`: a future
        # provider could put request details or credentials there.
        return self._failed(
            _PROVIDER_REASON.get(exc.code, AI_PROVIDER_ERROR),
            _PROVIDER_MESSAGE.get(exc.code, "The AI provider returned an error."),
            extraction_method=method,
        )

    def _failed(
        self,
        reason: str,
        message: str,
        *,
        usage: dict | None = None,
        extraction_method: ExtractionMethod | None = None,
    ) -> ExtractionResult:
        return ExtractionResult(
            configured=True,
            outcome=ExtractionOutcome.FAILED,
            reason=reason,
            message=message,
            provider=self._provider.name,
            model=self._provider.model,
            usage=usage,
            extraction_method=(
                extraction_method.value if extraction_method is not None else None
            ),
        )


#: The previous class name remains as an alias so existing imports keep working.
#: The implementation is no longer vision-only.
AIVisionInvoiceExtractor = AIDocumentInvoiceExtractor


def _accepts_keyword(callable_object: object, keyword: str) -> bool:
    """Whether a provider method accepts `keyword`.

    Legacy providers may predate the explicit `json_mode` argument. Inspecting
    the signature keeps them usable without catching a TypeError raised inside
    the provider itself.
    """
    try:
        parameters = inspect.signature(callable_object).parameters
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        return False
    return keyword in parameters


_PROVIDER_REASON = {
    AIProviderError.TIMEOUT: AI_TIMEOUT,
    AIProviderError.RATE_LIMIT: AI_RATE_LIMIT,
    AIProviderError.CONNECTION_ERROR: AI_CONNECTION_ERROR,
    AIProviderError.PROVIDER_ERROR: AI_PROVIDER_ERROR,
    AIProviderError.UNSUPPORTED_CAPABILITY: AI_UNSUPPORTED_CAPABILITY,
}

_PROVIDER_MESSAGE = {
    AIProviderError.TIMEOUT: "The AI provider timed out. Try again.",
    AIProviderError.RATE_LIMIT: "The AI provider is rate limiting requests. Try again later.",
    AIProviderError.CONNECTION_ERROR: "Could not connect to the AI provider.",
    AIProviderError.PROVIDER_ERROR: "The AI provider returned an error.",
    AIProviderError.UNSUPPORTED_CAPABILITY: (
        "The configured AI provider does not support this document type."
    ),
}


def get_extractor() -> InvoiceExtractor:
    """Return the extractor to use.

    A single indirection point: swapping DeepSeek for a local model changes the
    factory, not the invoice workflow.
    """
    return AIDocumentInvoiceExtractor()


__all__ = [
    "AIDocumentInvoiceExtractor",
    "AIVisionInvoiceExtractor",
    "AI_CONNECTION_ERROR",
    "AI_PROVIDER_ERROR",
    "AI_RATE_LIMIT",
    "AI_TIMEOUT",
    "AI_UNSUPPORTED_CAPABILITY",
    "EXTRACTION_INTERNAL_ERROR",
    "EXTRACTION_INVALID_OUTPUT",
    "EXTRACTION_NEEDS_REVIEW",
    "EXTRACTION_NOT_CONFIGURED",
    "EXTRACTION_NO_TEXT",
    "EXTRACTION_TEXT_TOO_LARGE",
    "ExtractionMethod",
    "ExtractionOutcome",
    "ExtractionResult",
    "InvoiceExtractor",
    "NOT_AN_INVOICE",
    "NOT_CONFIGURED_MESSAGE",
    "NO_TEXT_MESSAGE",
    "get_extractor",
]

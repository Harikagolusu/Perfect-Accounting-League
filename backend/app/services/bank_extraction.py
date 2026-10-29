"""Bank statement extraction abstraction.

The seam a real parser plugs into. The AI extractor below uses the same
document preparation pipeline as invoice extraction:

    Document -> routing -> text / OCR / structured / vision -> AIProvider
             -> JSON response -> typed transaction rows

The extractor does not write to the database. It returns a
`StatementExtractionResult` and lets `process_statement` apply the lifecycle
and persistence rules.
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.models.bank import BankStatement

if TYPE_CHECKING:
    from app.models.document import Document

logger = logging.getLogger(__name__)

#: Stable machine-readable reasons, so callers never match on prose.
EXTRACTION_NOT_CONFIGURED = "EXTRACTION_NOT_CONFIGURED"
EXTRACTION_INVALID_OUTPUT = "EXTRACTION_INVALID_OUTPUT"
EXTRACTION_INTERNAL_ERROR = "EXTRACTION_INTERNAL_ERROR"
NOT_A_BANK_STATEMENT = "NOT_A_BANK_STATEMENT"
EXTRACTION_NO_TEXT = "EXTRACTION_NO_TEXT"
EXTRACTION_TEXT_TOO_LARGE = "EXTRACTION_TEXT_TOO_LARGE"
AI_TIMEOUT = "AI_TIMEOUT"
AI_RATE_LIMIT = "AI_RATE_LIMIT"
AI_CONNECTION_ERROR = "AI_CONNECTION_ERROR"
AI_PROVIDER_ERROR = "AI_PROVIDER_ERROR"
AI_UNSUPPORTED_CAPABILITY = "AI_UNSUPPORTED_CAPABILITY"

NOT_CONFIGURED_MESSAGE = (
    "Automatic bank statement extraction is not configured yet. "
    "Add transactions manually."
)

#: A statement with 200 transactions × ~50 tokens each needs room to breathe.
BANK_MAX_TOKENS = 16_384
#: Hard cap on document text. Larger files are rejected rather than truncated
#: to avoid cutting off mid-statement and producing a partial result.
BANK_MAX_TEXT_CHARACTERS = 400_000
#: Max pages to process from a PDF bank statement.
BANK_MAX_PAGES = 20

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%d-%m-%y",
    "%d %b %Y",
    "%d %b %y",
    "%d %B %Y",
)


@dataclass(frozen=True)
class StatementExtractionResult:
    """Outcome of an extraction attempt.

    `configured` is False whenever no parser could run. An unconfigured result
    never carries transactions.

    `transactions` holds parsed transaction rows ready to persist.
    `statement_updates` holds statement-level fields extracted from the header
    (dates, balances) that `process_statement` may apply to the statement row.
    """

    configured: bool
    reason: str | None = None
    message: str | None = None
    transaction_count: int | None = None
    transactions: tuple[dict, ...] = ()
    statement_updates: dict | None = None
    issues: tuple[str, ...] = ()


@runtime_checkable
class BankStatementExtractor(Protocol):
    """Reads transactions out of a stored statement document."""

    name: str

    def is_configured(self) -> bool:
        """Whether this extractor can actually run right now."""
        ...

    def extract(
        self,
        statement: BankStatement,
        raw_bytes: bytes,
        *,
        document: "Document | None" = None,
    ) -> StatementExtractionResult:
        """Attempt to read transactions from the statement."""
        ...


class UnconfiguredBankStatementExtractor:
    """The no-op extractor registered when AI is not configured.

    It performs no work and invents nothing, so the processing endpoint always
    has an honest answer to give.
    """

    name = "unconfigured"

    def is_configured(self) -> bool:
        return False

    def extract(
        self,
        statement: BankStatement,
        raw_bytes: bytes,
        *,
        document: "Document | None" = None,
    ) -> StatementExtractionResult:
        return StatementExtractionResult(
            configured=False,
            reason=EXTRACTION_NOT_CONFIGURED,
            message=NOT_CONFIGURED_MESSAGE,
        )


# ---------------------------------------------------------------------------
# AI extractor
# ---------------------------------------------------------------------------

class AIBankStatementExtractor:
    """Text-first bank statement extractor backed by the configured AI provider."""

    name = "ai-document"

    def __init__(self, provider=None, *, ocr_processor=None) -> None:
        from app.services.ai import get_ai_provider
        from app.services.ocr import get_ocr_processor

        self._provider = provider if provider is not None else get_ai_provider()
        self._ocr = (
            ocr_processor if ocr_processor is not None else get_ocr_processor()
        )

    def is_configured(self) -> bool:
        return self._provider.is_configured()

    def extract(
        self,
        statement: BankStatement,
        raw_bytes: bytes,
        *,
        document: "Document | None" = None,
    ) -> StatementExtractionResult:
        from app.services.ai import AIProviderError
        from app.services.document_errors import DocumentPreparationError
        from app.services.document_preparation import (
            DocumentKind,
            prepare_vision_images_for_extension,
            route_document,
        )

        if not self._provider.is_configured():
            return StatementExtractionResult(
                configured=False,
                reason=EXTRACTION_NOT_CONFIGURED,
                message=NOT_CONFIGURED_MESSAGE,
            )

        if not raw_bytes:
            return self._failed(
                EXTRACTION_NO_TEXT, "No document bytes to extract from."
            )

        try:
            detected = route_document(
                document,
                raw_bytes,
                max_pages=BANK_MAX_PAGES,
            )
            return self._extract_detected(detected, raw_bytes)
        except DocumentPreparationError as exc:
            return self._failed(exc.code, exc.message)
        except AIProviderError as exc:
            return self._provider_failure(exc)
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.warning(
                "Unexpected bank extraction failure (%s)", type(exc).__name__
            )
            return self._failed(
                EXTRACTION_INTERNAL_ERROR,
                "Automatic extraction failed unexpectedly.",
            )

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _extract_detected(self, detected, raw_bytes: bytes) -> StatementExtractionResult:
        from app.services.document_preparation import (
            DocumentKind,
            prepare_vision_images_for_extension,
        )
        from app.services.structured_documents import extract_structured_text
        from app.services.text_quality import PageText, assess_text_quality

        if detected.kind is DocumentKind.IMAGE:
            images = prepare_vision_images_for_extension(
                detected.extension, raw_bytes, max_pages=BANK_MAX_PAGES
            )
            return self._extract_with_vision(images)

        if detected.kind is DocumentKind.PDF:
            return self._extract_pdf(detected, raw_bytes)

        structured = extract_structured_text(detected.extension, raw_bytes)
        if not structured.text.strip():
            return self._failed(EXTRACTION_NO_TEXT, "No readable text in document.")
        pages = (PageText(page_number=1, text=structured.text),)
        quality = assess_text_quality(pages, min_meaningful_characters=20)
        if not quality.usable:
            return self._failed(EXTRACTION_NO_TEXT, "Document text is not usable.")
        return self._extract_text_pages(pages)

    def _extract_pdf(self, detected, raw_bytes: bytes) -> StatementExtractionResult:
        from app.services.document_preparation import prepare_vision_images_for_extension
        from app.services.ocr import OCRUnavailableError
        from app.services.pdf_text import combine_pages, read_pdf_text
        from app.services.text_quality import assess_text_quality

        pdf_text = read_pdf_text(raw_bytes, max_pages=BANK_MAX_PAGES)
        quality = assess_text_quality(pdf_text.pages)
        if quality.usable:
            return self._extract_text_pages(pdf_text.pages)

        logger.info("PDF native text not usable (%s); trying OCR", quality.reason)
        ocr_pages = self._try_ocr(raw_bytes)
        if ocr_pages is not None:
            ocr_quality = assess_text_quality(ocr_pages)
            if ocr_quality.usable:
                return self._extract_text_pages(ocr_pages)
            logger.info("OCR text not usable (%s); using vision", ocr_quality.reason)

        images = prepare_vision_images_for_extension(
            detected.extension, raw_bytes, max_pages=BANK_MAX_PAGES
        )
        return self._extract_with_vision(images)

    def _try_ocr(self, raw_bytes: bytes):
        from app.services.ocr import OCRUnavailableError

        if self._ocr is None:
            return None
        try:
            if not self._ocr.is_available():
                return None
            pages = self._ocr.extract_text(raw_bytes, max_pages=BANK_MAX_PAGES)
        except OCRUnavailableError:
            return None
        except Exception as exc:
            logger.warning("OCR fallback failed (%s)", type(exc).__name__)
            return None
        return pages or None

    # ------------------------------------------------------------------
    # Provider calls
    # ------------------------------------------------------------------

    def _extract_text_pages(self, pages) -> StatementExtractionResult:
        from app.services.pdf_text import combine_pages
        from app.services.bank_prompt import BANK_TEXT_SYSTEM_PROMPT, build_bank_text_prompt
        from app.services.ai import AIProviderError

        text = combine_pages(pages)
        if not text.strip():
            return self._failed(EXTRACTION_NO_TEXT, "No readable text in document.")
        if len(text) > BANK_MAX_TEXT_CHARACTERS:
            return self._failed(
                EXTRACTION_TEXT_TOO_LARGE,
                "The document is too large to process in one pass.",
            )

        prompt = build_bank_text_prompt(text)

        generator = getattr(self._provider, "generate_from_text", None)
        if generator is None:
            generator = getattr(self._provider, "generate_text", None)
        if generator is None:
            return self._failed(
                AI_UNSUPPORTED_CAPABILITY,
                "The configured AI provider does not support text extraction.",
            )
        request: dict = {
            "system_prompt": BANK_TEXT_SYSTEM_PROMPT,
            "temperature": 0.0,
            "max_tokens": BANK_MAX_TOKENS,
        }
        if _accepts_keyword(generator, "json_mode"):
            request["json_mode"] = True
        try:
            response = generator(prompt, **request)
        except NotImplementedError:
            return self._failed(
                AI_UNSUPPORTED_CAPABILITY,
                "The configured AI provider does not support text extraction.",
            )
        except AIProviderError as exc:
            return self._provider_failure(exc)

        return self._interpret_response(response.text)

    def _extract_with_vision(self, images) -> StatementExtractionResult:
        from app.services.bank_prompt import (
            BANK_EXTRACTION_PROMPT,
            BANK_EXTRACTION_SYSTEM_PROMPT,
        )
        from app.services.ai import AIProviderError

        generator = getattr(self._provider, "generate_from_images", None)
        if generator is None:
            return self._failed(
                AI_UNSUPPORTED_CAPABILITY,
                "The configured AI provider does not support image extraction.",
            )
        request: dict = {
            "system_prompt": BANK_EXTRACTION_SYSTEM_PROMPT,
            "temperature": 0.0,
            "max_tokens": BANK_MAX_TOKENS,
        }
        if _accepts_keyword(generator, "json_mode"):
            request["json_mode"] = True
        try:
            response = generator(BANK_EXTRACTION_PROMPT, images, **request)
        except NotImplementedError:
            return self._failed(
                AI_UNSUPPORTED_CAPABILITY,
                "The configured AI provider does not support image extraction.",
            )
        except AIProviderError as exc:
            return self._provider_failure(exc)
        return self._interpret_response(response.text)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _interpret_response(self, raw_text: str) -> StatementExtractionResult:
        try:
            data = _parse_bank_response(raw_text)
        except _ParseError as exc:
            return self._failed(EXTRACTION_INVALID_OUTPUT, str(exc))

        if not data.get("is_bank_statement"):
            return StatementExtractionResult(
                configured=True,
                reason=NOT_A_BANK_STATEMENT,
                message="The document does not appear to be a bank statement.",
                issues=("Document does not appear to be a bank statement.",),
            )

        transactions = tuple(data["transactions"])
        issues: list[str] = list(data.get("issues", []))

        statement_updates: dict = {}
        for key in (
            "statement_start_date", "statement_end_date",
            "opening_balance", "closing_balance",
        ):
            val = data.get(key)
            if val is not None:
                statement_updates[key] = val

        msg = (
            f"Extracted {len(transactions)} transaction(s)."
            if not issues
            else f"Extracted {len(transactions)} transaction(s) with {len(issues)} issue(s)."
        )
        return StatementExtractionResult(
            configured=True,
            reason=None,
            message=msg,
            transaction_count=len(transactions),
            transactions=transactions,
            statement_updates=statement_updates or None,
            issues=tuple(issues),
        )

    # ------------------------------------------------------------------
    # Failure helpers
    # ------------------------------------------------------------------

    def _provider_failure(self, exc) -> StatementExtractionResult:
        from app.services.ai import AIProviderError

        if exc.code == AIProviderError.NOT_CONFIGURED:
            return StatementExtractionResult(
                configured=False,
                reason=EXTRACTION_NOT_CONFIGURED,
                message=NOT_CONFIGURED_MESSAGE,
            )
        return self._failed(
            _PROVIDER_REASON.get(exc.code, AI_PROVIDER_ERROR),
            _PROVIDER_MESSAGE.get(exc.code, "The AI provider returned an error."),
        )

    def _failed(self, reason: str, message: str) -> StatementExtractionResult:
        return StatementExtractionResult(
            configured=True,
            reason=reason,
            message=message,
        )


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------

class _ParseError(ValueError):
    pass


def _parse_bank_response(raw_text: str) -> dict:
    """Parse and type-convert the AI's JSON response.

    Returns a dict with Python-typed values:
    - dates as `date` objects (or None)
    - balances as `Decimal` (or None)
    - transactions as list[dict] with same typing
    - issues as list[str]
    """
    # Strip code fences the model sometimes adds despite the prompt.
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        )
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _ParseError(f"AI response is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise _ParseError("AI response is not a JSON object.")

    result: dict = {"is_bank_statement": bool(raw.get("is_bank_statement", False))}
    if not result["is_bank_statement"]:
        return result

    issues: list[str] = []

    for key in ("statement_start_date", "statement_end_date"):
        val = raw.get(key)
        result[key] = _parse_date_or_none(val, key, issues)

    for key in ("opening_balance", "closing_balance"):
        val = raw.get(key)
        result[key] = _parse_decimal_or_none(val, key, issues)

    raw_txns = raw.get("transactions")
    if not isinstance(raw_txns, list):
        raw_txns = []

    transactions: list[dict] = []
    for i, row in enumerate(raw_txns):
        if not isinstance(row, dict):
            continue
        txn_date = _parse_date_or_none(row.get("transaction_date"), f"row {i} transaction_date", issues)
        if txn_date is None:
            # Skip rows with no parseable date — they are header/footer rows.
            continue
        txn: dict = {
            "transaction_date": txn_date,
            "value_date": _parse_date_or_none(row.get("value_date"), None, None),
            "description": _clean_str(row.get("description")),
            "reference_number": _clean_str(row.get("reference_number")),
            "debit_amount": _parse_decimal_or_none(row.get("debit_amount"), None, None),
            "credit_amount": _parse_decimal_or_none(row.get("credit_amount"), None, None),
            "balance": _parse_decimal_or_none(row.get("balance"), None, None),
        }
        transactions.append(txn)

    result["transactions"] = transactions
    result["issues"] = issues
    return result


def _parse_date_or_none(
    value: object,
    field_name: str | None,
    issues: list[str] | None,
) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    if issues is not None and field_name is not None:
        issues.append(f"Unrecognised date format for {field_name}: {text!r}")
    return None


def _parse_decimal_or_none(
    value: object,
    field_name: str | None,
    issues: list[str] | None,
) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        if issues is not None and field_name is not None:
            issues.append(f"Non-numeric value for {field_name}: {text!r}")
        return None


def _clean_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _accepts_keyword(callable_object: object, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_object).parameters
    except (TypeError, ValueError):
        return False
    return keyword in parameters


_PROVIDER_REASON = {}
_PROVIDER_MESSAGE = {}


def _build_provider_maps() -> None:
    try:
        from app.services.ai import AIProviderError
        global _PROVIDER_REASON, _PROVIDER_MESSAGE
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
    except ImportError:
        pass


_build_provider_maps()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_statement_extractor() -> BankStatementExtractor:
    """Return the extractor to use.

    Returns the AI extractor when a provider is configured, or the no-op
    extractor otherwise. This is the only function that changes when the
    provider is swapped.
    """
    try:
        from app.services.ai import get_ai_provider
        provider = get_ai_provider()
        if provider.is_configured():
            return AIBankStatementExtractor(provider=provider)
    except Exception:
        pass
    return UnconfiguredBankStatementExtractor()

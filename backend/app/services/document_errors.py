"""Errors shared by document routing, text extraction and OCR.

Keeping the error class in its own module lets the router, the PDF reader, the
OCR processor and the invoice extractor share stable codes without importing
each other in a cycle. `message` is always safe to show a user; no filesystem
path, document content or provider detail belongs in it.
"""

from __future__ import annotations


class DocumentPreparationError(Exception):
    """The document cannot be turned into text or provider-ready images."""

    #: Extension is not one the extraction pipeline can read.
    UNSUPPORTED_DOCUMENT_TYPE = "UNSUPPORTED_DOCUMENT_TYPE"
    #: The file is empty or does not match its declared format.
    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    #: The file could not be parsed at all.
    DOCUMENT_UNREADABLE = "DOCUMENT_UNREADABLE"
    #: The PDF has more pages than the configured limit.
    DOCUMENT_TOO_MANY_PAGES = "DOCUMENT_TOO_MANY_PAGES"
    #: A structured container expands beyond the safe processing budget.
    DOCUMENT_TOO_LARGE = "DOCUMENT_TOO_LARGE"
    #: The PDF is password-protected.
    DOCUMENT_ENCRYPTED = "DOCUMENT_ENCRYPTED"
    #: The page limit is not usable (misconfigured).
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    #: The document has no readable text after extraction.
    NO_READABLE_CONTENT = "NO_READABLE_CONTENT"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


#: The router and readers speak about "document processing"; the older image
#: preparation module speaks about "document preparation". They are the same
#: failure surface, so callers can use either name.
DocumentProcessingError = DocumentPreparationError


__all__ = ["DocumentPreparationError", "DocumentProcessingError"]

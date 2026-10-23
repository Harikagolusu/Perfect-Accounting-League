"""Document routing and provider-ready image preparation.

This module is the single place that decides *what kind of file* an invoice
document is. Keeping routing here means the extractor never scatters extension
checks through its branches:

* PDF   -> native text first, OCR/vision only as fallbacks;
* JPEG/PNG -> vision directly;
* XLSX/XLS/DOCX -> structured text, then the text model.

It also owns the vision fallback's image preparation: the stored bytes are
turned into bounded JPEGs in memory. Nothing is written to disk, so the original
upload is untouched, and no provider-specific object leaks out of this module.

Errors use the shared `DocumentPreparationError` codes. Messages are safe to
show a user and never contain a filesystem path or document content.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from app.core.storage import CONTENT_SIGNATURES
from app.models.document import Document
from app.services.ai import ImageInput
from app.services.document_errors import DocumentPreparationError
from app.services.pdf_text import inspect_pdf

logger = logging.getLogger(__name__)

#: Documents the vision fallback can turn into images.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".jpg", ".jpeg", ".png"})

#: Documents the invoice extractor can route. Spreadsheets and DOCX go through
#: the text/structured path; images and PDFs may use vision as a fallback.
SUPPORTED_INVOICE_EXTENSIONS: frozenset[str] = (
    SUPPORTED_EXTENSIONS | frozenset({".xlsx", ".xls", ".docx"})
)

#: Render resolution for PDF pages when vision is genuinely required. 150 DPI
#: keeps normal invoice text legible while staying below the wasteful range.
PDF_RENDER_DPI = 150
#: Longest edge kept for any image. Invoice text remains readable and the
#: base64 payload stays bounded.
MAX_IMAGE_EDGE = 2200
#: JPEG quality for the prepared image. Visually lossless for text at this DPI.
JPEG_QUALITY = 88


class DocumentKind(str, Enum):
    """How the extraction pipeline should read a stored document."""

    PDF = "PDF"
    IMAGE = "IMAGE"
    XLSX = "XLSX"
    XLS = "XLS"
    DOCX = "DOCX"


@dataclass(frozen=True)
class DetectedDocument:
    """A validated document plus the processing path it routes to."""

    kind: DocumentKind
    extension: str
    page_count: int | None = None


def _document_extension(document: Document) -> str:
    """The stored file's extension, which the storage layer generated itself."""
    extension = Path(document.stored_filename).suffix.lower()
    if extension:
        return extension
    # Defensive fallback for a hand-written row; never used to build a path.
    return Path(document.original_filename).suffix.lower()


def route_document(
    document: Document,
    raw_bytes: bytes,
    *,
    max_pages: int,
) -> DetectedDocument:
    """Validate a stored document and decide which reader should handle it.

    Raises `DocumentPreparationError` with a stable code on any unsupported,
    empty, mismatched, encrypted or oversized input. This runs before any
    provider call, so an invalid document never costs a request.
    """
    extension = _document_extension(document)
    if extension not in SUPPORTED_INVOICE_EXTENSIONS:
        raise DocumentPreparationError(
            DocumentPreparationError.UNSUPPORTED_DOCUMENT_TYPE,
            "Only PDF, JPEG, PNG, XLSX, XLS and DOCX documents can be read for "
            "invoice extraction.",
        )

    if not raw_bytes:
        raise DocumentPreparationError(
            DocumentPreparationError.INVALID_DOCUMENT,
            "The stored document is empty.",
        )

    signature = CONTENT_SIGNATURES.get(extension)
    if signature is not None and not raw_bytes.startswith(signature):
        raise DocumentPreparationError(
            DocumentPreparationError.INVALID_DOCUMENT,
            "The stored file does not look like its declared format.",
        )

    if extension == ".pdf":
        page_count = inspect_pdf(raw_bytes, max_pages=max_pages)
        return DetectedDocument(DocumentKind.PDF, extension, page_count=page_count)
    if extension in {".jpg", ".jpeg", ".png"}:
        return DetectedDocument(DocumentKind.IMAGE, extension)
    if extension == ".xlsx":
        return DetectedDocument(DocumentKind.XLSX, extension)
    if extension == ".xls":
        return DetectedDocument(DocumentKind.XLS, extension)
    return DetectedDocument(DocumentKind.DOCX, extension)


def prepare_vision_images(
    document: Document,
    raw_bytes: bytes,
    *,
    max_pages: int,
) -> tuple[ImageInput, ...]:
    """Prepare a stored PDF or image for the vision provider."""
    return prepare_vision_images_for_extension(
        _document_extension(document),
        raw_bytes,
        max_pages=max_pages,
    )


def prepare_vision_images_for_extension(
    extension: str,
    raw_bytes: bytes,
    *,
    max_pages: int,
) -> tuple[ImageInput, ...]:
    """Prepare an already-routed PDF or image for the vision provider.

    PDFs are rendered only when vision is actually needed. Digital PDFs use
    `pdf_text` instead, so they are never rasterised in the normal path.
    """
    extension = extension.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise DocumentPreparationError(
            DocumentPreparationError.UNSUPPORTED_DOCUMENT_TYPE,
            "Only PDF, JPEG and PNG documents can be prepared as images.",
        )

    if not raw_bytes:
        raise DocumentPreparationError(
            DocumentPreparationError.INVALID_DOCUMENT,
            "The stored document is empty.",
        )

    signature = CONTENT_SIGNATURES.get(extension)
    if signature is not None and not raw_bytes.startswith(signature):
        raise DocumentPreparationError(
            DocumentPreparationError.INVALID_DOCUMENT,
            "The stored file does not look like its declared format.",
        )

    if extension == ".pdf":
        return _prepare_pdf(raw_bytes, max_pages=max_pages)
    return (_prepare_image(raw_bytes),)


#: Backwards-compatible name used by the original image-only pipeline and its
#: tests. New code should call `prepare_vision_images`.
prepare_document = prepare_vision_images


def _prepare_pdf(raw_bytes: bytes, *, max_pages: int) -> tuple[ImageInput, ...]:
    if max_pages < 1:
        raise DocumentPreparationError(
            DocumentPreparationError.INVALID_CONFIGURATION,
            "PDF page processing is disabled by configuration.",
        )

    try:
        with pymupdf.open(stream=raw_bytes, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise DocumentPreparationError(
                    DocumentPreparationError.DOCUMENT_ENCRYPTED,
                    "The PDF is password-protected and cannot be read.",
                )
            page_count = pdf.page_count
            if page_count < 1:
                raise DocumentPreparationError(
                    DocumentPreparationError.INVALID_DOCUMENT,
                    "The PDF contains no pages.",
                )
            if page_count > max_pages:
                # Never truncate: the rest of the document might hold the
                # invoice total or a tax summary.
                raise DocumentPreparationError(
                    DocumentPreparationError.DOCUMENT_TOO_MANY_PAGES,
                    f"The PDF has {page_count} pages; the configured limit is "
                    f"{max_pages}. Split the document or raise INVOICE_MAX_PAGES.",
                )

            prepared: list[ImageInput] = []
            for page in pdf:
                pixmap = page.get_pixmap(dpi=PDF_RENDER_DPI, alpha=False)
                png = pixmap.tobytes("png")
                prepared.append(ImageInput(data=_to_jpeg(png), mime_type="image/jpeg"))
            return tuple(prepared)
    except DocumentPreparationError:
        raise
    except Exception as exc:
        # No document content or path in the log.
        logger.warning("Could not render PDF for extraction (%s)", type(exc).__name__)
        raise DocumentPreparationError(
            DocumentPreparationError.DOCUMENT_UNREADABLE,
            "The PDF could not be read.",
        ) from exc


def _prepare_image(raw_bytes: bytes) -> ImageInput:
    try:
        with Image.open(BytesIO(raw_bytes)) as image:
            if image.format not in {"JPEG", "PNG"}:
                raise DocumentPreparationError(
                    DocumentPreparationError.UNSUPPORTED_DOCUMENT_TYPE,
                    "Only JPEG and PNG images can be read.",
                )
            # Respect phone-camera orientation without altering the source.
            oriented = ImageOps.exif_transpose(image)
            data = _to_jpeg_bytes(oriented)
    except DocumentPreparationError:
        raise
    except Exception as exc:
        logger.warning("Could not read image for extraction (%s)", type(exc).__name__)
        raise DocumentPreparationError(
            DocumentPreparationError.DOCUMENT_UNREADABLE,
            "The image could not be read.",
        ) from exc
    return ImageInput(data=data, mime_type="image/jpeg")


def _to_jpeg(png_bytes: bytes) -> bytes:
    """Re-encode a rendered page as a bounded JPEG."""
    with Image.open(BytesIO(png_bytes)) as image:
        return _to_jpeg_bytes(image)


def _to_jpeg_bytes(image: Image.Image) -> bytes:
    """Normalise any supported image to a bounded, opaque JPEG.

    Transparency is flattened onto white, which is what a scanned page looks
    like. Images are only ever downscaled, never enlarged.
    """
    if image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        prepared = background
    else:
        prepared = image.convert("RGB")

    if max(prepared.size) > MAX_IMAGE_EDGE:
        prepared.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)

    buffer = BytesIO()
    prepared.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


__all__ = [
    "DetectedDocument",
    "DocumentKind",
    "DocumentPreparationError",
    "SUPPORTED_EXTENSIONS",
    "SUPPORTED_INVOICE_EXTENSIONS",
    "prepare_document",
    "prepare_vision_images",
    "prepare_vision_images_for_extension",
    "route_document",
]

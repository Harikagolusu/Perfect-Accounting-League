"""Invoice document preparation.

Turns one stored document into the image payload the AI provider accepts. This
module is deliberately separate from both storage and the provider:

* it reads bytes the caller already loaded from the existing document layer;
* it never writes to disk, so the original uploaded file is untouched;
* it returns plain image bytes plus a MIME type, not provider-specific objects.

PDFs are rendered locally to images because the vision path accepts images, not
PDF files. The configured page limit is enforced before any page is rendered:
an oversized PDF is reported as an explicit, controlled failure instead of
being silently truncated.

No OCR, no cloud call and no parsing of accounting data happen here.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from app.core.storage import CONTENT_SIGNATURES
from app.models.document import Document
from app.services.ai import ImageInput

logger = logging.getLogger(__name__)

#: Invoice documents this step can prepare. The inbox itself also accepts
#: spreadsheets, which are not invoice inputs and are rejected here.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".jpg", ".jpeg", ".png"})

#: Render resolution for PDF pages. 150 DPI keeps normal invoice text legible
#: while staying far below the size at which the payload becomes wasteful.
PDF_RENDER_DPI = 150
#: Longest edge kept for any image. Invoice text remains readable and the
#: base64 payload stays bounded.
MAX_IMAGE_EDGE = 2200
#: JPEG quality for the prepared image. Visually lossless for text at this DPI.
JPEG_QUALITY = 88


class DocumentPreparationError(Exception):
    """The document cannot be turned into provider-ready images.

    `code` is a stable machine-readable reason; `message` is safe to show a
    user. Neither contains a filesystem path.
    """

    #: Extension is not one of PDF/JPEG/PNG.
    UNSUPPORTED_DOCUMENT_TYPE = "UNSUPPORTED_DOCUMENT_TYPE"
    #: The file is empty or does not match its declared format.
    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    #: The file could not be parsed at all.
    DOCUMENT_UNREADABLE = "DOCUMENT_UNREADABLE"
    #: The PDF has more pages than the configured limit.
    DOCUMENT_TOO_MANY_PAGES = "DOCUMENT_TOO_MANY_PAGES"
    #: The PDF is password-protected.
    DOCUMENT_ENCRYPTED = "DOCUMENT_ENCRYPTED"
    #: The page limit is not usable (misconfigured).
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _document_extension(document: Document) -> str:
    """The stored file's extension, which the storage layer generated itself."""
    extension = Path(document.stored_filename).suffix.lower()
    if extension:
        return extension
    # Defensive fallback for a hand-written row; never used to build a path.
    return Path(document.original_filename).suffix.lower()


def prepare_document(
    document: Document,
    raw_bytes: bytes,
    *,
    max_pages: int,
) -> tuple[ImageInput, ...]:
    """Prepare a stored document for the vision provider.

    Raises `DocumentPreparationError` with a stable code on any unsupported or
    unreadable input. The original bytes are never modified.
    """
    extension = _document_extension(document)
    if extension not in SUPPORTED_EXTENSIONS:
        raise DocumentPreparationError(
            DocumentPreparationError.UNSUPPORTED_DOCUMENT_TYPE,
            "Only PDF, JPEG and PNG documents can be read for invoice extraction.",
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
    "DocumentPreparationError",
    "SUPPORTED_EXTENSIONS",
    "prepare_document",
]

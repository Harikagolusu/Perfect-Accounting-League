"""Local file storage for uploaded documents.

PAL runs inside the client's own environment, so documents stay on local disk
rather than in cloud object storage. This module owns everything
filesystem-related: the allowlist, content sniffing, safe filename generation,
and writing/removing files. It never touches the database.

Trust model
-----------
The client's filename, extension and declared MIME type are all untrusted
input. Three independent checks run before anything is written:

1. the extension must be on the allowlist;
2. the leading bytes must match the signature of that format;
3. the declared MIME type, if meaningful, must be from the same top-level
   family (application/* vs image/*).

The stored filename is a freshly generated UUID. The original name is kept
only as a display label in the database and is never used as a path.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Extension -> canonical MIME type.
ALLOWED_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Leading bytes that identify each format on disk.
CONTENT_SIGNATURES: dict[str, bytes] = {
    ".pdf": b"%PDF-",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".png": b"\x89PNG\r\n\x1a\n",
    # XLSX is a zip container.
    ".xlsx": b"PK\x03\x04",
    # Legacy XLS is an OLE2 compound file.
    ".xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    # DOCX is also a zip container; the extension disambiguates it from XLSX.
    ".docx": b"PK\x03\x04",
}

SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(sorted(ALLOWED_TYPES))

# Browsers and CLI clients frequently send a generic type for any binary
# file. It carries no information, so the extension and signature decide.
GENERIC_MIME_TYPES: frozenset[str] = frozenset(
    {"", "application/octet-stream", "binary/octet-stream"}
)


class UploadValidationError(Exception):
    """An upload is not acceptable.

    `status_code` maps straight onto the HTTP response so routers stay thin.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class StorageError(Exception):
    """The filesystem could not be read or written as required."""


@dataclass(frozen=True)
class ValidatedUpload:
    """An upload that passed every check, ready to be stored."""

    extension: str
    file_type: str
    size: int


def _extension_of(filename: str) -> str:
    """Lower-cased final suffix. Path separators are already handled by Path."""
    return Path(filename.strip()).suffix.lower()


def validate_upload(
    filename: str | None, content_type: str | None, data: bytes
) -> ValidatedUpload:
    """Validate an upload without writing anything.

    Raises UploadValidationError with an HTTP status on any failure.
    """
    if not filename or not filename.strip():
        raise UploadValidationError("A filename is required.")

    extension = _extension_of(filename)
    if extension not in ALLOWED_TYPES:
        allowed = ", ".join(SUPPORTED_EXTENSIONS)
        raise UploadValidationError(
            f"Unsupported file type '{extension or filename}'. Allowed: {allowed}.",
            status_code=415,
        )

    if not data:
        raise UploadValidationError("The uploaded file is empty.")

    max_bytes = get_settings().document_max_upload_bytes
    if len(data) > max_bytes:
        raise UploadValidationError(
            f"File is larger than the maximum upload size of {max_bytes} bytes.",
            status_code=413,
        )

    canonical_mime = ALLOWED_TYPES[extension]
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared not in GENERIC_MIME_TYPES:
        # Compare only the top-level family: clients disagree wildly about
        # subtypes, but "image/jpeg" for a PDF is still a clear mismatch.
        if declared.split("/")[0] != canonical_mime.split("/")[0]:
            raise UploadValidationError(
                f"Content type '{declared}' does not match a {extension} file.",
                status_code=415,
            )

    if not data.startswith(CONTENT_SIGNATURES[extension]):
        raise UploadValidationError(
            f"File contents do not look like a {extension} file.", status_code=415
        )

    return ValidatedUpload(extension=extension, file_type=canonical_mime, size=len(data))


def build_stored_filename(extension: str) -> str:
    """A collision-free name that reveals nothing about the client's file."""
    return f"{uuid.uuid4().hex}{extension}"


def company_directory(company_id: uuid.UUID) -> PurePosixPath:
    """Storage-relative directory holding one company's documents."""
    return PurePosixPath(str(company_id))


def resolve_storage_path(relative_path: str) -> Path:
    """Resolve a stored relative path, refusing anything outside the root.

    Stored paths are always POSIX-style so a database written on one platform
    stays readable on another. Relative paths are written by this module and
    read back from the database, but the guard means a tampered row still
    cannot reach the wider filesystem.
    """
    root = get_settings().document_storage_dir
    candidate = (root / Path(*PurePosixPath(relative_path).parts)).resolve()
    if candidate != root and root not in candidate.parents:
        raise StorageError("Refusing to resolve a path outside the storage root.")
    return candidate


def write_document(company_id: uuid.UUID, stored_filename: str, data: bytes) -> str:
    """Write bytes under the company's directory and return the stored path.

    The returned path is relative to the storage root, so the deployment can
    move without invalidating existing rows. Raises StorageError on failure.
    """
    relative = company_directory(company_id) / stored_filename
    destination = resolve_storage_path(relative.as_posix())
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # "xb" fails if the file already exists, so nothing can be overwritten.
        with destination.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        logger.error("Refusing to overwrite an existing document file")
        raise StorageError("Stored file already exists.") from None
    except OSError as exc:
        logger.error("Failed to store document: %s", exc)
        raise StorageError("Could not store the uploaded file.") from exc
    return relative.as_posix()


def delete_document(relative_path: str) -> bool:
    """Remove a stored file. Returns True if a file was actually removed.

    A missing file is not an error: the database row still needs cleaning up.
    """
    target = resolve_storage_path(relative_path)
    try:
        target.unlink()
    except FileNotFoundError:
        logger.warning("Document file was already missing on disk: %s", relative_path)
        return False
    except OSError as exc:
        logger.error("Failed to remove document file: %s", exc)
        raise StorageError("Could not remove the stored file.") from exc
    return True


def ensure_storage_root() -> Path:
    """Create the storage root if it does not exist yet."""
    root = get_settings().document_storage_dir
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("Could not create the document storage root: %s", exc)
        raise StorageError("Document storage is unavailable.") from exc
    return root
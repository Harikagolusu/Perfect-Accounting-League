"""Document business logic — the Document Inbox.

All database access for the Document entity lives here; routers stay thin.

Company scoping
---------------
Every read is filtered by `company_id`, and there is no lookup by document id
alone. Asking for a document that belongs to another company raises
`DocumentNotFoundError`, exactly as a missing one does.

Filesystem + database ordering
------------------------------
The file is written before the row, so a failure never leaves a row pointing
at nothing. If the row insert then fails, the file is removed again.
Deletion is best-effort on the filesystem: the database row is always removed,
and an undeletable file is logged rather than blocking the user.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import (
    build_stored_filename,
    delete_document as remove_stored_file,
    ensure_storage_root,
    validate_upload,
    write_document,
)
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.voucher import AccountingEntityType
from app.services.accounting_audit import record_event
from app.services.company import get_company

logger = logging.getLogger(__name__)


class DocumentNotFoundError(Exception):
    """Raised when a document id does not resolve within the given company."""


def list_documents(
    db: Session,
    company_id: uuid.UUID,
    *,
    status: DocumentStatus | None = None,
    document_type: DocumentType | None = None,
) -> list[Document]:
    """Return a company's documents, newest first, with optional filters."""
    stmt = (
        select(Document)
        .where(Document.company_id == company_id)
        .order_by(Document.created_at.desc(), Document.original_filename)
    )
    if status is not None:
        stmt = stmt.where(Document.status == status)
    if document_type is not None:
        stmt = stmt.where(Document.document_type == document_type)
    return list(db.scalars(stmt).all())


def get_document(db: Session, company_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    """Return a document only if it belongs to `company_id`."""
    document = db.scalar(
        select(Document).where(
            Document.id == document_id, Document.company_id == company_id
        )
    )
    if document is None:
        raise DocumentNotFoundError(f"{company_id}/{document_id}")
    return document


def create_document(
    db: Session,
    company_id: uuid.UUID,
    *,
    original_filename: str | None,
    content_type: str | None,
    data: bytes,
) -> Document:
    """Validate, store and record one uploaded file.

    Raises CompanyNotFoundError if the company does not exist, and
    UploadValidationError from `app.core.storage` if the file is unacceptable.
    """
    get_company(db, company_id)

    validated = validate_upload(original_filename, content_type, data)
    ensure_storage_root()

    stored_filename = build_stored_filename(validated.extension)
    relative_path = write_document(company_id, stored_filename, data)

    document = Document(
        company_id=company_id,
        original_filename=(original_filename or stored_filename).strip(),
        stored_filename=stored_filename,
        file_path=relative_path,
        file_type=validated.file_type,
        file_size=validated.size,
        document_type=DocumentType.UNKNOWN,
        status=DocumentStatus.UPLOADED,
    )
    try:
        db.add(document)
        record_event(
            db,
            company_id=company_id,
            entity_type=AccountingEntityType.DOCUMENT,
            entity_id=document.id,
            event_type="DOCUMENT_UPLOADED",
            new_value=document.original_filename,
            reason="Document uploaded to the inbox.",
        )
        db.commit()
    except Exception:
        db.rollback()
        # Never leave an orphaned file behind if the row could not be written.
        try:
            remove_stored_file(relative_path)
        except Exception:  # pragma: no cover - best effort cleanup
            logger.exception(
                "Failed to clean up file %s after a failed insert", relative_path
            )
        raise

    db.refresh(document)
    logger.info(
        "Document uploaded: company=%s document=%s type=%s size=%d",
        company_id,
        document.id,
        validated.file_type,
        validated.size,
    )
    return document


def delete_document(db: Session, company_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    """Delete a document and its stored file, scoped to the owning company."""
    document = get_document(db, company_id, document_id)
    relative_path = document.file_path
    removed = False

    try:
        removed = remove_stored_file(relative_path)
    except Exception:
        # The row still goes: leaving it would be more confusing than an
        # orphaned file that the logs already point at.
        logger.exception("Could not remove stored file for document %s", document_id)

    record_event(
        db,
        company_id=company_id,
        entity_type=AccountingEntityType.DOCUMENT,
        entity_id=document.id,
        event_type="DOCUMENT_DELETED",
        new_value=document.original_filename,
        reason="Document removed from the inbox.",
    )
    db.delete(document)
    db.commit()

    logger.info(
        "Document deleted: company=%s document=%s file_removed=%s",
        company_id,
        document_id,
        removed,
    )
    return document
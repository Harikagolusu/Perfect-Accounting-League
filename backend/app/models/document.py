"""Document ORM model — the raw uploaded-document record.

A Document holds metadata about a file that was uploaded into the inbox. It
deliberately carries no extracted or interpreted accounting data: invoice
numbers, GST amounts, OCR text and processing results belong to the future
processing models, not here.

Every document belongs to exactly one company, enforced by a NOT NULL foreign
key. Queries must always filter on `company_id`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocumentType(str, enum.Enum):
    """What kind of document this is.

    `UNKNOWN` is the only value the inbox assigns. Classification is a later
    processing step and is never guessed here.
    """

    UNKNOWN = "UNKNOWN"
    INVOICE = "INVOICE"
    BANK_STATEMENT = "BANK_STATEMENT"
    OTHER = "OTHER"


class DocumentStatus(str, enum.Enum):
    """Where a document sits in the processing pipeline.

    The inbox only ever sets `UPLOADED`. The remaining states exist so the
    pipeline has a destination, and no step fills them in yet.
    """

    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


DOCUMENT_TYPE_ENUM = SAEnum(
    DocumentType,
    name="document_type",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

DOCUMENT_STATUS_ENUM = SAEnum(
    DocumentStatus,
    name="document_status",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Document(Base):
    """One uploaded file belonging to one company."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # ON DELETE RESTRICT: documents outlive neither a hard-deleted company,
    # and companies are soft deleted anyway.
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Kept verbatim for display only. Never used to build a filesystem path.
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # Generated UUID-based name; the only name used on disk.
    stored_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # Storage-root-relative path. Internal: never returned by the API.
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    document_type: Mapped[DocumentType] = mapped_column(
        DOCUMENT_TYPE_ENUM,
        nullable=False,
        default=DocumentType.UNKNOWN,
        server_default=DocumentType.UNKNOWN.value,
    )
    status: Mapped[DocumentStatus] = mapped_column(
        DOCUMENT_STATUS_ENUM,
        nullable=False,
        default=DocumentStatus.UPLOADED,
        server_default=DocumentStatus.UPLOADED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<Document id={self.id} company_id={self.company_id} "
            f"name={self.original_filename!r} status={self.status.value}>"
        )
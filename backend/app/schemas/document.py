"""Pydantic v2 schemas for the Document Inbox.

Upload takes a multipart file rather than a JSON body, so there is no create
schema: the company comes from the URL path and the bytes come from the form.

`DocumentResponse` deliberately omits `file_path` and `stored_filename`. The
stored location is an internal implementation detail and must not be exposed
to clients.
"""

import uuid

from app.models.document import DocumentStatus, DocumentType
from app.schemas.common import TimestampedResponse


class DocumentResponse(TimestampedResponse):
    """Document metadata as returned by the API."""

    id: uuid.UUID
    company_id: uuid.UUID
    original_filename: str
    file_type: str
    file_size: int
    document_type: DocumentType
    status: DocumentStatus
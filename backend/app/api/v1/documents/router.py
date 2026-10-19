"""Document Inbox endpoints.

Thin HTTP layer: resolve the path resources, hand the upload to
`app.services.document`, and translate storage errors into safe responses. No
database queries and no filesystem access live here.

Upload errors never leak a server path: the client gets the validation reason
and nothing about where files are kept.
"""

from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.storage import StorageError, UploadValidationError
from app.models.document import Document, DocumentStatus, DocumentType
from app.schemas.document import DocumentResponse
from app.services.company import CompanyNotFoundError
from app.services.document import (
    DocumentNotFoundError,
    create_document,
    delete_document,
    get_document,
    list_documents,
)

router = APIRouter()


def document_or_404(
    company_id: UUID = Path(description="Owning company id."),
    document_id: UUID = Path(description="Document id, scoped to this company."),
    db: Session = Depends(get_db),
) -> Document:
    """Resolve the document within the company, or 404."""
    try:
        return get_document(db, company_id, document_id)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        ) from None


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document",
)
async def post_document(
    company_id: UUID = Path(description="Company the document belongs to."),
    file: UploadFile = File(description="PDF, JPEG, PNG, XLS or XLSX file."),
    db: Session = Depends(get_db),
) -> DocumentResponse:
    max_bytes = get_settings().document_max_upload_bytes
    # Reject on the declared size before buffering, so an oversized upload
    # never has to be read into memory.
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File is larger than the maximum upload size of {max_bytes} bytes.",
        )

    data = await file.read()

    try:
        return create_document(
            db,
            company_id,
            original_filename=file.filename,
            content_type=file.content_type,
            data=data,
        )
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The document could not be stored. Please try again.",
        ) from None


@router.get("", response_model=list[DocumentResponse], summary="List a company's documents")
def get_documents(
    company_id: UUID = Path(description="Company to scope the listing to."),
    status_filter: DocumentStatus | None = Query(
        default=None, alias="status", description="Filter by processing status."
    ),
    document_type: DocumentType | None = Query(
        default=None, description="Filter by document type."
    ),
    db: Session = Depends(get_db),
) -> list[DocumentResponse]:
    return list_documents(db, company_id, status=status_filter, document_type=document_type)


@router.get("/{document_id}", response_model=DocumentResponse, summary="Get a document")
def get_document_by_id(document: Document = Depends(document_or_404)) -> DocumentResponse:
    return document


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
)
def remove_document(
    document: Document = Depends(document_or_404), db: Session = Depends(get_db)
) -> Response:
    """Delete the stored file and the record. Scoped to the owning company."""
    try:
        delete_document(db, document.company_id, document.id)
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The document could not be fully removed.",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
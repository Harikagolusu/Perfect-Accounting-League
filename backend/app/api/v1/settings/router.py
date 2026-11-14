"""Company-scoped Settings API.

The Settings form persists as one JSON document per company. GET returns the
saved document (an empty one before the first save); PUT replaces it wholesale,
matching the form's "Save changes" button which submits the whole form state.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.api.v1.deps import company_or_404
from app.core.database import get_db
from app.schemas.company_settings import (
    CompanySettingsResponse,
    CompanySettingsUpdate,
)
from app.services.company_settings import (
    get_company_settings,
    save_company_settings,
)

router = APIRouter()


@router.get("", response_model=CompanySettingsResponse, summary="Get company settings")
def get_settings_endpoint(
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
    _company=Depends(company_or_404),
) -> CompanySettingsResponse:
    settings = get_company_settings(db, company_id)
    if settings is None:
        return CompanySettingsResponse(company_id=company_id, data={})
    return CompanySettingsResponse.model_validate(settings)


@router.put("", response_model=CompanySettingsResponse, summary="Save company settings")
def put_settings_endpoint(
    payload: CompanySettingsUpdate,
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
    _company=Depends(company_or_404),
) -> CompanySettingsResponse:
    settings = save_company_settings(db, company_id, payload.data)
    return CompanySettingsResponse.model_validate(settings)


__all__ = ["router"]

"""Small shared HTTP dependencies for accounting endpoints.

There is no authentication layer in Phase 1. The actor is a display name used
only for the audit trail; the domain does not pretend it is an authenticated
identity or a role.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Header, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.company import Company
from app.services.company import CompanyNotFoundError, get_company


def get_actor(
    x_pal_actor: str | None = Header(default=None, alias="X-PAL-Actor"),
) -> str:
    cleaned = (x_pal_actor or "").strip()
    return cleaned or "local-user"


def company_or_404(
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> Company:
    try:
        return get_company(db, company_id)
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        )


__all__ = ["company_or_404", "get_actor"]

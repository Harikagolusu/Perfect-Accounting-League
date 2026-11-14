"""Company settings read/write.

A company has at most one settings row. Reading a company that has never saved
settings returns None; the API turns that into an empty document so the UI
always has something to render. Saving upserts the single row.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company_settings import CompanySettings


def get_company_settings(
    db: Session, company_id: uuid.UUID
) -> CompanySettings | None:
    return db.scalar(
        select(CompanySettings).where(CompanySettings.company_id == company_id)
    )


def save_company_settings(
    db: Session, company_id: uuid.UUID, data: dict[str, Any]
) -> CompanySettings:
    settings = get_company_settings(db, company_id)
    if settings is None:
        settings = CompanySettings(company_id=company_id, data=data)
        db.add(settings)
    else:
        settings.data = data
    db.commit()
    db.refresh(settings)
    return settings


__all__ = ["get_company_settings", "save_company_settings"]

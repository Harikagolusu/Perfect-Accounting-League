"""Aggregate router for API v1.

Business modules are registered here as they are implemented. Each module lives
in its own package (`app.api.v1.<module>`) and exposes a router that is included
below, so `v1` stays a single, obvious entry point.

Company-owned modules are mounted under `/companies/{company_id}/...` so the
owning company is part of every address, not something each handler has to
remember.
"""

from fastapi import APIRouter

from app.api.v1.companies.router import router as companies_router
from app.api.v1.documents.router import router as documents_router
from app.api.v1.vendors.router import router as vendors_router
from app.core.config import get_settings
from app.schemas import HealthResponse

settings = get_settings()

api_router = APIRouter()


@api_router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Liveness probe.

    Deliberately does not touch the database, so it stays useful while
    PostgreSQL is down and can be used as a container/orchestrator probe.
    """
    return HealthResponse(status="ok", service=settings.app_name)


# ---------------------------------------------------------------------------
# Implemented modules
# ---------------------------------------------------------------------------
api_router.include_router(companies_router, prefix="/companies", tags=["companies"])
api_router.include_router(
    vendors_router, prefix="/companies/{company_id}/vendors", tags=["vendors"]
)
api_router.include_router(
    documents_router, prefix="/companies/{company_id}/documents", tags=["documents"]
)


# ---------------------------------------------------------------------------
# Future modules - all company-scoped, all mounted under /companies/{company_id}.
#
#   api_router.include_router(invoices.router,        prefix="/companies/{company_id}/invoices",        tags=["invoices"])
#   api_router.include_router(bank_statements.router, prefix="/companies/{company_id}/bank-statements", tags=["bank-statements"])
#   api_router.include_router(gst.router,             prefix="/companies/{company_id}/gst",             tags=["gst"])
#   api_router.include_router(tds.router,             prefix="/companies/{company_id}/tds",             tags=["tds"])
#   api_router.include_router(reconciliation.router,  prefix="/companies/{company_id}/reconciliation",  tags=["reconciliation"])
#
# Integration modules are company-wide rather than company-owned, so they stay
# at the top level:
#
#   api_router.include_router(tally.router,           prefix="/companies/{company_id}/tally",           tags=["tally"])
#   api_router.include_router(zoho.router,            prefix="/companies/{company_id}/zoho",            tags=["zoho"])
# ---------------------------------------------------------------------------
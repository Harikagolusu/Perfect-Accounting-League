"""Aggregate router for API v1.

Business modules are registered here as they are implemented. Each module lives
in its own package (`app.api.v1.<module>`) and exposes a router that is included
below, so `v1` stays a single, obvious entry point.
"""

from fastapi import APIRouter

from app.api.v1.companies.router import router as companies_router
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


# ---------------------------------------------------------------------------
# Future modules - registered as each feature is implemented.
#
#   api_router.include_router(documents.router,       prefix="/documents",       tags=["documents"])
#   api_router.include_router(invoices.router,        prefix="/invoices",        tags=["invoices"])
#   api_router.include_router(bank_statements.router, prefix="/bank-statements", tags=["bank-statements"])
#   api_router.include_router(vendors.router,         prefix="/vendors",         tags=["vendors"])
#   api_router.include_router(tally.router,           prefix="/tally",           tags=["tally"])
#   api_router.include_router(zoho.router,            prefix="/zoho",            tags=["zoho"])
#   api_router.include_router(gst.router,             prefix="/gst",             tags=["gst"])
#   api_router.include_router(tds.router,             prefix="/tds",             tags=["tds"])
#   api_router.include_router(reconciliation.router,  prefix="/reconciliation",  tags=["reconciliation"])
# ---------------------------------------------------------------------------
"""SQLAlchemy ORM models.

Every model is defined here and registered on `app.core.database.Base` so that
Alembic autogenerate picks up its table. `import app.models` in migration
environments is what makes new models visible to autogenerate.
"""

from app.models.company import Company, CompanyStatus
from app.models.vendor import Vendor, VendorStatus

__all__ = ["Company", "CompanyStatus", "Vendor", "VendorStatus"]
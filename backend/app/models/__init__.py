"""SQLAlchemy ORM models.

Every model is defined here and registered on `app.core.database.Base` so that
Alembic autogenerate picks up its table. `import app.models` in migration
environments is what makes new models visible to autogenerate.
"""

from app.models.bank import (
    BankAccount,
    BankStatement,
    BankStatementStatus,
    BankTransaction,
    TransactionType,
)
from app.models.company import Company, CompanyStatus
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.invoice import Invoice, InvoiceSource, InvoiceStatus
from app.models.vendor import Vendor, VendorStatus

__all__ = [
    "BankAccount",
    "BankStatement",
    "BankStatementStatus",
    "BankTransaction",
    "Company",
    "CompanyStatus",
    "Document",
    "DocumentStatus",
    "DocumentType",
    "Invoice",
    "InvoiceSource",
    "InvoiceStatus",
    "TransactionType",
    "Vendor",
    "VendorStatus",
]
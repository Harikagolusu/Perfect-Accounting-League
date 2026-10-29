"""SQLAlchemy ORM models.

Every model is defined here and registered on `app.core.database.Base` so that
Alembic autogenerate picks up its table. `import app.models` in migration
environments is what makes new models visible to autogenerate.
"""

from app.models.accounting_mapping import (
    AccountingMapping,
    AccountingMappingMatchType,
    AccountingMappingSourceType,
)
from app.models.bank import (
    BankAccount,
    BankStatement,
    BankStatementStatus,
    BankTransaction,
    TransactionType,
)
from app.models.company import Company, CompanyStatus
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.invoice import Invoice, InvoiceDirection, InvoiceSource, InvoiceStatus
from app.models.invoice_line_item import InvoiceLineItem
from app.models.ledger import Ledger, LedgerSource
from app.models.tally_sync import TallySyncLog
from app.models.vendor import Vendor, VendorStatus
from app.models.voucher import (
    AccountingAuditEvent,
    AccountingEntityType,
    Voucher,
    VoucherLine,
    VoucherSide,
    VoucherSourceType,
    VoucherStatus,
    VoucherType,
)

__all__ = [
    "AccountingAuditEvent",
    "AccountingEntityType",
    "AccountingMapping",
    "AccountingMappingMatchType",
    "AccountingMappingSourceType",
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
    "InvoiceDirection",
    "InvoiceLineItem",
    "InvoiceSource",
    "InvoiceStatus",
    "Ledger",
    "LedgerSource",
    "TallySyncLog",
    "TransactionType",
    "Vendor",
    "VendorStatus",
    "Voucher",
    "VoucherLine",
    "VoucherSide",
    "VoucherSourceType",
    "VoucherStatus",
    "VoucherType",
]
"""Pydantic v2 schemas used for request and response contracts."""

from pydantic import BaseModel

from app.schemas.bank import (
    BankAccountCreate,
    BankAccountResponse,
    BankAccountUpdate,
    BankStatementCreate,
    BankStatementResponse,
    BankStatementUpdate,
    BankTransactionCreate,
    BankTransactionResponse,
    ProcessStatementResponse,
)
from app.schemas.company import CompanyCreate, CompanyResponse, CompanyUpdate
from app.schemas.document import DocumentResponse
from app.schemas.invoice import (
    InvoiceCreate,
    InvoiceResponse,
    InvoiceUpdate,
    ProcessInvoiceResponse,
)
from app.schemas.vendor import VendorCreate, VendorResponse, VendorUpdate


class HealthResponse(BaseModel):
    """Payload returned by the liveness probe."""

    status: str
    service: str


__all__ = [
    "BankAccountCreate",
    "BankAccountResponse",
    "BankAccountUpdate",
    "BankStatementCreate",
    "BankStatementResponse",
    "BankStatementUpdate",
    "BankTransactionCreate",
    "BankTransactionResponse",
    "CompanyCreate",
    "CompanyResponse",
    "CompanyUpdate",
    "DocumentResponse",
    "HealthResponse",
    "InvoiceCreate",
    "InvoiceResponse",
    "InvoiceUpdate",
    "ProcessInvoiceResponse",
    "ProcessStatementResponse",
    "VendorCreate",
    "VendorResponse",
    "VendorUpdate",
]
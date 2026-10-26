"""Company ORM model.

The Company is the root scope for every company-owned record in PAL
(vendors, invoices, bank statements, GST, TDS, reconciliation). Those
relationships are deliberately not declared yet.

Status decision
---------------
A single `status` column replaces the earlier `is_active` boolean.

`is_active` could not represent "Onboarding", which the Companies & Clients UI
uses as a real lifecycle state. More importantly, keeping both would have left
two columns able to disagree about the same fact. `status` is therefore the
single source of truth, and soft delete is expressed as
`status = INACTIVE` rather than as a separate flag.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CompanyStatus(str, enum.Enum):
    """Lifecycle state of a company.

    `ACTIVE` and `ONBOARDING` are both live states. `INACTIVE` is the soft
    delete marker and is excluded from default listings.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ONBOARDING = "ONBOARDING"


COMPANY_STATUS_ENUM = SAEnum(
    CompanyStatus,
    name="company_status",
    # Persist the value ("ACTIVE"), not the member name, so the stored text
    # matches exactly what the API returns.
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Company(Base):
    """A client business whose accounting data PAL processes."""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255))
    gstin: Mapped[str | None] = mapped_column(String(15))
    pan: Mapped[str | None] = mapped_column(String(10))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(Text)
    # Ledger names used when a company's vouchers are generated. They stay
    # optional because a company may be onboarded before its Tally/Zoho chart
    # of accounts is confirmed; an export then reports a mapping exception
    # rather than inventing a ledger name.
    cgst_ledger: Mapped[str | None] = mapped_column(String(255))
    sgst_ledger: Mapped[str | None] = mapped_column(String(255))
    igst_ledger: Mapped[str | None] = mapped_column(String(255))
    cess_ledger: Mapped[str | None] = mapped_column(String(255))
    round_off_ledger: Mapped[str | None] = mapped_column(String(255))
    #: Sales/income ledger used by the sales-voucher flow. It stays optional:
    #: a missing ledger is a review exception, not a guessed account.
    sales_ledger: Mapped[str | None] = mapped_column(String(255))
    financial_year: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[CompanyStatus] = mapped_column(
        COMPANY_STATUS_ENUM,
        nullable=False,
        default=CompanyStatus.ACTIVE,
        server_default=CompanyStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # `onupdate` keeps updated_at fresh on every UPDATE, so PATCH and
    # soft-delete both bump it without the service doing anything.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r} status={self.status.value}>"
"""Vendor ORM model — the Vendor Master.

A vendor is always scoped to exactly one company: `company_id` is NOT NULL and
carries a real foreign key to `companies.id`. Every query in the service layer
filters on it, which is what makes multi-company isolation enforceable rather
than merely a UI convention.

Status decision
---------------
Mirrors `CompanyStatus`: an explicit enum instead of a boolean, because the
Vendor Master UI has a third, real state ("Needs Mapping") that no boolean can
represent. `INACTIVE` is the soft-delete marker and is excluded from default
listings.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VendorStatus(str, enum.Enum):
    """Lifecycle state of a vendor.

    `ACTIVE` and `NEEDS_MAPPING` are both live states; `NEEDS_MAPPING` means
    the vendor has no approved ledger mapping yet. `INACTIVE` is the soft
    delete marker.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    NEEDS_MAPPING = "NEEDS_MAPPING"


VENDOR_STATUS_ENUM = SAEnum(
    VendorStatus,
    name="vendor_status",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Vendor(Base):
    """A supplier belonging to exactly one company."""

    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # ON DELETE RESTRICT: a company must never be hard-deleted out from under
    # its vendors. Companies are soft-deleted (status = INACTIVE) instead.
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15))
    pan: Mapped[str | None] = mapped_column(String(10))
    default_expense_ledger: Mapped[str | None] = mapped_column(String(255))
    creditor_ledger: Mapped[str | None] = mapped_column(String(255))
    parent_group: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[VendorStatus] = mapped_column(
        VENDOR_STATUS_ENUM,
        nullable=False,
        default=VendorStatus.ACTIVE,
        server_default=VendorStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<Vendor id={self.id} company_id={self.company_id} "
            f"name={self.name!r} status={self.status.value}>"
        )
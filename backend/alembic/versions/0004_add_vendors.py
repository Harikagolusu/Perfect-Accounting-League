"""Add vendors table (Vendor Master).

Vendors are the first company-owned entity: every row carries a NOT NULL
company_id foreign key to companies.id with an index, so listings stay scoped
and cheap. ON DELETE RESTRICT guards the relationship; companies are soft
deleted rather than removed.

Revision ID: 0004_add_vendors
Revises: 0003_company_status
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_add_vendors"
down_revision: str | None = "0003_company_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VENDOR_STATUS_VALUES = ("ACTIVE", "INACTIVE", "NEEDS_MAPPING")


def upgrade() -> None:
    # The enum type is emitted by create_table itself; creating it separately
    # here would emit a second CREATE TYPE and fail.
    vendor_status = postgresql.ENUM(*VENDOR_STATUS_VALUES, name="vendor_status")

    op.create_table(
        "vendors",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("gstin", sa.String(length=15), nullable=True),
        sa.Column("pan", sa.String(length=10), nullable=True),
        sa.Column("default_expense_ledger", sa.String(length=255), nullable=True),
        sa.Column("creditor_ledger", sa.String(length=255), nullable=True),
        sa.Column("parent_group", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            vendor_status,
            server_default=sa.text("'ACTIVE'::vendor_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_vendors_company_id", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Every listing filters by company, so the index is load-bearing.
    op.create_index("ix_vendors_company_id", "vendors", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_vendors_company_id", table_name="vendors")
    op.drop_table("vendors")
    postgresql.ENUM(name="vendor_status").drop(op.get_bind(), checkfirst=True)
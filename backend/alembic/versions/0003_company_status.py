"""Replace companies.is_active with companies.status.

The boolean could not represent the "Onboarding" lifecycle state the UI uses,
and two columns able to describe the same fact can disagree. A single native
PostgreSQL enum replaces it, and soft delete becomes status = 'INACTIVE'.

Existing rows were all created under the old schema, where `is_active` defaults
to true, so they backfill to ACTIVE. Rows that had been soft-deleted
(is_active = false) backfill to INACTIVE to preserve their previous meaning.

Revision ID: 0003_company_status
Revises: 0002_add_companies
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_company_status"
down_revision: str | None = "0002_add_companies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMPANY_STATUS_VALUES = ("ACTIVE", "INACTIVE", "ONBOARDING")


def upgrade() -> None:
    company_status = postgresql.ENUM(
        *COMPANY_STATUS_VALUES, name="company_status"
    )
    # checkfirst: the type may already exist if this migration is re-run.
    company_status.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "companies",
        sa.Column("status", company_status, nullable=True),
    )

    # Backfill from the old flag so previously soft-deleted companies stay out
    # of default listings, then tighten the column.
    op.execute(
        "UPDATE companies SET status = 'INACTIVE' WHERE is_active IS FALSE"
    )
    op.execute("UPDATE companies SET status = 'ACTIVE' WHERE status IS NULL")
    op.alter_column(
        "companies",
        "status",
        nullable=False,
        server_default=sa.text("'ACTIVE'::company_status"),
    )

    op.drop_column("companies", "is_active")


def downgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.execute("UPDATE companies SET is_active = (status = 'ACTIVE')")
    op.drop_column("companies", "status")
    postgresql.ENUM(name="company_status").drop(op.get_bind(), checkfirst=True)
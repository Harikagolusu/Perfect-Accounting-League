"""Allow invoices to record AI extraction as their source (Step 8).

Adds the `AI` value to the existing `invoice_source` enum. No table or column
changes are made: Step 8 reuses the invoice fields created in Step 5.

Revision ID: 0008_add_invoice_source_ai
Revises: 0007_add_bank_statements
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_add_invoice_source_ai"
down_revision: str | None = "0007_add_bank_statements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL 12+ allows ADD VALUE inside a transaction, and the new value
    # is not used in this revision, so no autocommit block is required.
    op.execute("ALTER TYPE invoice_source ADD VALUE IF NOT EXISTS 'AI'")


def downgrade() -> None:
    """Rebuild the enum without AI.

    Rows already marked AI fall back to MANUAL: the value cannot exist once
    the type is rebuilt, and the manual value is the only remaining honest
    description of stored data.
    """
    op.execute("UPDATE invoices SET source = 'MANUAL' WHERE source = 'AI'")
    op.execute("ALTER TYPE invoice_source RENAME TO invoice_source_old")
    op.execute("CREATE TYPE invoice_source AS ENUM ('MANUAL')")
    op.execute("ALTER TABLE invoices ALTER COLUMN source DROP DEFAULT")
    op.execute(
        "ALTER TABLE invoices ALTER COLUMN source TYPE invoice_source "
        "USING source::text::invoice_source"
    )
    op.execute(
        "ALTER TABLE invoices ALTER COLUMN source "
        "SET DEFAULT 'MANUAL'::invoice_source"
    )
    op.execute("DROP TYPE invoice_source_old")

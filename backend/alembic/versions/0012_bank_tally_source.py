"""Record where a bank statement came from and the Tally voucher line key.

TallyPrime is a second source of bank data next to uploaded statements. Two
columns make that import idempotent and auditable:

* ``bank_statements.source`` distinguishes a Tally read from an upload or a
  hand-entered statement, so a sync finds the statement it created last time
  instead of appending to someone else's.
* ``bank_transactions.external_id`` holds the Tally voucher GUID plus the
  ledger-entry position. A unique constraint on
  (bank_statement_id, external_id) means a repeated sync can never duplicate a
  voucher line. PostgreSQL treats NULLs as distinct, so manual rows are not
  affected.

Revision ID: 0012_bank_tally_source
Revises: 0003_extraction_fields
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0012_bank_tally_source"
down_revision: str = "0003_extraction_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bank_statements",
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default="MANUAL",
        ),
    )
    op.add_column(
        "bank_transactions",
        sa.Column("external_id", sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint(
        "uq_bank_transactions_statement_external",
        "bank_transactions",
        ["bank_statement_id", "external_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_bank_transactions_statement_external",
        "bank_transactions",
        type_="unique",
    )
    op.drop_column("bank_transactions", "external_id")
    op.drop_column("bank_statements", "source")

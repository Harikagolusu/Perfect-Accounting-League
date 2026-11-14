"""Add invoices.exchange_rate for foreign-currency invoices.

Revision ID: 0020_invoice_exchange_rate
Revises: 0019_tag_bank_docs
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0020_invoice_exchange_rate"
down_revision: str = "0019_tag_bank_docs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("exchange_rate", sa.Numeric(18, 6), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "exchange_rate")

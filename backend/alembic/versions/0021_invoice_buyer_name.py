"""Add invoices.buyer_name (the printed bill-to name).

Revision ID: 0021_invoice_buyer_name
Revises: 0020_invoice_exchange_rate
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0021_invoice_buyer_name"
down_revision: str = "0020_invoice_exchange_rate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("buyer_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "buyer_name")

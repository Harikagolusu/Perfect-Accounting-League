"""Add unit_rate, discount_amount, freight_amount.

unit_rate on invoice_line_items: the printed price per unit (distinct from
gst_rate which is the GST percentage). Required for accurate line item
extraction since every invoice table shows a rate column.

discount_amount and freight_amount on invoices: header-level adjustments that
are printed separately from the taxable amount on many Indian tax invoices.
Without them, the accounting consistency check (taxable + tax + adj = total)
always fails for those documents, sending every such invoice to manual review.

Revision ID: 0003_extraction_fields
Revises: 0002_tally_sync_log
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0003_extraction_fields"
down_revision: str = "0002_tally_sync_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_line_items",
        sa.Column("unit_rate", sa.Numeric(15, 2), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("discount_amount", sa.Numeric(15, 2), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("freight_amount", sa.Numeric(15, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoices", "freight_amount")
    op.drop_column("invoices", "discount_amount")
    op.drop_column("invoice_line_items", "unit_rate")

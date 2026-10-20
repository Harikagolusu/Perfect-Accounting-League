"""Add deterministic vendor mapping state to invoices (Step 9).

Step 9 does not add a mapping-history table. The existing Vendor Master fields
(`parent_group`, `default_expense_ledger`, `creditor_ledger`) already hold the
historical grouping, and each invoice now records which vendor it was matched
to, how, and whether a person confirmed it.

`vendor_id` stays the only *assigned* vendor. `suggested_vendor_id` holds a
pending name-based suggestion so it can never be mistaken for an assignment.
`previous_vendor_id` keeps exactly one prior assignment for explicit
corrections, without introducing an approval workflow.

Revision ID: 0009_add_invoice_vendor_mapping
Revises: 0008_add_invoice_source_ai
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_add_invoice_vendor_mapping"
down_revision: str | None = "0008_add_invoice_source_ai"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAPPING_STATUS_VALUES = (
    "UNMAPPED",
    "SUGGESTED",
    "MATCHED",
    "CONFIRMED",
    "REJECTED",
    "AMBIGUOUS",
)
MATCH_METHOD_VALUES = ("EXACT_GSTIN", "EXACT_PAN", "NORMALIZED_NAME")


def upgrade() -> None:
    mapping_status = postgresql.ENUM(
        *MAPPING_STATUS_VALUES, name="invoice_vendor_mapping_status"
    )
    mapping_status.create(op.get_bind(), checkfirst=True)

    match_method = postgresql.ENUM(
        *MATCH_METHOD_VALUES, name="invoice_vendor_match_method"
    )
    match_method.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "invoices",
        sa.Column(
            "vendor_mapping_status",
            postgresql.ENUM(
                *MAPPING_STATUS_VALUES,
                name="invoice_vendor_mapping_status",
                create_type=False,
            ),
            server_default=sa.text(
                "'UNMAPPED'::invoice_vendor_mapping_status"
            ),
            nullable=False,
        ),
    )
    op.add_column(
        "invoices",
        sa.Column(
            "vendor_match_method",
            postgresql.ENUM(
                *MATCH_METHOD_VALUES,
                name="invoice_vendor_match_method",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "invoices",
        sa.Column("suggested_vendor_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("previous_vendor_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_invoices_suggested_vendor_id",
        "invoices",
        "vendors",
        ["suggested_vendor_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_invoices_previous_vendor_id",
        "invoices",
        "vendors",
        ["previous_vendor_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_invoices_suggested_vendor_id",
        "invoices",
        ["suggested_vendor_id"],
        unique=False,
    )

    # Existing invoices already linked to a vendor were either manual choices
    # or pre-Step-9 matches. CONFIRMED is the only honest description: the
    # system cannot prove which method produced them.
    op.execute(
        "UPDATE invoices SET vendor_mapping_status = 'CONFIRMED' "
        "WHERE vendor_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_invoices_suggested_vendor_id", table_name="invoices")
    op.drop_constraint(
        "fk_invoices_previous_vendor_id", "invoices", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_invoices_suggested_vendor_id", "invoices", type_="foreignkey"
    )
    op.drop_column("invoices", "previous_vendor_id")
    op.drop_column("invoices", "suggested_vendor_id")
    op.drop_column("invoices", "vendor_match_method")
    op.drop_column("invoices", "vendor_mapping_status")

    postgresql.ENUM(name="invoice_vendor_match_method").drop(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(name="invoice_vendor_mapping_status").drop(
        op.get_bind(), checkfirst=True
    )

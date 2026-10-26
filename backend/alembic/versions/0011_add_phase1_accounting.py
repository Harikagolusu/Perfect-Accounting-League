"""Add the Phase 1 accounting workflow.

This migration adds only the canonical records the client's Phase 1 process
requires:

* company-scoped ledger master;
* traceable accounting mappings learned from approved postings/rules;
* draft vouchers with multiple ledger lines;
* append-only accounting audit events;
* the small canonical fields needed by the sales and bank-contra flows.

Existing data is preserved. New columns are nullable or have a non-destructive
server default. No existing extraction/export table is changed.

Revision ID: 0011_add_phase1_accounting
Revises: 0010_add_client_output_fields
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_add_phase1_accounting"
down_revision: str | None = "0010_add_client_output_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEDGER_SOURCE_VALUES = ("MANUAL", "IMPORT", "TALLY", "ZOHO")
MAPPING_SOURCE_VALUES = ("INVOICE", "BANK_TRANSACTION")
MAPPING_MATCH_VALUES = ("VENDOR", "COUNTERPARTY", "NARRATION", "RULE")
VOUCHER_STATUS_VALUES = (
    "DRAFT",
    "PENDING_APPROVAL",
    "REJECTED",
    "APPROVED",
    "POSTING_IN_PROGRESS",
    "POSTED",
    "POSTING_FAILED",
    "AWAITING_IMPORT_CONFIRMATION",
    "NEEDS_REVIEW",
)
VOUCHER_TYPE_VALUES = (
    "PURCHASE",
    "SALES",
    "RECEIPT",
    "PAYMENT",
    "CONTRA",
    "JOURNAL",
)
VOUCHER_SOURCE_VALUES = ("INVOICE", "BANK_TRANSACTION", "MANUAL")
VOUCHER_SIDE_VALUES = ("Dr", "Cr")
ACCOUNTING_ENTITY_VALUES = ("VOUCHER", "LEDGER", "MAPPING")
INVOICE_DIRECTION_VALUES = ("PURCHASE", "SALES")


def _enum(name: str, values: tuple[str, ...]) -> postgresql.ENUM:
    enum = postgresql.ENUM(*values, name=name)
    enum.create(op.get_bind(), checkfirst=True)
    return enum


def upgrade() -> None:
    ledger_source = _enum("ledger_source", LEDGER_SOURCE_VALUES)
    mapping_source = _enum(
        "accounting_mapping_source_type", MAPPING_SOURCE_VALUES
    )
    mapping_match = _enum("accounting_mapping_match_type", MAPPING_MATCH_VALUES)
    voucher_status = _enum("voucher_status", VOUCHER_STATUS_VALUES)
    voucher_type = _enum("voucher_type", VOUCHER_TYPE_VALUES)
    voucher_source = _enum("voucher_source_type", VOUCHER_SOURCE_VALUES)
    voucher_side = _enum("voucher_side", VOUCHER_SIDE_VALUES)
    accounting_entity = _enum("accounting_entity_type", ACCOUNTING_ENTITY_VALUES)
    invoice_direction = _enum("invoice_direction", INVOICE_DIRECTION_VALUES)

    # --- Existing tables: canonical fields for the new flows -----------------
    op.add_column(
        "companies", sa.Column("sales_ledger", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "invoices",
        sa.Column(
            "direction",
            postgresql.ENUM(
                *INVOICE_DIRECTION_VALUES,
                name="invoice_direction",
                create_type=False,
            ),
            server_default=sa.text("'PURCHASE'::invoice_direction"),
            nullable=False,
        ),
    )
    op.add_column(
        "bank_transactions",
        sa.Column(
            "is_inter_account_transfer",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # --- Ledger master -------------------------------------------------------
    op.create_table(
        "ledgers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("ledger_type", sa.String(length=64), nullable=True),
        sa.Column("parent_group", sa.String(length=255), nullable=True),
        sa.Column("tax_classification", sa.String(length=64), nullable=True),
        sa.Column(
            "source",
            postgresql.ENUM(
                *LEDGER_SOURCE_VALUES, name="ledger_source", create_type=False
            ),
            server_default=sa.text("'MANUAL'::ledger_source"),
            nullable=False,
        ),
        sa.Column("external_tally_id", sa.String(length=255), nullable=True),
        sa.Column("external_zoho_id", sa.String(length=255), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
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
            ["company_id"],
            ["companies.id"],
            name="fk_ledgers_company_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "name", name="uq_ledgers_company_name"),
    )
    op.create_index("ix_ledgers_company_id", "ledgers", ["company_id"], unique=False)

    # --- Accounting mappings -------------------------------------------------
    op.create_table(
        "accounting_mappings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column(
            "source_type",
            postgresql.ENUM(
                *MAPPING_SOURCE_VALUES,
                name="accounting_mapping_source_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "match_type",
            postgresql.ENUM(
                *MAPPING_MATCH_VALUES,
                name="accounting_mapping_match_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("vendor_id", sa.UUID(), nullable=True),
        sa.Column("counterparty_name", sa.String(length=255), nullable=True),
        sa.Column("narration_pattern", sa.String(length=512), nullable=True),
        sa.Column("transaction_context", sa.String(length=64), nullable=True),
        sa.Column("voucher_type", sa.String(length=32), nullable=True),
        sa.Column("ledger_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
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
            ["company_id"],
            ["companies.id"],
            name="fk_accounting_mappings_company_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name="fk_accounting_mappings_vendor_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ledger_id"],
            ["ledgers.id"],
            name="fk_accounting_mappings_ledger_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_accounting_mappings_company_id",
        "accounting_mappings",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_accounting_mappings_vendor_id",
        "accounting_mappings",
        ["vendor_id"],
        unique=False,
    )
    op.create_index(
        "ix_accounting_mappings_ledger_id",
        "accounting_mappings",
        ["ledger_id"],
        unique=False,
    )

    # --- Vouchers ------------------------------------------------------------
    op.create_table(
        "vouchers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("voucher_date", sa.Date(), nullable=False),
        sa.Column(
            "voucher_type",
            postgresql.ENUM(
                *VOUCHER_TYPE_VALUES, name="voucher_type", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("voucher_number", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                *VOUCHER_STATUS_VALUES, name="voucher_status", create_type=False
            ),
            server_default=sa.text("'DRAFT'::voucher_status"),
            nullable=False,
        ),
        sa.Column("narration", sa.Text(), nullable=True),
        sa.Column(
            "source_type",
            postgresql.ENUM(
                *VOUCHER_SOURCE_VALUES, name="voucher_source_type", create_type=False
            ),
            server_default=sa.text("'MANUAL'::voucher_source_type"),
            nullable=False,
        ),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("source_document_id", sa.UUID(), nullable=True),
        sa.Column(
            "currency", sa.String(length=3), server_default="INR", nullable=False
        ),
        sa.Column(
            "created_by",
            sa.String(length=255),
            server_default="local-user",
            nullable=False,
        ),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.String(length=255), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("posting_reference", sa.String(length=255), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posting_error", sa.Text(), nullable=True),
        sa.Column("external_tally_voucher_id", sa.String(length=255), nullable=True),
        sa.Column("external_zoho_voucher_id", sa.String(length=255), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exported_by", sa.String(length=255), nullable=True),
        sa.Column(
            "export_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("validation_issues", sa.JSON(), nullable=True),
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
            ["company_id"],
            ["companies.id"],
            name="fk_vouchers_company_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["documents.id"],
            name="fk_vouchers_source_document_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "source_type", "source_id", name="uq_vouchers_company_source"
        ),
        sa.UniqueConstraint(
            "company_id",
            "voucher_type",
            "voucher_number",
            name="uq_vouchers_company_type_number",
        ),
    )
    for column in ("company_id", "voucher_date", "voucher_type", "status", "source_type", "source_id", "source_document_id"):
        op.create_index(f"ix_vouchers_{column}", "vouchers", [column], unique=False)

    op.create_table(
        "voucher_lines",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("voucher_id", sa.UUID(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("ledger_id", sa.UUID(), nullable=True),
        sa.Column(
            "ledger_name", sa.String(length=255), server_default="", nullable=False
        ),
        sa.Column(
            "side",
            postgresql.ENUM(*VOUCHER_SIDE_VALUES, name="voucher_side", create_type=False),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("narration", sa.Text(), nullable=True),
        sa.Column(
            "line_type", sa.String(length=32), server_default="LEDGER", nullable=False
        ),
        sa.Column("item_name", sa.String(length=255), nullable=True),
        sa.Column("hsn_sac", sa.String(length=16), nullable=True),
        sa.Column("billed_quantity", sa.Numeric(15, 3), nullable=True),
        sa.Column("item_rate", sa.Numeric(15, 2), nullable=True),
        sa.Column("item_rate_per", sa.String(length=16), nullable=True),
        sa.Column("item_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("tax_type", sa.String(length=32), nullable=True),
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
            ["company_id"],
            ["companies.id"],
            name="fk_voucher_lines_company_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voucher_id"],
            ["vouchers.id"],
            name="fk_voucher_lines_voucher_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ledger_id"],
            ["ledgers.id"],
            name="fk_voucher_lines_ledger_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "voucher_id", "line_number", name="uq_voucher_lines_voucher_line_number"
        ),
    )
    for column in ("company_id", "voucher_id", "ledger_id"):
        op.create_index(
            f"ix_voucher_lines_{column}", "voucher_lines", [column], unique=False
        )

    # --- Append-only accounting audit trail ---------------------------------
    op.create_table(
        "accounting_audit_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column(
            "entity_type",
            postgresql.ENUM(
                *ACCOUNTING_ENTITY_VALUES,
                name="accounting_entity_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("voucher_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "actor",
            sa.String(length=255),
            server_default="local-user",
            nullable=False,
        ),
        sa.Column("previous_status", sa.String(length=64), nullable=True),
        sa.Column("new_status", sa.String(length=64), nullable=True),
        sa.Column("field", sa.String(length=255), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("event_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_accounting_audit_events_company_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voucher_id"],
            ["vouchers.id"],
            name="fk_accounting_audit_events_voucher_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("company_id", "entity_type", "entity_id", "voucher_id", "event_type", "created_at"):
        op.create_index(
            f"ix_accounting_audit_events_{column}",
            "accounting_audit_events",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("accounting_audit_events")
    op.drop_table("voucher_lines")
    op.drop_table("vouchers")
    op.drop_table("accounting_mappings")
    op.drop_table("ledgers")

    op.drop_column("bank_transactions", "is_inter_account_transfer")
    op.drop_column("invoices", "direction")
    op.drop_column("companies", "sales_ledger")

    for name in (
        "accounting_entity_type",
        "voucher_side",
        "voucher_source_type",
        "voucher_type",
        "voucher_status",
        "accounting_mapping_match_type",
        "accounting_mapping_source_type",
        "ledger_source",
        "invoice_direction",
    ):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)

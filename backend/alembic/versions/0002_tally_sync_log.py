"""Add tally_sync_log table.

A new table is required because the existing schema has no place to store sync
state (last sync time, per-run counts, error messages). The accounting_audit
table is append-only and scoped to accounting entity changes; mixing integration
sync metadata into it would break its purpose.

Revision ID: 0002_tally_sync_log
Revises: 0001_initial_schema
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_tally_sync_log"
down_revision: str = "0011_add_phase1_accounting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tally_sync_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("tally_company_name", sa.String(255), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ledgers_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ledgers_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ledgers_updated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ledgers_unchanged", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "triggered_by",
            sa.String(255),
            nullable=False,
            server_default="local-user",
        ),
    )


def downgrade() -> None:
    op.drop_table("tally_sync_log")

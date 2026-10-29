"""Extend the accounting audit entity vocabulary.

The company-wide activity trail now records invoice, vendor and document
actions alongside voucher/ledger/mapping events, so the Audit Trail survives a
restart instead of living in browser memory. This adds the three new values to
the ``accounting_entity_type`` enum.

PostgreSQL cannot both add an enum value and use it in the same transaction, so
the ALTERs run in an autocommit block. ``IF NOT EXISTS`` keeps the migration
idempotent.

Revision ID: 0013_add_audit_entity_types
Revises: 0012_bank_tally_source
"""

from alembic import op

revision: str = "0013_add_audit_entity_types"
down_revision: str = "0012_bank_tally_source"
branch_labels = None
depends_on = None

_NEW_VALUES = ("INVOICE", "VENDOR", "DOCUMENT")


def upgrade() -> None:
    # ADD VALUE cannot run inside a transaction block.
    with op.get_context().autocommit_block():
        for value in _NEW_VALUES:
            op.execute(
                f"ALTER TYPE accounting_entity_type ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    # PostgreSQL has no DROP VALUE. Removing an enum member would require
    # rebuilding the type, which is unsafe while audit rows reference it, so
    # the downgrade is intentionally a no-op.
    pass

"""Let a reviewer park a bank transaction for client clarification.

Two columns on ``bank_transactions`` carry a review annotation that never
touches the bank facts:

* ``needs_clarification`` flags the row as waiting on the client;
* ``clarification_note`` holds the reviewer's question.

Both are review assertions, so neither is ever inferred from narration.

Revision ID: 0014_bank_clarification
Revises: 0013_add_audit_entity_types
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0014_bank_clarification"
down_revision: str = "0013_add_audit_entity_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bank_transactions",
        sa.Column(
            "needs_clarification",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "bank_transactions",
        sa.Column("clarification_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bank_transactions", "clarification_note")
    op.drop_column("bank_transactions", "needs_clarification")

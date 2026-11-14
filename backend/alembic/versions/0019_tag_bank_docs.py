"""Tag files behind bank statements as BANK_STATEMENT.

They were stored as UNKNOWN, so the invoice Document Inbox listed them.

Revision ID: 0019_tag_bank_docs
Revises: 0018_add_app_secrets
"""

from alembic import op

revision: str = "0019_tag_bank_docs"
down_revision: str = "0018_add_app_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE documents SET document_type = 'BANK_STATEMENT' "
        "WHERE id IN (SELECT document_id FROM bank_statements WHERE document_id IS NOT NULL)"
    )


def downgrade() -> None:
    # Data-only tag; UNKNOWN was the previous value for every one of them.
    op.execute(
        "UPDATE documents SET document_type = 'UNKNOWN' "
        "WHERE document_type = 'BANK_STATEMENT'"
    )

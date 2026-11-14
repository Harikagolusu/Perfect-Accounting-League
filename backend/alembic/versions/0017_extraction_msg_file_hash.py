"""Keep the reason an extraction failed or needs review, and each file's hash.

* ``invoices.extraction_message`` / ``bank_statements.extraction_message``: the
  plain-language reason, so the inbox can still show it after a refresh.
* ``documents.content_sha256``: spots the same file uploaded twice.

Revision ID: 0017_extraction_msg_file_hash
Revises: 0016_add_company_settings
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0017_extraction_msg_file_hash"
down_revision: str = "0016_add_company_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("extraction_message", sa.Text(), nullable=True))
    op.add_column(
        "bank_statements", sa.Column("extraction_message", sa.Text(), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("content_sha256", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "ix_documents_content_sha256", "documents", ["content_sha256"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_documents_content_sha256", table_name="documents")
    op.drop_column("documents", "content_sha256")
    op.drop_column("bank_statements", "extraction_message")
    op.drop_column("invoices", "extraction_message")

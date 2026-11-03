"""Tag each uploaded document with its upload batch code.

``documents.batch_code`` is a short shared code for all files uploaded together
in one batch, so the inbox can be filtered to a single upload. Nullable: rows
created before this migration have no batch.

Revision ID: 0015_document_batch_code
Revises: 0014_bank_clarification
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0015_document_batch_code"
down_revision: str = "0014_bank_clarification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("batch_code", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_documents_batch_code", "documents", ["batch_code"]
    )


def downgrade() -> None:
    op.drop_index("ix_documents_batch_code", table_name="documents")
    op.drop_column("documents", "batch_code")

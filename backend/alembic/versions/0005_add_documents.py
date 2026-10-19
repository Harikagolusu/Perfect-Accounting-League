"""Add documents table (Document Inbox).

Documents are the raw uploaded-file record: metadata in PostgreSQL plus the
bytes on local disk. The company foreign key keeps the inbox isolated per
client, and ON DELETE RESTRICT preserves that relationship.

Revision ID: 0005_add_documents
Revises: 0004_add_vendors
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_add_documents"
down_revision: str | None = "0004_add_vendors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENT_TYPE_VALUES = ("UNKNOWN", "INVOICE", "BANK_STATEMENT", "OTHER")
DOCUMENT_STATUS_VALUES = ("UPLOADED", "PROCESSING", "PROCESSED", "FAILED")


def upgrade() -> None:
    # The enum types are emitted by create_table itself; creating them
    # separately here would emit a second CREATE TYPE and fail.
    document_type = postgresql.ENUM(*DOCUMENT_TYPE_VALUES, name="document_type")
    document_status = postgresql.ENUM(*DOCUMENT_STATUS_VALUES, name="document_status")

    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("stored_filename", sa.String(length=512), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("file_type", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column(
            "document_type",
            document_type,
            server_default=sa.text("'UNKNOWN'::document_type"),
            nullable=False,
        ),
        sa.Column(
            "status",
            document_status,
            server_default=sa.text("'UPLOADED'::document_status"),
            nullable=False,
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
            name="fk_documents_company_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Every inbox listing filters by company.
    op.create_index("ix_documents_company_id", "documents", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_documents_company_id", table_name="documents")
    op.drop_table("documents")
    postgresql.ENUM(name="document_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="document_type").drop(op.get_bind(), checkfirst=True)
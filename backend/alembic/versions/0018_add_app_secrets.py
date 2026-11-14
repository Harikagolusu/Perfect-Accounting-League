"""Add the encrypted app_secrets table (DeepSeek key saved from Settings).

Revision ID: 0018_add_app_secrets
Revises: 0017_extraction_msg_file_hash
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0018_add_app_secrets"
down_revision: str = "0017_extraction_msg_file_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_secrets",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("app_secrets")

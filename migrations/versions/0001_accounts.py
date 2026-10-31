"""create accounts table

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("customer_id", sa.String(), nullable=False),
        sa.Column("balance_piastres", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(), nullable=False, server_default="SAR"),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
    )
    op.create_index("ix_accounts_customer_id", "accounts", ["customer_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_accounts_customer_id", table_name="accounts")
    op.drop_table("accounts")

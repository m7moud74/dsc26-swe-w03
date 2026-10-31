"""create transfers and idempotency records

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transfers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("from_account_id", sa.String(), nullable=False),
        sa.Column("to_account_id", sa.String(), nullable=False),
        sa.Column("amount_piastres", sa.Integer(), nullable=False),
        sa.Column("memo", sa.String(length=140), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="completed"),
        sa.CheckConstraint("amount_piastres > 0", name="ck_transfers_amount_piastres_positive"),
        sa.ForeignKeyConstraint(["from_account_id"], ["accounts.id"], name="fk_transfers_from_account_id"),
        sa.ForeignKeyConstraint(["to_account_id"], ["accounts.id"], name="fk_transfers_to_account_id"),
        sa.PrimaryKeyConstraint("id", name="pk_transfers"),
    )
    op.create_index("ix_transfers_from_account_id", "transfers", ["from_account_id"], unique=False)
    op.create_index("ix_transfers_to_account_id", "transfers", ["to_account_id"], unique=False)

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("customer_id", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_records"),
        sa.UniqueConstraint("customer_id", "key", name="uq_idempotency_customer_key"),
    )
    op.create_index("ix_idempotency_records_customer_id", "idempotency_records", ["customer_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_idempotency_records_customer_id", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_index("ix_transfers_to_account_id", table_name="transfers")
    op.drop_index("ix_transfers_from_account_id", table_name="transfers")
    op.drop_table("transfers")

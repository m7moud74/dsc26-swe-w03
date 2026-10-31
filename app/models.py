from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id = Column(String, primary_key=True)
    customer_id = Column(String, nullable=False, index=True)
    balance_piastres = Column(Integer, nullable=False, default=0)
    currency = Column(String, nullable=False, default="SAR")


class Transfer(Base):
    __tablename__ = "transfers"

    id = Column(String, primary_key=True)
    from_account_id = Column(
        String, ForeignKey("accounts.id", name="fk_transfers_from_account_id"), nullable=False, index=True
    )
    to_account_id = Column(
        String, ForeignKey("accounts.id", name="fk_transfers_to_account_id"), nullable=False, index=True
    )
    amount_piastres = Column(Integer, nullable=False)
    memo = Column(String(140), nullable=True)
    status = Column(String, nullable=False, default="completed")

    __table_args__ = (
        CheckConstraint("amount_piastres > 0", name="ck_transfers_amount_piastres_positive"),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(String, nullable=False, index=True)
    key = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    response_status = Column(Integer, nullable=False)
    response_body = Column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint("customer_id", "key", name="uq_idempotency_customer_key"),
    )

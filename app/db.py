from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def make_engine(database_url: str):
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args, future=True)


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.db import make_engine, make_session_factory
from app.main import create_app
from app.models import Base
from app.settings import Settings


def make_settings(db_path=None, **overrides):
    if db_path is None:
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(db_path)
    url = f"sqlite:///{db_path}"
    defaults = dict(
        database_url=url,
        api_tokens={
            "token-alice": "alice",
            "token-bob": "bob",
            "token-carol": "carol",
        },
        max_transfer_piastres=1000,
        rate_limit_capacity=5,
        rate_limit_refill_per_second=1.0,
    )
    defaults.update(overrides)
    # clock default
    if "clock" not in defaults:
        import time

        defaults["clock"] = time.monotonic
    settings = Settings(**defaults)
    return settings, db_path


@pytest.fixture
def settings(tmp_path):
    db_path = str(tmp_path / "test.db")
    url = f"sqlite:///{db_path}"
    settings = Settings(
        database_url=url,
        api_tokens={"token-alice": "alice", "token-bob": "bob", "token-carol": "carol"},
        max_transfer_piastres=1000,
        rate_limit_capacity=1000,
        rate_limit_refill_per_second=1.0,
        clock=__import__("time").monotonic,
    )
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return settings


@pytest.fixture
def app(settings):
    application = create_app(settings)
    # create tables
    engine = application.state.engine
    Base.metadata.create_all(engine)
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c

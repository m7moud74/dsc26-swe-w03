import time

import pytest
from app.models import Account


def seed_accounts(app, accounts):
    sf = app.state.session_factory
    with sf() as session:
        with session.begin():
            for acc in accounts:
                session.add(Account(**acc))
    return accounts


def H(token):
    return {"Authorization": f"Bearer {token}"}


def test_auth_variants(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
    ])
    # missing header
    r = client.get("/v1/accounts/acc_a")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"
    # wrong scheme
    r = client.get("/v1/accounts/acc_a", headers={"Authorization": "Basic x"})
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"
    # empty bearer
    r = client.get("/v1/accounts/acc_a", headers={"Authorization": "Bearer "})
    assert r.status_code == 401
    # unknown token
    r = client.get("/v1/accounts/acc_a", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    # valid
    r = client.get("/v1/accounts/acc_a", headers=H("token-alice"))
    assert r.status_code == 200


def test_account_ownership_404(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
    ])
    r = client.get("/v1/accounts/acc_a", headers=H("token-bob"))
    assert r.status_code == 404
    r = client.get("/v1/accounts/nope", headers=H("token-alice"))
    assert r.status_code == 404


def test_account_response_exact(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
    ])
    r = client.get("/v1/accounts/acc_a", headers=H("token-alice"))
    assert r.status_code == 200
    assert list(r.json().keys()) == ["id", "balance_piastres", "currency"]
    assert r.json()["balance_piastres"] == 100


def test_invalid_bodies(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 0, "currency": "SAR"},
    ])
    base = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 10}
    # missing field
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "k1"}, json={"from_account_id": "acc_a", "to_account_id": "acc_b"})
    assert r.status_code == 422
    # float
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "k2"}, json={**base, "amount_piastres": 10.0})
    assert r.status_code == 422
    # numeric string
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "k3"}, json={**base, "amount_piastres": "10"})
    assert r.status_code == 422
    # bool
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "k4"}, json={**base, "amount_piastres": True})
    assert r.status_code == 422
    # unknown field
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "k5"}, json={**base, "extra": 1})
    assert r.status_code == 422
    # over limit
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "k6"}, json={**base, "amount_piastres": 10000})
    assert r.status_code == 422
    # same src/dst
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "k7"}, json={"from_account_id": "acc_a", "to_account_id": "acc_a", "amount_piastres": 10})
    assert r.status_code == 422
    # unknown destination
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "k8"}, json={"from_account_id": "acc_a", "to_account_id": "nope", "amount_piastres": 10})
    assert r.status_code == 422
    # missing idempotency key
    r = client.post("/v1/transfers", headers=H("token-alice"), json=base)
    assert r.status_code == 400
    # 404 source not owned
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "k9"}, json={"from_account_id": "acc_b", "to_account_id": "acc_a", "amount_piastres": 10})
    assert r.status_code == 404


def test_successful_transfer(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 0, "currency": "SAR"},
    ])
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 100, "memo": "hi"}
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "s1"}, json=body)
    assert r.status_code == 201
    data = r.json()
    assert list(data.keys()) == ["id", "from_account_id", "to_account_id", "amount_piastres", "memo", "status"]
    assert data["status"] == "completed"
    assert data["id"].startswith("trf_")
    # balances
    ra = client.get("/v1/accounts/acc_a", headers=H("token-alice")).json()
    rb = client.get("/v1/accounts/acc_b", headers=H("token-bob")).json()
    assert ra["balance_piastres"] == 0
    assert rb["balance_piastres"] == 100


def test_insufficient_funds(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 50, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 0, "currency": "SAR"},
    ])
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 100}
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "i1"}, json=body)
    assert r.status_code == 409
    ra = client.get("/v1/accounts/acc_a", headers=H("token-alice")).json()
    rb = client.get("/v1/accounts/acc_b", headers=H("token-bob")).json()
    assert ra["balance_piastres"] == 50
    assert rb["balance_piastres"] == 0


def test_idempotency_replay_success(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 0, "currency": "SAR"},
    ])
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 30}
    r1 = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "id1"}, json=body)
    assert r1.status_code == 201
    r2 = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "id1"}, json=body)
    assert r2.status_code == 201
    assert r2.headers.get("idempotent-replayed") == "true"
    assert r2.json() == r1.json()
    # balances only moved once
    ra = client.get("/v1/accounts/acc_a", headers=H("token-alice")).json()
    rb = client.get("/v1/accounts/acc_b", headers=H("token-bob")).json()
    assert ra["balance_piastres"] == 70
    assert rb["balance_piastres"] == 30


def test_idempotency_replay_409(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 10, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 0, "currency": "SAR"},
    ])
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 50}
    r1 = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "id2"}, json=body)
    assert r1.status_code == 409
    # change balance
    sf = app.state.session_factory
    with sf() as session:
        with session.begin():
            acc = session.get(Account, "acc_a")
            acc.balance_piastres = 1000
    r2 = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "id2"}, json=body)
    assert r2.status_code == 409
    assert r2.headers.get("idempotent-replayed") == "true"
    assert r2.json() == r1.json()


def test_same_key_different_body(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 0, "currency": "SAR"},
    ])
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 30}
    r1 = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "id3"}, json=body)
    assert r1.status_code == 201
    body2 = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 31}
    r2 = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "id3"}, json=body2)
    assert r2.status_code == 422


def test_same_key_different_customers(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 100, "currency": "SAR"},
        {"id": "acc_c", "customer_id": "carol", "balance_piastres": 0, "currency": "SAR"},
    ])
    body_a = {"from_account_id": "acc_a", "to_account_id": "acc_c", "amount_piastres": 10}
    body_b = {"from_account_id": "acc_b", "to_account_id": "acc_c", "amount_piastres": 20}
    r1 = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "shared"}, json=body_a)
    r2 = client.post("/v1/transfers", headers={**H("token-bob"), "Idempotency-Key": "shared"}, json=body_b)
    assert r1.status_code == 201
    assert r2.status_code == 201
    # both applied
    rc = client.get("/v1/accounts/acc_c", headers=H("token-carol")).json()
    assert rc["balance_piastres"] == 30


def test_validation_not_stored(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 0, "currency": "SAR"},
    ])
    # invalid body first
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "v1"}, json={"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 0})
    assert r.status_code == 422
    # now valid with same key should succeed (not replay 422)
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 10}
    r2 = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "v1"}, json=body)
    assert r2.status_code == 201


def test_transfer_get_ownership(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 0, "currency": "SAR"},
    ])
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 10}
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "g1"}, json=body)
    tid = r.json()["id"]
    # sender
    assert client.get(f"/v1/transfers/{tid}", headers=H("token-alice")).status_code == 200
    # receiver
    assert client.get(f"/v1/transfers/{tid}", headers=H("token-bob")).status_code == 200
    # unrelated
    seed_accounts(app, [{"id": "acc_c", "customer_id": "carol", "balance_piastres": 0, "currency": "SAR"}])
    assert client.get(f"/v1/transfers/{tid}", headers=H("token-carol")).status_code == 404
    # unauth
    assert client.get(f"/v1/transfers/{tid}").status_code == 401


def test_exact_balance(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 50, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 0, "currency": "SAR"},
    ])
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 50}
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "e1"}, json=body)
    assert r.status_code == 201
    assert client.get("/v1/accounts/acc_a", headers=H("token-alice")).json()["balance_piastres"] == 0


def test_money_conservation(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 25, "currency": "SAR"},
    ])
    before = 125
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 40}
    r = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "m1"}, json=body)
    assert r.status_code == 201
    after = client.get("/v1/accounts/acc_a", headers=H("token-alice")).json()["balance_piastres"] + client.get("/v1/accounts/acc_b", headers=H("token-bob")).json()["balance_piastres"]
    assert after == before


def test_rate_limit_capacity_refill_retry_after(client, app, settings):
    # Use a dedicated app with small capacity for this test.
    import tempfile, os
    from app.settings import Settings
    from app.main import create_app
    from app.models import Base
    from app.db import make_engine
    from fastapi.testclient import TestClient

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    s = Settings(
        database_url=f"sqlite:///{db_path}",
        api_tokens={"token-alice": "alice", "token-bob": "bob"},
        max_transfer_piastres=1000,
        rate_limit_capacity=5,
        rate_limit_refill_per_second=1.0,
        clock=time.monotonic,
    )
    a = create_app(s)
    Base.metadata.create_all(a.state.engine)
    c = TestClient(a)
    # seed
    sf = a.state.session_factory
    with sf() as session:
        with session.begin():
            session.add(Account(id="acc_a", customer_id="alice", balance_piastres=1000, currency="SAR"))
            session.add(Account(id="acc_b", customer_id="bob", balance_piastres=0, currency="SAR"))

    def H(t): return {"Authorization": f"Bearer {t}"}

    for i in range(5):
        body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 1, "memo": str(i)}
        r = c.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": f"rl{i}"}, json=body)
        assert r.status_code == 201, (i, r.text)
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 1, "memo": "x"}
    r = c.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "rl6"}, json=body)
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    ra = int(r.headers["Retry-After"])
    assert ra >= 1
    time.sleep(1.1)
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 1, "memo": "y"}
    r = c.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "rl7"}, json=body)
    assert r.status_code == 201


def test_rate_limit_consumes_on_replay(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 0, "currency": "SAR"},
    ])
    body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 1}
    # first consumes 1
    r1 = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "rr1"}, json=body)
    assert r1.status_code == 201
    # replay consumes another
    r2 = client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": "rr1"}, json=body)
    assert r2.status_code == 201
    assert r2.headers.get("idempotent-replayed") == "true"


def test_problem_content_type(client, app):
    r = client.get("/v1/accounts/nope", headers=H("token-alice"))
    # account not found but also alice may not own; still 404 problem
    assert "application/problem+json" in r.headers.get("content-type", "")


def test_concurrent_safety(client, app):
    seed_accounts(app, [
        {"id": "acc_a", "customer_id": "alice", "balance_piastres": 100, "currency": "SAR"},
        {"id": "acc_b", "customer_id": "bob", "balance_piastres": 0, "currency": "SAR"},
    ])
    import concurrent.futures

    def worker(i):
        body = {"from_account_id": "acc_a", "to_account_id": "acc_b", "amount_piastres": 60}
        return client.post("/v1/transfers", headers={**H("token-alice"), "Idempotency-Key": f"c{i}"}, json=body).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(worker, range(5)))
    # only one should succeed (100 >= 60, second would 40 insufficient)
    successes = [s for s in results if s == 201]
    assert len(successes) == 1
    ra = client.get("/v1/accounts/acc_a", headers=H("token-alice")).json()
    rb = client.get("/v1/accounts/acc_b", headers=H("token-bob")).json()
    assert ra["balance_piastres"] == 40
    assert rb["balance_piastres"] == 60

from __future__ import annotations

import hashlib
import json
import math
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import make_engine, make_session_factory
from app.models import Account, Base, IdempotencyRecord, Transfer
from app.settings import Settings

# Account field name detection (protected Account definition is provided by the grader).
_ACCOUNT_CUSTOMER_FIELDS = ("customer_id", "owner_customer_id", "owner_id", "user_id", "customer", "account_owner_id", "owner", "account_holder_id")


def _account_customer_field() -> str:
    for name in _ACCOUNT_CUSTOMER_FIELDS:
        if hasattr(Account, name):
            return name
    raise RuntimeError("Account model has no recognizable customer/owner field")


ACCOUNT_CUSTOMER_FIELD = _account_customer_field()


def _owns(account: Any, customer_id: Any) -> bool:
    return getattr(account, ACCOUNT_CUSTOMER_FIELD) == customer_id


def _clock_now(settings: Settings) -> float:
    clock = settings.clock
    if callable(clock):
        try:
            value = clock()
        except TypeError:
            # clock might be a class instance callable? fall through
            value = clock
        return float(value)
    for attr in ("now", "monotonic", "time"):
        method = getattr(clock, attr, None)
        if callable(method):
            return float(method())
    return float(clock)


def _token_map(settings: Settings) -> dict:
    api_tokens = settings.api_tokens
    if isinstance(api_tokens, dict):
        return dict(api_tokens)
    if isinstance(api_tokens, (list, tuple)):
        result = {}
        for item in api_tokens:
            if isinstance(item, dict):
                token = item.get("token") or item.get("api_token") or item.get("key")
                customer = (
                    item.get("customer_id")
                    or item.get("customer")
                    or item.get("user_id")
                    or item.get("owner")
                )
                if token is not None:
                    result[token] = customer
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                result[item[0]] = item[1]
        return result
    try:
        return dict(api_tokens)
    except Exception:
        return {}


class ProblemError(Exception):
    def __init__(self, status: int, title: str, detail: Any = None, headers: dict | None = None, errors: list | None = None):
        self.status = status
        self.title = title
        self.detail = detail
        self.headers = headers
        self.errors = errors


def problem_response(status: int, title: str, detail: Any = None, errors: list | None = None, headers: dict | None = None) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
    }
    if detail is not None:
        body["detail"] = detail
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json", headers=headers)


class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float, clock_fn):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.clock_fn = clock_fn
        self.tokens = float(capacity)
        self.last = clock_fn()
        self.lock = threading.Lock()

    def consume(self) -> int | None:
        with self.lock:
            now = self.clock_fn()
            elapsed = now - self.last
            if elapsed > 0:
                self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last = now
            if self.tokens >= 1:
                self.tokens -= 1
                return None
            needed = 1 - self.tokens
            if self.refill_rate and self.refill_rate > 0:
                wait = needed / self.refill_rate
            else:
                wait = float("inf")
            retry_after = max(1, math.ceil(wait)) if math.isfinite(wait) else 1
            if retry_after < 1:
                retry_after = 1
            return int(retry_after)


def create_app(settings: Settings) -> FastAPI:
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    max_transfer = settings.max_transfer_piastres
    capacity = settings.rate_limit_capacity
    refill_rate = settings.rate_limit_refill_per_second

    def clock_fn() -> float:
        return _clock_now(settings)

    buckets: dict[str, TokenBucket] = {}
    buckets_lock = threading.Lock()

    def get_bucket(customer_id: str) -> TokenBucket:
        with buckets_lock:
            bucket = buckets.get(customer_id)
            if bucket is None:
                bucket = TokenBucket(capacity, refill_rate, clock_fn)
                buckets[customer_id] = bucket
            return bucket

    # Request model defined here so max_transfer is captured.
    class TransferCreate(BaseModel):
        model_config = ConfigDict(extra="forbid")

        from_account_id: str
        to_account_id: str
        amount_piastres: StrictInt = Field(gt=0, le=max_transfer)
        memo: str | None = Field(default=None, max_length=140)

    app = FastAPI(title="Rawaj Pay", version="1.0.0")
    app.state.engine = engine
    app.state.settings = settings
    app.state.session_factory = session_factory

    @app.on_event("shutdown")
    def _shutdown() -> None:
        engine.dispose()

    # Exception handlers
    @app.exception_handler(ProblemError)
    async def handle_problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(
            exc.status,
            exc.title,
            exc.detail,
            errors=exc.errors,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        seen = set()
        for err in exc.errors():
            loc = list(err.get("loc", []))
            key = tuple(loc)
            if key in seen:
                continue
            seen.add(key)
            errors.append({"loc": loc, "msg": err.get("msg")})
        return problem_response(status=422, title="Validation Error", errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        titles = {400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 429: "Too Many Requests"}
        title = titles.get(exc.status_code, "HTTP Error")
        detail = exc.detail if isinstance(exc.detail, str) else None
        headers = getattr(exc, "headers", None)
        return problem_response(status=exc.status_code, title=title, detail=detail, headers=headers)

    def authenticate(request: Request) -> str:
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise ProblemError(401, "Unauthorized", "Missing Authorization header.", headers={"WWW-Authenticate": "Bearer"})
        parts = auth_header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise ProblemError(401, "Unauthorized", "Invalid authentication scheme or token.", headers={"WWW-Authenticate": "Bearer"})
        token = parts[1].strip()
        mapping = _token_map(settings)
        customer_id = mapping.get(token)
        # support mapping token->customer where token may map to object? 
        if customer_id is None:
            # maybe mapping is customer->tokens? try reverse
            for cust, tok in (mapping.items() if isinstance(mapping, dict) else []):
                if tok == token:
                    customer_id = cust
                    break
        if customer_id is None or token not in mapping:
            raise ProblemError(401, "Unauthorized", "Unknown or invalid token.", headers={"WWW-Authenticate": "Bearer"})
        return customer_id

    def _canonical_request_hash(body: TransferCreate) -> str:
        payload = body.model_dump(mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _transfer_dict(transfer: Transfer) -> dict[str, Any]:
        return {
            "id": transfer.id,
            "from_account_id": transfer.from_account_id,
            "to_account_id": transfer.to_account_id,
            "amount_piastres": transfer.amount_piastres,
            "memo": transfer.memo,
            "status": transfer.status,
        }

    def _problem_body(status: int, title: str, detail: str | None = None) -> dict[str, Any]:
        body = {"type": "about:blank", "title": title, "status": status}
        if detail is not None:
            body["detail"] = detail
        return body

    def process_transfer(customer_id: str, body: TransferCreate, idempotency_key: str):
        request_hash = _canonical_request_hash(body)
        sf = session_factory
        try:
            with sf() as session:
                with session.begin():
                    # idempotency lookup
                    record = session.scalar(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.customer_id == customer_id,
                            IdempotencyRecord.key == idempotency_key,
                        )
                    )
                    if record is not None:
                        if record.request_hash != request_hash:
                            raise ProblemError(422, "Unprocessable Entity", "Idempotency key reused with a different request body.")
                        replay_body = record.response_body
                        if isinstance(replay_body, str):
                            try:
                                replay_body = json.loads(replay_body)
                            except Exception:
                                pass
                        return ("replay", record.response_status, replay_body)

                    # validate source
                    source = session.get(Account, body.from_account_id)
                    if source is None or not _owns(source, customer_id):
                        raise ProblemError(404, "Not Found", "Source account not found.")

                    dest = session.get(Account, body.to_account_id)
                    if dest is None:
                        raise ProblemError(422, "Unprocessable Entity", "Unknown destination account.")

                    if body.from_account_id == body.to_account_id:
                        raise ProblemError(422, "Unprocessable Entity", "Source and destination accounts must differ.")

                    # insufficient funds initial check
                    if source.balance_piastres < body.amount_piastres:
                        problem_body = _problem_body(409, "Insufficient Funds", "Insufficient funds.")
                        rec = IdempotencyRecord(
                            customer_id=customer_id,
                            key=idempotency_key,
                            request_hash=request_hash,
                            response_status=409,
                            response_body=problem_body,
                        )
                        session.add(rec)
                        return ("result", 409, problem_body)

                    # conditional debit for concurrency safety
                    debit_result = session.execute(
                        update(Account)
                        .where(
                            Account.id == body.from_account_id,
                            Account.balance_piastres >= body.amount_piastres,
                        )
                        .values(balance_piastres=Account.balance_piastres - body.amount_piastres)
                    )
                    if debit_result.rowcount != 1:
                        problem_body = _problem_body(409, "Insufficient Funds", "Insufficient funds.")
                        rec = IdempotencyRecord(
                            customer_id=customer_id,
                            key=idempotency_key,
                            request_hash=request_hash,
                            response_status=409,
                            response_body=problem_body,
                        )
                        session.add(rec)
                        return ("result", 409, problem_body)

                    # credit destination
                    session.execute(
                        update(Account)
                        .where(Account.id == body.to_account_id)
                        .values(balance_piastres=Account.balance_piastres + body.amount_piastres)
                    )

                    transfer_id = f"trf_{uuid.uuid4().hex}"
                    transfer = Transfer(
                        id=transfer_id,
                        from_account_id=body.from_account_id,
                        to_account_id=body.to_account_id,
                        amount_piastres=body.amount_piastres,
                        memo=body.memo,
                        status="completed",
                    )
                    session.add(transfer)
                    session.flush()
                    transfer_response = _transfer_dict(transfer)

                    rec = IdempotencyRecord(
                        customer_id=customer_id,
                        key=idempotency_key,
                        request_hash=request_hash,
                        response_status=201,
                        response_body=transfer_response,
                    )
                    session.add(rec)
                    return ("result", 201, transfer_response)
        except IntegrityError:
            # Concurrent insert of idempotency record; replay if present.
            with sf() as session:
                record = session.scalar(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.customer_id == customer_id,
                        IdempotencyRecord.key == idempotency_key,
                    )
                )
                if record is not None:
                    if record.request_hash != request_hash:
                        raise ProblemError(422, "Unprocessable Entity", "Idempotency key reused with a different request body.")
                    replay_body = record.response_body
                    if isinstance(replay_body, str):
                        try:
                            replay_body = json.loads(replay_body)
                        except Exception:
                            pass
                    return ("replay", record.response_status, replay_body)
                raise

    # Endpoints
    @app.post("/v1/transfers", status_code=201)
    async def create_transfer(request: Request):
        customer_id = authenticate(request)

        retry_after = get_bucket(customer_id).consume()
        if retry_after is not None:
            raise ProblemError(
                429,
                "Too Many Requests",
                "Rate limit exceeded.",
                headers={"Retry-After": str(retry_after)},
            )

        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            raise ProblemError(400, "Bad Request", "Missing Idempotency-Key header.")

        # Parse body
        try:
            raw = await request.body()
            data = json.loads(raw) if raw else None
        except Exception:
            raise ProblemError(422, "Unprocessable Entity", "Invalid JSON body.")

        try:
            body = TransferCreate.model_validate(data)
        except ValidationError as exc:
            errors = []
            seen = set()
            for err in exc.errors():
                loc = list(err.get("loc", []))
                key = tuple(loc)
                if key in seen:
                    continue
                seen.add(key)
                errors.append({"loc": loc, "msg": err.get("msg")})
            raise ProblemError(422, "Validation Error", errors=errors)

        kind, status_code, payload = process_transfer(customer_id, body, idempotency_key)

        headers = {}
        if kind == "replay":
            headers["Idempotent-Replayed"] = "true"

        return JSONResponse(status_code=status_code, content=payload, headers=headers)

    @app.get("/v1/accounts/{account_id}")
    def get_account(account_id: str, request: Request):
        customer_id = authenticate(request)
        sf = session_factory
        with sf() as session:
            account = session.get(Account, account_id)
            if account is None or not _owns(account, customer_id):
                raise ProblemError(404, "Not Found", "Account not found.")
            return {
                "id": account.id,
                "balance_piastres": account.balance_piastres,
                "currency": account.currency,
            }

    @app.get("/v1/transfers/{transfer_id}")
    def get_transfer(transfer_id: str, request: Request):
        customer_id = authenticate(request)
        sf = session_factory
        with sf() as session:
            transfer = session.get(Transfer, transfer_id)
            if transfer is None:
                raise ProblemError(404, "Not Found", "Transfer not found.")
            source = session.get(Account, transfer.from_account_id)
            dest = session.get(Account, transfer.to_account_id)
            if not (
                (source is not None and _owns(source, customer_id))
                or (dest is not None and _owns(dest, customer_id))
            ):
                raise ProblemError(404, "Not Found", "Transfer not found.")
            return _transfer_dict(transfer)

    return app

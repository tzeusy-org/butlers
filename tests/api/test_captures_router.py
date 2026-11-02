"""Owner capture admission, status and the explicit chat bridge (bu-2jtfw.9.4.2).

RFC0037 "Capture service, roles and owner provenance" and "Capture audit and
validation do not retain source bodies"; REQ-general-capture-001/-006 ingress.

Unit cases drive the production app, owner middleware and audit middleware through
raw ASGI: refusals read zero body octets and acquire no General connection, and a
sentinel matrix proves no outcome leaks intake content into response, audit, logs
or traces. Integration cases run the mounted routes over the real migrated capture
ledger and the landed General capture service.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from butlers.api import dashboard_audit_middleware
from butlers.api.app import create_app
from butlers.api.deps import get_db_optional
from butlers.api.owner_auth.service import AuthError
from butlers.api.routers import captures
from butlers.core.capture import CaptureReceipt, CaptureUnavailable, rotate_epoch

_HOST = "butlers.example.test"
_ORIGIN = f"https://{_HOST}"
_KEY = "synthetic-capture-owner-key"
_SESSION = "synthetic-session-token"
_OTHER_SESSION = "synthetic-other-session"
_CSRF = "synthetic-csrf"
_COOKIE = "__Host-butlers-default-owner"
# Planted ordinary text under non-secret key names, an attachment locator and a
# private-shaped value. Any appearance outside the ledger is a leak.
_TEXT_SENTINEL = "capture-sentinel-text-4b1d"
_LOCATOR_SENTINEL = "https://files.example.test/capture-sentinel-locator-4b1d"
_PRIVATE_SENTINEL = "passport-sentinel-K4B1D"
_SENTINELS = (_TEXT_SENTINEL, _LOCATOR_SENTINEL, _PRIVATE_SENTINEL)


class _SessionOwner:
    """Owner auth state: one API key and two cookie sessions with CSRF."""

    async def authorize(
        self,
        *,
        session_token: str | None = None,
        api_key: str | None = None,
        csrf_token: str | None = None,
        unsafe: bool = False,
    ) -> SimpleNamespace:
        if api_key is not None:
            if api_key != _KEY:
                raise AuthError("UNAUTHORIZED")
            return SimpleNamespace(method="header", expires_at=None)
        if session_token not in {_SESSION, _OTHER_SESSION}:
            raise AuthError("UNAUTHORIZED")
        if unsafe and csrf_token != _CSRF:
            raise AuthError("FORBIDDEN")
        digest = hashlib.sha256(session_token.encode()).hexdigest()
        return SimpleNamespace(method="session", session_digest=digest, expires_at=None)


class _Conn:
    def __init__(self, role: str = "butler_general_rw") -> None:
        self.role = role
        self.executed: list[str] = []
        self.transactions: list[str] = []
        self.row: dict | None = None

    @asynccontextmanager
    async def transaction(self):
        self.transactions.append("open")
        try:
            yield
        except BaseException:
            self.transactions.append("rollback")
            raise
        self.transactions.append("commit")

    async def execute(self, sql: str, *args) -> None:
        self.executed.append(sql)

    async def fetchval(self, sql: str, *args):
        return self.role

    async def fetchrow(self, sql: str, *args):
        return self.row

    async def fetch(self, sql: str, *args):
        return []


class _Manager:
    """Dashboard DB manager seam counting General pool requests."""

    def __init__(self, conn: _Conn | None = None) -> None:
        self.conn = conn
        self.calls = 0

    def pool(self, _name: str):
        self.calls += 1
        if self.conn is None:
            raise KeyError("synthetic unavailable")
        conn = self.conn

        class _Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        return _Pool()

    def credential_shared_pool(self):
        return SimpleNamespace(fetchval=_exists)


async def _exists(_sql, conversation_id):
    return 1 if conversation_id == _CONVERSATION else None


_CONVERSATION = uuid.UUID("00000000-0000-4000-8000-0000000c0a7e")


@pytest.fixture
def seams(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_AUTH_ORIGIN", _ORIGIN)
    monkeypatch.setenv("DASHBOARD_AUTH_RP_ID", _HOST)
    observed = SimpleNamespace(manager=_Manager(), audits=[], epoch_path=tmp_path / "epoch.json")
    rotate_epoch(observed.epoch_path)

    async def record(_db_manager, **fields) -> None:
        observed.audits.append(fields)

    monkeypatch.setattr(dashboard_audit_middleware, "get_db_manager", lambda: observed.manager)
    monkeypatch.setattr(dashboard_audit_middleware, "emit_dashboard_audit", record)
    app = create_app(api_key=_KEY)
    app.state.owner_auth_service = _SessionOwner()
    app.state.capture_epoch_path = observed.epoch_path
    app.dependency_overrides[get_db_optional] = lambda: observed.manager
    observed.app = app
    return observed


def _owner_key() -> list[tuple[bytes, bytes]]:
    return [(b"x-api-key", _KEY.encode())]


def _cookie(session: str = _SESSION, *, origin: bool = True, csrf: str | None = _CSRF):
    headers = [(b"cookie", f"{_COOKIE}={session}".encode())]
    if origin:
        headers.append((b"origin", _ORIGIN.encode()))
    if csrf is not None:
        headers.append((b"x-csrf-token", csrf.encode()))
    return headers


async def _raw(app, method: str, path: str, body: bytes = b"", headers=()) -> tuple[int, Any, int]:
    """Drive one request through the full ASGI stack, counting ``receive`` calls."""
    pending = [body]
    received = 0
    sent: list[dict] = []

    async def receive():
        nonlocal received
        received += 1
        if pending:
            return {"type": "http.request", "body": pending.pop(), "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    path_only, _, query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path_only,
        "raw_path": path_only.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(b"host", _HOST.encode()), (b"content-type", b"application/json"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": (_HOST, 443),
    }
    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, (json.loads(raw) if raw else None), received


async def _context(app, door: str = "dashboard", headers=None, query: str = "") -> str:
    status, body, _ = await _raw(
        app,
        "GET",
        f"/api/general/capture-intake-context?door={door}{query}",
        headers=_owner_key() if headers is None else headers,
    )
    assert status == 200, body
    assert body["data"]["door"] == door
    return body["data"]["intake_context"]


def _intake(**extra) -> bytes:
    payload = {
        "text": _TEXT_SENTINEL,
        "references": [{"source_id": _SOURCE_ID, "attachment_id": _ATTACHMENT_ID}],
        "mutation_id": "retry-1",
    }
    payload.update(extra)
    return json.dumps(payload).encode()


_SOURCE_ID = "00000000-0000-4000-8000-00000000a5c1"
_ATTACHMENT_ID = "00000000-0000-4000-8000-00000000a7a1"
_DOORS = [("dashboard", "/api/general/captures"), ("chat", "/api/general/capture-chat")]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(("door", "path"), _DOORS)
async def test_refusals_read_no_body_and_acquire_no_general_connection(seams, door, path):
    """Owner auth, Origin/CSRF and intake context all fail before body, pool or admission."""
    app = seams.app
    body = _intake(note=_PRIVATE_SENTINEL)
    owner_auth_cases = [
        ([], 401, "UNAUTHORIZED"),
        ([(b"x-api-key", b"wrong-key")], 401, "UNAUTHORIZED"),
        (_cookie(origin=False), 403, "FORBIDDEN"),
        (_cookie(csrf="wrong-csrf"), 403, "FORBIDDEN"),
        (_cookie(csrf=None), 403, "FORBIDDEN"),
    ]
    for headers, status_code, code in owner_auth_cases:
        status, response, received = await _raw(app, "POST", path, body, headers)
        assert (status, response["error"]["code"], received) == (status_code, code, 0)
    # The outer owner middleware refuses before the audit middleware runs at all.
    assert seams.audits == []

    other_door = await _context(app, "chat" if door == "dashboard" else "dashboard")
    session_bound = await _context(app, door, headers=_cookie(_OTHER_SESSION))
    current = await _context(app, door)
    head, _, mac = current.partition(".")
    tampered = f"{head[:-2]}AA.{mac}"
    original_ttl = captures.CONTEXT_TTL
    captures.CONTEXT_TTL = timedelta(seconds=-1)
    try:
        expired = await _context(app, door)
    finally:
        captures.CONTEXT_TTL = original_ttl
    context_cases = [
        _owner_key(),
        [*_owner_key(), (b"x-capture-intake", other_door.encode())],
        [*_owner_key(), (b"x-capture-intake", session_bound.encode())],
        [*_owner_key(), (b"x-capture-intake", tampered.encode())],
        [*_owner_key(), (b"x-capture-intake", expired.encode())],
        [*_owner_key(), (b"x-capture-intake", b"x"), (b"x-capture-intake", current.encode())],
    ]
    for headers in context_cases:
        status, response, received = await _raw(app, "POST", path, body, headers)
        assert (status, response["error"]["code"], received) == (
            403,
            "CAPTURE_INTAKE_CONTEXT_REQUIRED",
            0,
        )
    assert seams.manager.calls == 0

    # A context minted before an epoch rotation (restore) cannot authorize a retry.
    rotate_epoch(seams.epoch_path)
    status, response, received = await _raw(
        app, "POST", path, body, [*_owner_key(), (b"x-capture-intake", current.encode())]
    )
    assert (status, response["error"]["code"], received) == (
        403,
        "CAPTURE_INTAKE_CONTEXT_REQUIRED",
        0,
    )
    assert seams.manager.calls == 0
    operation = captures.AUDIT_OPERATIONS[door]
    assert [a["operation"] for a in seams.audits] == [operation] * (len(context_cases) + 1)
    assert {a["outcome"]["category"] for a in seams.audits} == {"intake_context_refused"}
    assert all(a["body"] is None and a["result"] == "error" for a in seams.audits)

    # Positive control: a current context reaches the General pool seam (unavailable).
    fresh = await _context(app, door)
    status, response, received = await _raw(
        app,
        "POST",
        path,
        body[:0] + _intake(),
        [*_owner_key(), (b"x-capture-intake", fresh.encode())],
    )
    assert (status, response["error"]["code"]) == (503, "CAPTURE_UNAVAILABLE")
    assert received >= 1 and seams.manager.calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_context_binds_only_an_owner_verified_conversation(seams):
    app = seams.app
    await _context(app, "chat", query=f"&conversation_id={_CONVERSATION}")
    for query, code in [
        (f"&conversation_id={uuid.uuid4()}", "CONVERSATION_NOT_FOUND"),
        ("&conversation_id=not-a-uuid", "CONVERSATION_NOT_FOUND"),
    ]:
        status, body, _ = await _raw(
            app,
            "GET",
            f"/api/general/capture-intake-context?door=chat{query}",
            headers=_owner_key(),
        )
        assert (status, body["error"]["code"]) == (404, code)
    for door in ("internal", "dashboard&conversation_id=" + str(_CONVERSATION)):
        status, body, _ = await _raw(
            app, "GET", f"/api/general/capture-intake-context?door={door}", headers=_owner_key()
        )
        assert (status, body["error"]["code"]) == (422, "CAPTURE_INVALID")


class _FakeService:
    """Stands in for the General service; ``behavior`` selects one outcome."""

    behavior = "held"
    started: Any = None

    def __init__(self, pool, epoch_path) -> None:
        self.pool = pool

    async def admit(self, authority, text, *, references=None, mutation_key=None):
        async with self.pool.acquire() as conn, conn.transaction():
            if self.behavior == "value_error":
                raise ValueError(f"bad {text} {references}")
            if self.behavior == "unexpected":
                raise RuntimeError(f"boom {text} {references}")
            if self.behavior == "conflict":
                raise CaptureUnavailable("mutation_conflict")
            if self.behavior == "timeout":
                await asyncio.sleep(5)
            if self.behavior == "cancelled":
                self.started.set()
                await asyncio.sleep(3600)
            capture_id = uuid.uuid4()
            conn.row = {
                "id": capture_id,
                "disposition": "held",
                "category": "pending",
                "operation_id": None,
                "receipt": None,
                "mutation_key": mutation_key,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
            return CaptureReceipt(capture_id, "held", "pending")


@pytest.fixture
def traced(monkeypatch, caplog):
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.metrics import NoOpMeterProvider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrument = FastAPIInstrumentor.instrument_app

    def local_instrument(app, **kwargs):
        return instrument(
            app, tracer_provider=provider, meter_provider=NoOpMeterProvider(), **kwargs
        )

    monkeypatch.setattr(FastAPIInstrumentor, "instrument_app", staticmethod(local_instrument))
    monkeypatch.setattr("butlers.core.telemetry.init_telemetry", lambda *args: None)
    monkeypatch.setattr("butlers.core.metrics.init_metrics", lambda *args: None)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST", ".*")
    monkeypatch.setenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE", ".*")
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(captures, "_capture_service", lambda: _FakeService)
    monkeypatch.setattr(captures, "REQUEST_TIMEOUT_S", 1.0)
    try:
        yield exporter
    finally:
        provider.shutdown()


def _retained(seams, exporter, caplog, *responses) -> str:
    spans = exporter.get_finished_spans()
    return (
        repr(seams.audits)
        + "".join(json.dumps(r) for r in responses)
        + caplog.text
        + repr([(s.name, dict(s.attributes or {}), s.events) for s in spans])
    )


_OUTCOMES = [
    ("held", _intake(), 202, None, "pending"),
    ("held", b'{"text": "' + _TEXT_SENTINEL.encode() + b'", ', 422, "CAPTURE_INVALID", None),
    ("held", _intake(actor=_PRIVATE_SENTINEL), 422, "CAPTURE_INVALID", "invalid_input"),
    ("held", _intake(target_schema=_PRIVATE_SENTINEL), 422, "CAPTURE_INVALID", "invalid_input"),
    ("held", _intake(references=[{"url": _LOCATOR_SENTINEL}]), 422, "CAPTURE_INVALID", None),
    ("value_error", _intake(), 500, "CAPTURE_FAILED", "internal_error"),
    ("unexpected", _intake(), 500, "CAPTURE_FAILED", "internal_error"),
    ("conflict", _intake(), 409, "CAPTURE_CONFLICT", "mutation_conflict"),
    ("timeout", _intake(), 504, "CAPTURE_TIMEOUT", "timeout"),
]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(("door", "path"), _DOORS)
@pytest.mark.parametrize(
    ("behavior", "body", "status_code", "code", "category"),
    _OUTCOMES,
    ids=[
        "success",
        "malformed_json",
        "actor_field",
        "target_field",
        "locator_reference",
        "value_error",
        "unexpected",
        "conflict",
        "timeout",
    ],
)
async def test_sentinel_matrix_exposes_no_intake_on_any_outcome(
    traced, seams, caplog, door, path, behavior, body, status_code, code, category
):
    conn = _Conn()
    seams.manager.conn = conn
    _FakeService.behavior = behavior
    context = await _context(seams.app, door)
    logging.getLogger("capture_privacy_control").warning("capture-log-live-control")
    status, response, _ = await _raw(
        seams.app, "POST", path, body, [*_owner_key(), (b"x-capture-intake", context.encode())]
    )
    assert status == status_code, response
    if code is None:
        assert response["data"]["disposition"] == "held"
        assert response["data"]["source_door"] == door
        assert response["data"]["target"] is None
    else:
        assert response["error"]["code"] == code
    if status_code != 202 and conn.transactions:
        assert conn.transactions[-1] == "rollback"

    # Positive companions: one content-blind audit with actor, fixed operation and outcome.
    (audit,) = seams.audits
    assert audit["operation"] == captures.AUDIT_OPERATIONS[door]
    assert audit["path"] == path and audit["body"] is None
    assert audit["result"] == ("success" if status_code < 400 else "error")
    assert audit["outcome"]["category"] == (category or audit["outcome"]["category"])
    assert audit["request"].state.owner_authority is not None
    if status_code == 202:
        assert audit["path_params"] == {"capture_id": response["data"]["capture_id"]}
    assert "capture-log-live-control" in caplog.text
    assert traced.get_finished_spans(), "server instrumentation must have run"
    retained = _retained(seams, traced, caplog, response)
    for value in (*_SENTINELS, context, context[-24:]):
        assert value not in retained


@pytest.mark.unit
@pytest.mark.asyncio
async def test_neutralized_no_body_policy_is_detected(traced, seams, caplog, monkeypatch):
    """Mutation control: without the server-owned policy the generic audit keeps the body."""
    seams.manager.conn = _Conn()
    _FakeService.behavior = "held"
    monkeypatch.setattr(dashboard_audit_middleware, "_capture_operation", lambda *_: None)
    context = await _context(seams.app)
    status, response, _ = await _raw(
        seams.app,
        "POST",
        "/api/general/captures",
        _intake(),
        [*_owner_key(), (b"x-capture-intake", context.encode())],
    )
    assert status == 202
    assert _TEXT_SENTINEL in _retained(seams, traced, caplog, response)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(("door", "path"), _DOORS)
async def test_cancellation_propagates_after_cleanup_and_fixed_audit(
    traced, seams, monkeypatch, door, path
):
    """A request cancelled mid-admission propagates; nothing is synthesized as success."""
    monkeypatch.setattr(captures, "REQUEST_TIMEOUT_S", 30)
    conn = _Conn()
    seams.manager.conn = conn
    _FakeService.behavior = "cancelled"
    _FakeService.started = asyncio.Event()
    context = await _context(seams.app, door)
    request = asyncio.create_task(
        _raw(
            seams.app,
            "POST",
            path,
            _intake(),
            [*_owner_key(), (b"x-capture-intake", context.encode())],
        )
    )
    await _FakeService.started.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert conn.transactions == ["open", "open", "rollback", "rollback"]
    (audit,) = seams.audits
    assert audit["outcome"] == {"category": "cancelled"}
    assert audit["result"] == "error" and audit["body"] is None
    assert audit["response_status"] is None and audit["path_params"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reads_are_unavailable_never_false_empty(seams):
    app = seams.app
    paths = ["/api/general/captures", f"/api/general/captures/{uuid.uuid4()}"]
    for conn in (None, _Conn(role="dashboard_api")):
        seams.manager.conn = conn
        for path in paths:
            status, body, _ = await _raw(app, "GET", path, headers=_owner_key())
            assert (status, body["error"]["code"]) == (503, "CAPTURE_UNAVAILABLE")
    seams.manager.conn = _Conn()
    status, body, _ = await _raw(app, "GET", paths[0], headers=_owner_key())
    assert (status, body) == (
        200,
        {"items": [], "next_cursor": None, "limit": 20, "sources_degraded": None},
    )
    status, body, _ = await _raw(app, "GET", paths[1], headers=_owner_key())
    assert (status, body["error"]["code"]) == (404, "CAPTURE_NOT_FOUND")
    status, body, _ = await _raw(app, "GET", f"{paths[0]}?cursor=edited", headers=_owner_key())
    assert status == 400
    status, body, _ = await _raw(app, "GET", f"{paths[0]}?limit=500", headers=_owner_key())
    assert (status, body["error"]["code"]) == (422, "CAPTURE_INVALID")
    for path in paths:
        status, _, received = await _raw(app, "GET", path)
        assert (status, received) == (401, 0)


# ---------------------------------------------------------------------------
# Real migrated ledger and the landed General capture service
# ---------------------------------------------------------------------------

from tests.integration import test_general_capture_ledger as ledger  # noqa: E402

capture_db_url = ledger.capture_db_url


@pytest.fixture
async def login_pool(capture_db_url):
    """The dashboard API's role-less login pool; routes must SET LOCAL ROLE themselves."""

    async def init(conn):
        from butlers.db import register_jsonb_codec

        await register_jsonb_codec(conn)

    pool = await asyncpg.create_pool(capture_db_url, min_size=1, max_size=1, init=init)
    yield pool
    await pool.close()


@pytest.fixture
async def mounted(login_pool, monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_AUTH_ORIGIN", _ORIGIN)
    monkeypatch.setenv("DASHBOARD_AUTH_RP_ID", _HOST)
    audits: list[dict] = []

    async def record(_db_manager, **fields) -> None:
        audits.append(fields)

    manager = SimpleNamespace(pool=lambda _name: login_pool)
    monkeypatch.setattr(dashboard_audit_middleware, "get_db_manager", lambda: manager)
    monkeypatch.setattr(dashboard_audit_middleware, "emit_dashboard_audit", record)
    epoch_path = tmp_path / "epoch.json"
    epoch = rotate_epoch(epoch_path)
    async with login_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_general_rw")
        await conn.execute(
            """INSERT INTO public.capture_service_control
               (admitted_epoch,admission_enabled,dispatch_enabled,recovery_required)
               VALUES ($1,true,true,false) ON CONFLICT (singleton) DO UPDATE
               SET admitted_epoch=$1,admission_enabled=true,dispatch_enabled=true,
                   recovery_required=false""",
            epoch.generation,
        )
    app = create_app(api_key=_KEY)
    app.state.owner_auth_service = _SessionOwner()
    app.state.capture_epoch_path = epoch_path
    app.dependency_overrides[get_db_optional] = lambda: manager
    return SimpleNamespace(app=app, pool=login_pool, epoch_path=epoch_path, audits=audits)


async def _post(app, path, context, body):
    return await _raw(
        app, "POST", path, body, [*_owner_key(), (b"x-capture-intake", context.encode())]
    )


async def _rows(pool) -> list:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_general_rw")
        return await conn.fetch("SELECT * FROM public.captures ORDER BY created_at")


async def _login_role(pool) -> str:
    # max_size=1: this is the same physical connection every route used.
    return await pool.fetchval("SELECT current_user || ':' || current_setting('search_path')")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(("door", "path"), _DOORS)
async def test_admission_commits_held_and_retries_reuse_the_receipt(mounted, door, path):
    app, pool = mounted.app, mounted.pool
    baseline = await _login_role(pool)
    assert not baseline.startswith("butler_general_rw")
    context = await _context(app, door)
    status, first, _ = await _post(app, path, context, _intake())
    assert status == 202, first
    held = first["data"]
    assert (held["disposition"], held["category"], held["source_door"]) == ("held", "pending", door)
    # Identical authorized retry returns the original durable receipt.
    status, again, _ = await _post(app, path, context, _intake())
    assert (status, again["data"]["capture_id"]) == (202, held["capture_id"])
    # Same key, changed payload refuses; nothing new is admitted.
    status, changed, _ = await _post(app, path, context, _intake(text="Changed synthetic"))
    assert (status, changed["error"]["code"]) == (409, "CAPTURE_CONFLICT")
    # No key: a fresh explicit occurrence each time.
    status, fresh, _ = await _post(app, path, context, _intake(mutation_id=None))
    assert status == 202 and fresh["data"]["capture_id"] != held["capture_id"]
    rows = await _rows(pool)
    assert len(rows) == 2
    raw = repr([dict(r) for r in rows])
    assert context not in raw and hashlib.sha256(context.encode()).hexdigest() not in raw
    assert await _login_role(pool) == baseline

    status, listed, _ = await _raw(
        app, "GET", "/api/general/captures?limit=1", headers=_owner_key()
    )
    assert status == 200 and len(listed["items"]) == 1 and listed["next_cursor"]
    status, rest, _ = await _raw(
        app,
        "GET",
        f"/api/general/captures?limit=1&cursor={listed['next_cursor']}",
        headers=_owner_key(),
    )
    ids = {listed["items"][0]["capture_id"], rest["items"][0]["capture_id"]}
    assert ids == {held["capture_id"], fresh["data"]["capture_id"]} and rest["next_cursor"] is None
    status, one, _ = await _raw(
        app, "GET", f"/api/general/captures/{held['capture_id']}", headers=_owner_key()
    )
    assert status == 200 and one["data"] == {**held, "updated_at": one["data"]["updated_at"]}
    assert _TEXT_SENTINEL not in json.dumps([listed, rest, one])
    assert await _login_role(pool) == baseline


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_restore_fence_refuses_old_context_and_never_relabels_in_doubt(mounted):
    app, pool = mounted.app, mounted.pool
    path = "/api/general/captures"
    old = await _context(app)
    status, first, _ = await _post(app, path, old, _intake(references=[]))
    assert status == 202
    capture_id = uuid.UUID(first["data"]["capture_id"])
    # Put that lineage in doubt through the real service, as a crash would.
    service = ledger.CaptureService(_RolePool(pool), mounted.epoch_path)
    claimed = await service.claim(capture_id, owner="general", kind="note")
    await service.mark_unknown(claimed.operation_id)

    # Restore: rotate the host epoch; the old snapshot lacks nothing it can replay.
    epoch = rotate_epoch(mounted.epoch_path)
    status, refused, received = await _post(app, path, old, _intake(references=[]))
    assert (status, refused["error"]["code"], received) == (
        403,
        "CAPTURE_INTAKE_CONTEXT_REQUIRED",
        0,
    )
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_general_rw")
        await conn.execute(
            "UPDATE public.capture_service_control SET admitted_epoch=$1", epoch.generation
        )
    fresh = await _context(app)
    status, renewed, _ = await _post(app, path, fresh, _intake(references=[]))
    assert status == 202 and renewed["data"]["capture_id"] != str(capture_id)
    status, kept, _ = await _raw(
        app, "GET", f"/api/general/captures/{capture_id}", headers=_owner_key()
    )
    assert (kept["data"]["disposition"], kept["data"]["category"]) == (
        "held",
        "target_outcome_unknown",
    )
    assert len(await _rows(pool)) == 2


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_disabled_admission_and_cancellation_admit_nothing_and_leak_no_role(
    mounted, monkeypatch
):
    app, pool = mounted.app, mounted.pool
    path = "/api/general/captures"
    baseline = await _login_role(pool)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_general_rw")
        await conn.execute("UPDATE public.capture_service_control SET admission_enabled=false")
    status, body, _ = await _post(app, path, await _context(app), _intake())
    assert (status, body["error"]["code"]) == (503, "CAPTURE_UNAVAILABLE")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_general_rw")
        await conn.execute("UPDATE public.capture_service_control SET admission_enabled=true")

    real = captures._capture_service()
    admitted = asyncio.Event()

    class _CancelAfterAdmit(real):
        async def admit(self, *args, **kwargs):
            receipt = await super().admit(*args, **kwargs)
            admitted.set()
            await asyncio.sleep(3600)
            return receipt

    monkeypatch.setattr(captures, "_capture_service", lambda: _CancelAfterAdmit)
    request = asyncio.create_task(_post(app, path, await _context(app), _intake()))
    await admitted.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    # The savepoint was released, but the outer transaction never committed.
    assert await _rows(pool) == []
    assert await _login_role(pool) == baseline
    assert [a["outcome"]["category"] for a in mounted.audits] == ["unavailable", "cancelled"]


class _RolePool:
    """Test-side General-role pool for driving the real service directly."""

    def __init__(self, pool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def acquire(self):
        async with self._pool.acquire() as conn:
            await conn.execute("SET ROLE butler_general_rw")
            try:
                yield conn
            finally:
                await conn.execute("RESET ROLE")

    async def fetchval(self, *args):
        async with self.acquire() as conn:
            return await conn.fetchval(*args)

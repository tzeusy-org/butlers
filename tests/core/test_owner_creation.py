"""D7 owner creation: raw transport binding and atomic caller protocol.

The connection recorder here proves enlistment/order only. Migrated role,
concurrency and actual rollback outcomes belong to the PostgreSQL owner.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastmcp import Client, FastMCP

from butlers.core.attention_urgency import (
    AttentionOrigin,
    _current_origin,
    evaluate_interruption,
)
from butlers.core.owner_creation import (
    META_KEY,
    CreationClaim,
    OwnerCreationMiddleware,
    _pending,
    argument_digest,
    owner_creation_transaction,
)

pytestmark = pytest.mark.unit


def test_raw_intent_uses_rfc8785_and_closed_json_types():
    import hashlib

    import rfc8785

    raw = {"name": "synthetic", "n": 1.0, "unicode": "😀", "null": None}
    assert (
        argument_digest(raw)
        == hashlib.sha256(b"butlers:owner-creation:v1\0" + rfc8785.dumps(raw)).hexdigest()
    )
    assert argument_digest({"n": 1}) == argument_digest({"n": 1.0})
    assert argument_digest({"n": None}) != argument_digest({})
    assert argument_digest({"n": "1"}) != argument_digest({"n": 1})
    for value in ((1,), {1: "bad"}, float("nan"), 2**64, "\ud800"):
        with pytest.raises((ValueError, TypeError)):
            argument_digest({"value": value})


async def test_real_raw_mcp_adapter_binds_one_call_and_refuses_mutations():
    server = FastMCP("general")
    server.add_middleware(OwnerCreationMiddleware("general"))
    seen = []

    @server.tool
    async def schedule_create(name: str):
        claim = _pending.get()
        seen.append(claim)
        if name == "raise":
            raise ValueError("Synthetic owner failure")
        return {"bound": claim is not None}

    raw = {"name": "synthetic"}
    proof = dict(
        nonce="a" * 43,
        session_digest="b" * 64,
        operation=str(uuid4()),
        request_digest=argument_digest(raw),
        target="general",
    )
    async with Client(server) as client:
        result = await client.call_tool("schedule_create", raw, meta={META_KEY: proof})
        assert result.data == {"bound": True}
        assert seen[-1] == CreationClaim(**proof)
        assert _pending.get() is None
        assert (await client.call_tool("schedule_create", raw)).data == {"bound": False}
        for field, value in (
            ("target", "home"),
            ("nonce", "b" * 64),
            ("request_digest", "c" * 64),
            ("operation", "bad"),
        ):
            with pytest.raises(Exception, match="Invalid owner creation claim"):
                await client.call_tool(
                    "schedule_create", raw, meta={META_KEY: proof | {field: value}}
                )
        with pytest.raises(Exception, match="Invalid owner creation claim"):
            await client.call_tool("schedule_create", {"name": "edited"}, meta={META_KEY: proof})
        raised = {"name": "raise"}
        with pytest.raises(Exception, match="Synthetic owner failure"):
            await client.call_tool(
                "schedule_create",
                raised,
                meta={META_KEY: proof | {"request_digest": argument_digest(raised)}},
            )
        assert _pending.get() is None
        assert (await client.call_tool("schedule_create", raw)).data == {"bound": False}


async def test_domain_write_and_closed_receipt_share_connection_and_transaction():
    events = []
    task = uuid4()

    @asynccontextmanager
    async def transaction():
        events.append("begin_tx")
        try:
            yield
        except BaseException:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    async def fetchval(_sql, phase, *_args):
        events.append(phase)
        return (
            {"state": "admitted"} if phase == "begin" else {"state": "completed", "id": str(task)}
        )

    connection = SimpleNamespace(transaction=transaction, fetchval=fetchval)

    @asynccontextmanager
    async def acquire():
        yield connection

    @owner_creation_transaction
    async def create(conn, *, fails=False):
        assert conn is connection
        events.append("write")
        if fails:
            raise ValueError("Synthetic write failure")
        return task

    claim = CreationClaim("a" * 43, "b" * 64, str(uuid4()), "c" * 64, "general")
    token = _pending.set(claim)
    try:
        assert await create(SimpleNamespace(acquire=acquire)) == task
        assert events == ["begin_tx", "begin", "write", "finish", "commit"]
        events.clear()
        with pytest.raises(ValueError, match="Synthetic write failure"):
            await create(SimpleNamespace(acquire=acquire), fails=True)
        assert events == ["begin_tx", "begin", "write", "rollback"]
        assert _pending.get() is claim
        connection.fetchval = AsyncMock(return_value={"state": "completed", "id": str(task)})
        events.clear()
        assert await create(SimpleNamespace(acquire=acquire)) == task
        assert events == ["begin_tx", "commit"]  # no recreated task or second effect
    finally:
        _pending.reset(token)


async def test_current_evaluator_has_no_model_or_locator_authority(caplog):
    pool = SimpleNamespace(
        fetchval=AsyncMock(return_value={"cause": "owner_creation", "ref": str(uuid4())})
    )
    assert not (await evaluate_interruption(pool, gate="context")).interrupts
    pool.fetchval.assert_not_awaited()
    origin = AttentionOrigin(namespace="general", task_id=uuid4())
    pool.fetchval.return_value = {"cause": "routine"}
    assert not (await evaluate_interruption(pool, gate="context", origin=origin)).interrupts
    assert "task_id" not in pool.fetchval.call_args.args[1]  # no claimed occurrence
    origin = AttentionOrigin(
        namespace="general", task_id=uuid4(), original_next_run_at=datetime.now(UTC)
    )
    pool.fetchval.return_value = {"cause": "owner_creation", "ref": str(uuid4())}
    assert (await evaluate_interruption(pool, gate="context", origin=origin)).interrupts
    assert pool.fetchval.call_args.args[1]["task_id"] == str(origin.task_id)
    pool.fetchval.side_effect = RuntimeError("PRIVATE_SQL_PAYLOAD_CANARY")
    token = _current_origin.set(origin)
    try:
        assert not (await evaluate_interruption(pool, gate="quiet_hours")).interrupts
    finally:
        _current_origin.reset(token)
    assert "PRIVATE_SQL_PAYLOAD_CANARY" not in caplog.text

    # Drive actual tick's final claim. The recorder models publication only;
    # PostgreSQL role, digest and rollback semantics have a separate real owner.
    from butlers.core.scheduler import tick
    from tests.core.test_scheduler_dispatch_idempotency import _ClaimTrackingPool, _make_due_task

    now = datetime.now(UTC)
    task = _make_due_task(task_id=str(uuid4()), until_at=now + timedelta(seconds=30))
    task["enabled"] = True
    claimed = []

    class FinalClaimPool(_ClaimTrackingPool):
        async def fetchrow(self, query, *args):
            row = await super().fetchrow(query, *args)
            if row and "SET enabled = false" in query:
                task["enabled"] = False
                if "dashboard_attention.final_claim" in query:
                    claimed.append((str(task["id"]), task["next_run_at"]))
            return row

        async def fetchval(self, query, *args):
            if "dashboard_attention.evaluate" in query:
                origin = args[0]
                valid = (
                    origin.get("task_id"),
                    datetime.fromisoformat(origin["original_next_run_at"]),
                ) in claimed
                return (
                    {"cause": "owner_creation", "ref": str(uuid4())}
                    if valid
                    else {"cause": "routine"}
                )
            return await super().fetchval(query, *args)

    final_pool = FinalClaimPool(tasks=[task])
    seen = []

    async def dispatch(**_kwargs):
        origin = _current_origin.get()
        assert origin is not None and not task["enabled"]
        seen.append(origin)
        assert (await evaluate_interruption(final_pool, gate="quiet_hours")).interrupts
        return {"status": "ok"}

    with patch("butlers.core.scheduler._next_run", return_value=now + timedelta(days=1)):
        assert await tick(final_pool, dispatch, stagger_key="general") == 1
    assert len(seen) == 1 and _current_origin.get() is None
    assert (await evaluate_interruption(final_pool, gate="context", origin=seen[0])).interrupts
    wrong = AttentionOrigin(namespace="general", task_id=seen[0].task_id, original_next_run_at=now)
    assert not (await evaluate_interruption(final_pool, gate="context", origin=wrong)).interrupts


async def test_real_notify_schema_cannot_expose_model_urgency():
    from butlers.config import ButlerType
    from butlers.core_tools._base import ToolContext
    from butlers.core_tools._notifications import register_notification_tools

    server = FastMCP("general")
    server.add_middleware(OwnerCreationMiddleware("general"))
    context = ToolContext(
        daemon=SimpleNamespace(),
        pool=None,
        spawner=None,
        butler_name="general",
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )

    def core_tool(_group, **kwargs):
        return server.tool(**kwargs)

    register_notification_tools(context, server, core_tool)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert "priority" not in tools["notify"].inputSchema["properties"]
        assert "interrupt_owner" not in tools["notify"].inputSchema["properties"]
        assert "message" in tools["notify"].inputSchema["properties"]


async def test_accepted_source_scope_and_runtime_binding_never_leak_attention(monkeypatch):
    from butlers.core import fact_authority as facts
    from butlers.core.attention_urgency import accepted_source_origin
    from butlers.guards import _McpRuntimeSessionGuard
    from tests.daemon.test_mcp_runtime_session_guard import (
        _discard_send,
        _empty_receive,
        _http_scope,
    )

    entity = uuid4()
    row = {
        "request_context": {
            "source_channel": "email",
            "source_sender_identity": " no-reply@accounts.google.com ",
        },
        "normalized_text": "Subject: Your password was changed",
        "raw_payload": {
            "headers": [
                {
                    "name": "Authentication-Results",
                    "value": "mx.example.test; dkim=pass header.d=accounts.google.com; "
                    "dmarc=pass header.from=accounts.google.com",
                }
            ],
        },
    }
    accepted = accepted_source_origin(row, entity)
    assert accepted.entity_id == entity and accepted.sender_domain == "accounts.google.com"
    assert accepted.security_kind == "password_changed"
    # The real existing authentication parser refuses missing/failed headers;
    # merely spelling a trusted sender or a security subject grants nothing.
    for raw in (
        {},
        {
            "headers": [
                {
                    "name": "Authentication-Results",
                    "value": "mx.example.test; dmarc=fail header.from=accounts.google.com",
                }
            ]
        },
    ):
        refused = accepted_source_origin(row | {"raw_payload": raw}, entity)
        assert refused.sender_domain is None and refused.security_kind is None
    origin = AttentionOrigin(
        namespace="general", task_id=uuid4(), original_next_run_at=datetime.now(UTC)
    )
    observed = []
    token = _current_origin.set(origin)
    try:
        invocation = await facts.register_invocation(
            "general",
            "synthetic-session",
            source_endpoint=None,
            routed=False,
        )

        async def app(_scope, _receive, _send):
            observed.append(_current_origin.get())
            raise RuntimeError("Synthetic handler refusal")

        guard = _McpRuntimeSessionGuard(app, butler_name="general")
        with pytest.raises(RuntimeError, match="Synthetic handler refusal"):
            await guard(
                _http_scope(headers=[(b"x-butlers-fact-invocation", invocation.encode())]),
                _empty_receive,
                _discard_send,
            )
        assert observed[-1] == origin
        assert _current_origin.get() == origin
        with pytest.raises(RuntimeError):
            await guard(
                _http_scope(query_string=b"runtime_session_id=synthetic-session"),
                _empty_receive,
                _discard_send,
            )
        assert observed[-1] is None  # diagnostic locator cannot inherit authority
        facts.settle_invocation(invocation)

        @facts.accepted_source_scope
        async def source():
            assert _current_origin.get() is None
            _current_origin.set(AttentionOrigin(channel="email"))
            raise RuntimeError("Synthetic source refusal")

        with pytest.raises(RuntimeError):
            await source()
        assert _current_origin.get() == origin
    finally:
        _current_origin.reset(token)

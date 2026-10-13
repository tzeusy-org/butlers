"""Actual core-chain fast runtime fences, operational recovery, and hermetic latency.

These require migrated PostgreSQL. Collection and fake-store tests cannot earn
their role, race, durable receipt, bootstrap or process-restart assertions.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import math
import shutil
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP

from butlers.core import memory_hooks
from butlers.core.dashboard_fast_answer_adapters import ConciergeTarget
from butlers.core.dashboard_fast_answer_catalog import validate_catalog_evidence
from butlers.core.dashboard_fast_answer_runtime import DashboardFastAnswerRuntime
from butlers.core.dashboard_fast_answer_store import FastAnswerFence, FastAnswerStore, reply_payload
from butlers.core.dashboard_turns import bind_ingress, claim_ingress, open_turn, request_cancel
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


class _SwitchboardRolePool:
    """Real per-acquisition role; never let a migration-owner pool prove runtime ACLs."""

    def __init__(self, pool):
        self.pool = pool

    @asynccontextmanager
    async def acquire(self):
        async with self.pool.acquire() as conn:
            await conn.execute("SET ROLE butler_switchboard_rw")
            try:
                yield conn
            finally:
                await conn.execute("RESET ROLE")

    async def fetchval(self, query, *args):
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def fetch(self, query, *args):
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)


@pytest_asyncio.fixture(loop_scope="function")
async def fast_db(postgres_container):
    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core"],
        {"core": "switchboard"},
    )
    pool = await asyncpg.create_pool(url, min_size=1, max_size=6, init=register_jsonb_codec)
    try:
        yield pool, _SwitchboardRolePool(pool), url
    finally:
        await pool.close()


async def _seed_turn(pool):
    conversation, message, request = uuid4(), uuid4(), uuid4()
    await pool.execute(
        "INSERT INTO public.dashboard_conversations(id,butler_name,title,status) "
        "VALUES($1,'switchboard','synthetic fast question','active')",
        conversation,
    )
    await pool.execute(
        "INSERT INTO public.dashboard_messages(id,conversation_id,role,content) "
        "VALUES($1,$2,'user','How is the fleet?')",
        message,
        conversation,
    )
    assert (
        await open_turn(pool, message_id=message, conversation_id=conversation)
    ).outcome == "ready"
    assert (await claim_ingress(pool, message_id=message)).outcome == "dispatch"
    assert (await bind_ingress(pool, message_id=message, request_id=request)).outcome == "accepted"
    return conversation, message, request


async def _clock(pool, stamp):
    # Test-owned migrated database only, through the actual clock entrypoint.
    assert isinstance(stamp, datetime) and stamp.tzinfo is not None
    literal = stamp.isoformat()
    await pool.execute(
        "CREATE OR REPLACE FUNCTION public.dashboard_fast_answer_now() RETURNS timestamptz "
        "LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$ SELECT '"
        + literal
        + "'::timestamptz $$"
    )


async def _state(pool, message):
    # Independent acquisition/readback after each owning function has committed.
    return dict(
        await pool.fetchrow(
            "SELECT * FROM public.dashboard_fast_answer_runtimes WHERE message_id=$1", message
        )
    )


async def test_core267_real_roles_stop_reply_races_and_legacy_fence(fast_db, monkeypatch):
    # REQ-dashboard-fast-answer-002 / -005 / -006: actual writes and readback.
    pool, role_pool, _url = fast_db
    store = FastAnswerStore(role_pool)
    # Replay the actual installer, then its exact downgrade and restoration in
    # this test-owned migrated database. This is SQL component evidence, not
    # an assertion that another schema's full Alembic chain has run.
    migration_path = (
        Path(__file__).parents[2]
        / "alembic/versions/core/core_267_dashboard_fast_answer_runtime.py"
    )
    spec = importlib.util.spec_from_file_location("fast_answer_migration_test", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = []
    monkeypatch.setattr(migration, "op", SimpleNamespace(execute=statements.append))
    migration.upgrade()
    for statement in statements:
        await pool.execute(statement)
    conversation, message, request = await _seed_turn(pool)
    assert await pool.fetchval("SELECT public.dashboard_turn_intent_lane($1,NULL)", message) is None
    statements.clear()
    migration.downgrade()
    for statement in statements:
        await pool.execute(statement)
    assert (
        await pool.fetchval("SELECT to_regclass('public.dashboard_fast_answer_runtimes')") is None
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.dashboard_conversation_turns WHERE message_id=$1", message
        )
        == 1
    )
    statements.clear()
    migration.upgrade()
    for statement in statements:
        await pool.execute(statement)
    assert await pool.fetchval("SELECT public.dashboard_turn_intent_lane($1,NULL)", message) is None
    owner, session = uuid4(), uuid4()
    registration = await store.register(message, request, session, owner)
    fence = FastAnswerFence(message, session, owner, registration["lease_generation"])
    assert registration["outcome"] == "registered"
    assert (await _state(pool, message))["heartbeat_at"] == datetime.fromisoformat(
        registration["heartbeat_at"]
    )
    await store.intent(message, "answer")
    with pytest.raises(asyncpg.UniqueViolationError):
        await store.intent(message, "non_answer")
    assert (
        await pool.fetchval("SELECT public.dashboard_turn_intent_lane($1,NULL)", message)
        == "answer"
    )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await role_pool.fetch(
            "SELECT * FROM public.dashboard_turn_register_session($1,$2,$3,'switchboard',"
            "'classification')",
            message,
            uuid4(),
            request,
        )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.dashboard_conversation_turn_sessions WHERE message_id=$1",
            message,
        )
        == 1
    )
    for operation in (
        "SELECT * FROM public.dashboard_turn_release_invoke($1,$2)",
        "SELECT * FROM public.dashboard_turn_complete_session($1,$2,true)",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await role_pool.fetch(operation, message, session)
    assert (
        await pool.fetchval(
            "SELECT invoke_active FROM public.dashboard_conversation_turn_sessions WHERE session_id=$1",
            session,
        )
        is True
    )
    sources = [
        {
            "kind": "view",
            "ref": "concierge.v_fleet_sessions",
            "as_of": datetime.now(UTC).isoformat(),
        }
    ]
    payload = reply_payload(message, conversation, "Two sessions are active.", sources)
    assert (await store.control(fence, "reply_claim", {"digest": payload["digest"]}))[
        "outcome"
    ] == "accepted"
    winner = await store.control(fence, "reply", payload)
    assert winner["outcome"] == "completed"
    assert (await store.control(fence, "reply", payload))["outcome"] == "completed"
    changed = {**payload, "message": "Changed"}
    assert (await store.control(fence, "reply", changed))["outcome"] == "conflict"
    assert (await request_cancel(pool, message_id=message)).outcome == "finished"
    row = await pool.fetchrow(
        "SELECT * FROM public.dashboard_messages WHERE id=$1", UUID(payload["reply_id"])
    )
    assert row["content"] == payload["message"] and row["sources"] == payload["source_labels"]
    assert (await _state(pool, message))["source_envelopes"] == sources
    assert not await pool.fetchval(
        "SELECT invoke_active FROM public.dashboard_conversation_turn_sessions WHERE session_id=$1",
        session,
    )

    # Stop first, concurrent repeated intent, and reply claim vs Stop one winner.
    for order in ("stop_first", "reply_first", "race"):
        conversation, message, request = await _seed_turn(pool)
        session = uuid4()
        registered = await store.register(message, request, session, owner)
        fence = FastAnswerFence(message, session, owner, registered["lease_generation"])
        payload = reply_payload(message, conversation, "Healthy.", sources)
        await store.control(fence, "reply_claim", {"digest": payload["digest"]})
        if order == "stop_first":
            stops = await asyncio.gather(
                *(request_cancel(pool, message_id=message) for _ in range(2))
            )
            assert all(item.outcome == "cancelling" for item in stops)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await role_pool.fetch(
                    "SELECT * FROM public.dashboard_turn_acknowledge_cancel($1,$2)",
                    message,
                    session,
                )
            assert (await store.control(fence, "reply", payload))["outcome"] == "stopped"
            assert (await store.control(fence, "cancelled"))["outcome"] == "cancelled"
            assert (await request_cancel(pool, message_id=message)).outcome == "cancelled"
        elif order == "reply_first":
            assert (await store.control(fence, "reply", payload))["outcome"] == "completed"
            assert (await request_cancel(pool, message_id=message)).outcome == "finished"
        else:
            reply, stop = await asyncio.gather(
                store.control(fence, "reply", payload), request_cancel(pool, message_id=message)
            )
            assert (reply["outcome"], stop.outcome) in {
                ("completed", "finished"),
                ("stopped", "cancelling"),
            }
        count = await pool.fetchval(
            "SELECT count(*) FROM public.dashboard_messages WHERE id=$1", UUID(payload["reply_id"])
        )
        assert count == (
            0
            if (await _state(pool, message))["state"] == "active"
            else int((await _state(pool, message))["state"] == "completed")
        )

    # Broad bootstrap-style grants cannot circumvent FORCE RLS or function roles.
    await pool.execute(
        "GRANT ALL ON public.dashboard_fast_answer_runtimes TO butler_switchboard_rw"
    )
    assert await role_pool.fetch("SELECT * FROM public.dashboard_fast_answer_runtimes") == []
    assert (
        await role_pool.fetch(
            "UPDATE public.dashboard_fast_answer_runtimes SET phase='reads' RETURNING message_id"
        )
        == []
    )
    async with pool.acquire() as conn:
        await conn.execute("SET ROLE butler_concierge_rw")
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetchval("SELECT public.dashboard_fast_answer_reconcile($1)", uuid4())
        finally:
            await conn.execute("RESET ROLE")
    # The role table update is a zero-row RLS operation; planting an insert must refuse.
    async with role_pool.acquire() as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO public.dashboard_fast_answer_runtimes "
                "(message_id,session_id,owner_instance_id,heartbeat_at,lease_expires_at) "
                "VALUES($1,$2,$3,now(),now())",
                message,
                uuid4(),
                uuid4(),
            )


async def test_core267_operational_lease_reconciliation_and_receipt_precedence(fast_db):
    # REQ-dashboard-fast-answer-002: startup/periodic entrypoints, both durable winners.
    pool, role_pool, _url = fast_db
    store = FastAnswerStore(role_pool)
    t0 = datetime(2026, 10, 12, tzinfo=UTC)
    await _clock(pool, t0)
    conversation, message, request = await _seed_turn(pool)
    owner, session = uuid4(), uuid4()
    await store.register(message, request, session, owner)
    fence = FastAnswerFence(message, session, owner, 1)
    replacement = DashboardFastAnswerRuntime(store, object(), sweep_seconds=0.01)
    await replacement.start()
    try:
        # Healthy heartbeats across minute 15; actual periodic sweep never takes over.
        for second in range(20, 941, 20):
            await _clock(pool, t0 + timedelta(seconds=second))
            assert (await store.control(fence, "heartbeat"))["outcome"] == "accepted"
            await asyncio.sleep(0.011)
        healthy = await _state(pool, message)
        assert healthy["state"] == "active" and healthy["lease_generation"] == 1
        assert healthy["reconcile_anchor_expires_at"] is None
        last_expiry = healthy["lease_expires_at"]
        await _clock(pool, last_expiry - timedelta(microseconds=1))
        await replacement.sweep()
        assert (await _state(pool, message))["reconcile_anchor_expires_at"] is None
        await _clock(pool, last_expiry)
        await asyncio.sleep(0.022)
        claimed = await _state(pool, message)
        assert claimed["lease_generation"] == 2
        assert claimed["reconcile_anchor_expires_at"] == last_expiry
        deadline = last_expiry + timedelta(minutes=15)
        assert claimed["reconcile_deadline_at"] == deadline
        for action, data in (
            ("heartbeat", {}),
            ("advance", {"phase": "reads"}),
            ("fallback", {}),
            ("failed", {}),
            ("cancelled", {}),
            ("reply_claim", {"digest": "0" * 64}),
            ("reply", {}),
        ):
            assert (await store.control(fence, action, data))["outcome"] == "fenced"
        await _clock(pool, deadline - timedelta(microseconds=1))
        await replacement.sweep()
        assert (await _state(pool, message))["state"] == "pending_reconciliation"
        await _clock(pool, deadline)
        await replacement.sweep()
        terminal = await _state(pool, message)
        assert terminal["state"] == "ambiguous"
        assert terminal["failure_reason"] == "fast_answer_runtime_outcome_unknown"
        await _clock(pool, deadline + timedelta(seconds=60))
        await replacement.sweep()
        assert (await _state(pool, message))["reconcile_deadline_at"] == deadline
        assert (await _state(pool, message))["updated_at"] == terminal["updated_at"]
    finally:
        await replacement.close()

    # An accepted heartbeat before expiry prevents a new claim; an expired claim
    # first makes its predecessor's heartbeat fail. Repeated replacement startup
    # and an actual store outage cannot recalculate the original L/D.
    await _clock(pool, t0)
    conversation, message, request = await _seed_turn(pool)
    session = uuid4()
    await store.register(message, request, session, owner)
    fence = FastAnswerFence(message, session, owner, 1)
    await _clock(pool, t0 + timedelta(seconds=59))
    await asyncio.gather(store.control(fence, "heartbeat"), replacement.sweep())
    assert (await _state(pool, message))["lease_generation"] == 1
    expiry = (await _state(pool, message))["lease_expires_at"]
    await _clock(pool, expiry)
    outcomes = await asyncio.gather(store.control(fence, "heartbeat"), replacement.sweep())
    assert outcomes[0]["outcome"] == "fenced"
    captured = await _state(pool, message)
    old_function = await pool.fetchval(
        "SELECT pg_get_functiondef('public.dashboard_fast_answer_reconcile(uuid)'::regprocedure)"
    )
    await pool.execute(
        "CREATE OR REPLACE FUNCTION public.dashboard_fast_answer_reconcile(p_owner uuid) "
        "RETURNS SETOF jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$ BEGIN "
        "RAISE EXCEPTION 'controlled unavailable store'; END $$"
    )
    try:
        with pytest.raises(asyncpg.RaiseError):
            await replacement.sweep()
        assert (await _state(pool, message)) == captured
    finally:
        await pool.execute(old_function)
    await _clock(pool, expiry + timedelta(minutes=16))
    restarted = DashboardFastAnswerRuntime(store, object())
    await restarted.start()
    await restarted.close()
    final = await _state(pool, message)
    assert final["state"] == "ambiguous" and final["reconcile_anchor_expires_at"] == expiry
    assert final["reconcile_deadline_at"] == expiry + timedelta(minutes=15)

    # Real committed terminal receipts win after an unresolved runtime projection.
    # The test owner deliberately loses only that projection; no product path
    # can partially commit the atomic reply. The operational sweeper must still
    # preserve actual receipt truth rather than infer ambiguity from expiry.
    for settled in ("completed", "cancelled", "failed"):
        await _clock(pool, t0)
        conversation, message, request = await _seed_turn(pool)
        session = uuid4()
        await store.register(message, request, session, owner)
        fence = FastAnswerFence(message, session, owner, 1)
        payload = reply_payload(
            message,
            conversation,
            "Receipt wins.",
            [{"kind": "view", "ref": "concierge.v_fleet_sessions", "as_of": t0.isoformat()}],
        )
        if settled == "completed":
            await store.control(fence, "reply_claim", {"digest": payload["digest"]})
            assert (await store.control(fence, "reply", payload))["outcome"] == settled
        elif settled == "cancelled":
            await request_cancel(pool, message_id=message)
            assert (await store.control(fence, "cancelled"))["outcome"] == settled
        else:
            assert (await store.control(fence, "failed"))["outcome"] == settled
        committed = await _state(pool, message)
        await pool.execute(
            "UPDATE public.dashboard_fast_answer_runtimes SET state='pending_reconciliation' "
            "WHERE message_id=$1",
            message,
        )
        await _clock(pool, t0 + timedelta(minutes=17))
        recovered = DashboardFastAnswerRuntime(store, object())
        await recovered.start()
        await recovered.close()
        observed = await _state(pool, message)
        assert observed["state"] == settled
        assert observed["reply_message_id"] == committed["reply_message_id"]
        assert observed["source_envelopes"] == committed["source_envelopes"]
        assert observed["failure_reason"] == committed["failure_reason"]
        assert observed["reconcile_anchor_expires_at"] is None
        assert (
            await pool.fetchval(
                "SELECT terminal_state FROM public.dashboard_conversation_turns WHERE message_id=$1",
                message,
            )
            == settled
        )


async def test_core267_real_process_restart_drives_replacement_supervisor(fast_db):
    # REQ-dashboard-fast-answer-002: a real owning process dies; no provider replay.
    pool, role_pool, url = fast_db
    t0 = datetime(2026, 10, 12, tzinfo=UTC)
    await _clock(pool, t0)
    conversation, message, request = await _seed_turn(pool)
    child_program = r"""
import asyncio,json,sys
from uuid import UUID
import asyncpg
from butlers.core.dashboard_fast_answer_runtime import DashboardFastAnswerRuntime
from butlers.core.dashboard_fast_answer_store import FastAnswerStore
from butlers.db import register_jsonb_codec
async def main():
    data=json.loads(sys.stdin.readline())
    pool=await asyncpg.create_pool(data['url'],min_size=1,max_size=2,
                                  init=register_jsonb_codec,
                                  server_settings={'role':'butler_switchboard_rw'})
    class Dependencies:
        async def models(self,fence):
            print('REGISTERED',flush=True)
            await asyncio.Event().wait()
    runtime=DashboardFastAnswerRuntime(FastAnswerStore(pool),Dependencies())
    await runtime.start()
    await runtime.run(message_id=UUID(data['message']),request_id=UUID(data['request']),
                      conversation_id=UUID(data['conversation']),prompt='synthetic')
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        child_program,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        process.stdin.write(
            json.dumps(
                {
                    "url": url,
                    "message": str(message),
                    "request": str(request),
                    "conversation": str(conversation),
                }
            ).encode()
            + b"\n"
        )
        await process.stdin.drain()
        assert await asyncio.wait_for(process.stdout.readline(), 20) == b"REGISTERED\n"
        child = await _state(pool, message)
        # Keep the owner alive through a lease that expires just before minute 15.
        await _clock(pool, t0 + timedelta(minutes=14, seconds=39))
        child_fence = FastAnswerFence(message, child["session_id"], child["owner_instance_id"], 1)
        await FastAnswerStore(role_pool).control(child_fence, "heartbeat")
        process.kill()
        await process.wait()
        assert process.returncode != 0
        expiry = (await _state(pool, message))["lease_expires_at"]
        replacement = DashboardFastAnswerRuntime(
            FastAnswerStore(role_pool), object(), sweep_seconds=0.01
        )
        await _clock(pool, expiry - timedelta(microseconds=1))
        await replacement.start()
        try:
            assert (await _state(pool, message))["lease_generation"] == 1
            await _clock(pool, expiry)
            await asyncio.sleep(0.03)
            pending = await _state(pool, message)
            assert pending["lease_generation"] == 2
            assert pending["owner_instance_id"] != child["owner_instance_id"]
            assert pending["reconcile_anchor_expires_at"] == expiry
            await _clock(pool, expiry + timedelta(minutes=15))
            await asyncio.sleep(0.03)
            assert (await _state(pool, message))["state"] == "ambiguous"
            assert not replacement._live  # real supervisor never reconstructs executable work
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM public.dashboard_messages WHERE conversation_id=$1 "
                    "AND role='assistant'",
                    conversation,
                )
                == 0
            )
        finally:
            await replacement.close()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.slow
async def test_hermetic_twenty_question_database_benchmark_and_catalog_corpus(
    fast_db, tmp_path, monkeypatch, record_property
):
    # REQ-dashboard-fast-answer-003 / -006: seeded DB, fixed latency, no live provider.
    pool, role_pool, _url = fast_db
    questions = [
        "How is the fleet?",
        "Which butlers are running?",
        "How many sessions today?",
        "Any failed sessions today?",
        "What was the latest fleet activity?",
        "What did the fleet spend today?",
        "How has fleet spend changed this week?",
        "Which model used the most tokens?",
        "Which butler had the highest spend?",
        "Which sessions used the most tokens?",
        "How many sessions did health run?",
        "What is the latest switchboard activity?",
        "What is the finance daemon status?",
        "How many sessions came from schedules?",
        "How many sessions came from routes?",
        "Were any insight deliveries recorded?",
        "Which fleet errors are recent?",
        "Show recent runtime activity",
        "What was the latest completed session?",
        "How many sessions are active across the fleet?",
    ]
    corpus_path = Path(__file__).parents[1] / "fixtures" / "fast_answer_catalog_corpus.json"
    corpus_bytes = corpus_path.read_bytes()
    corpus = json.loads(corpus_bytes)
    confusion, selected, wrong, per_owner = {}, 0, 0, {}
    for case in corpus:
        outcome = validate_catalog_evidence(case["rows"], eligible_owners=frozenset({"concierge"}))
        observed = outcome.selected_owner
        key = f"{case['expected_owner']}->{observed}"
        confusion[key] = confusion.get(key, 0) + 1
        per_owner[case["expected_owner"] or "none"] = (
            per_owner.get(case["expected_owner"] or "none", 0) + 1
        )
        selected += observed is not None
        wrong += observed is not None and observed != case["expected_owner"]
        assert observed == case["expected_owner"]
    assert wrong == 0 and selected > 0
    row = corpus[0]["rows"]
    # Seed real database rows; this bounded corpus is algorithm evidence, not a
    # live embedding/search quality claim. The live hook still owns held policy.
    await pool.execute(
        "INSERT INTO switchboard.state(key,value) VALUES($1,$2::jsonb)",
        "fast-answer-hermetic-catalog",
        row,
    )

    async def catalog(query):
        assert query in questions
        return await pool.fetchval(
            "SELECT value FROM switchboard.state WHERE key=$1", "fast-answer-hermetic-catalog"
        )

    # Registered synthetic MCP read, seeded real DB. This measures orchestration;
    # the actual DashboardReadModule/RFC0030 path has independent owning tests.
    server = FastMCP("hermetic-concierge")
    counts = {"reads": 0}

    @server.tool(name="module.states")
    async def module_states():
        return {"dashboard_read": {"enabled": True, "health": "active"}}

    @server.tool()
    async def dashboard_read_fleet_status():
        counts["reads"] += 1
        return {
            "count": await pool.fetchval(
                "SELECT count(*) FROM public.dashboard_messages WHERE role='user'"
            ),
            "source": {
                "kind": "table",
                "ref": "synthetic.hermetic_counter",
                "as_of": datetime.now(UTC).isoformat(),
            },
        }

    from importlib import import_module

    routing = import_module("butlers.tools.switchboard.routing.route")
    endpoint = "http://localhost:9999/mcp"
    monkeypatch.setattr(routing, "receiver_route_cutover_enabled", lambda: False)
    monkeypatch.setattr(
        routing,
        "resolve_routing_target",
        AsyncMock(return_value=({"endpoint_url": endpoint}, None)),
    )
    cli = Mock(side_effect=AssertionError("hermetic fast answer must not spawn a CLI"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", cli)

    class FixedLatencyDependencies:
        def __init__(self):
            self.providers = self.replies = 0
            self.registered_target = None

        async def models(self, fence):
            return "hermetic-fixed-api-pair"

        async def target(self):
            return self.registered_target

        async def classify(self, models, prompt, evidence, target):
            self.providers += 1
            await asyncio.sleep(0.01)
            return {
                "lane": "D",
                "scope": "system",
                "selected_owner": "concierge",
                "question": prompt,
                "reads": [{"name": "dashboard_read_fleet_status"}],
            }

        async def phrase(self, models, question, results):
            self.providers += 1
            await asyncio.sleep(0.01)
            return f"Synthetic count: {results[0]['count']}."

        async def reply(self, fence, payload):
            self.replies += 1
            return await store.control(fence, "reply", payload)

        async def decline(self, question):
            raise AssertionError("seeded corpus must not decline")

    store = FastAnswerStore(role_pool)
    deps = FixedLatencyDependencies()
    runtime = DashboardFastAnswerRuntime(store, deps)
    memory_hooks.register_fast_answer_catalog("switchboard", catalog)
    times = []
    try:
        async with Client(server) as client:
            deps.registered_target = ConciergeTarget(
                SimpleNamespace(execute=AsyncMock()),
                endpoint,
                client,
                {tool.name: tool for tool in await client.list_tools()},
                True,
            )
            for question in questions:
                conversation, message, request = await _seed_turn(pool)
                started = time.perf_counter()
                result = await runtime.run(
                    message_id=message,
                    request_id=request,
                    conversation_id=conversation,
                    prompt=question,
                )
                times.append(time.perf_counter() - started)
                assert result.outcome == "completed"
    finally:
        memory_hooks.unregister_fast_answer_catalog("switchboard", catalog)
        await runtime.close()
    p95 = sorted(times)[math.ceil(len(times) * 0.95) - 1]
    report = {
        "scope": "hermetic orchestration; no live-provider latency",
        "samples": 20,
        "stub_latency_seconds": 0.01,
        "wall_seconds": times,
        "p50": sorted(times)[9],
        "p95": p95,
        "provider_calls": deps.providers,
        "registered_plan_reads": counts["reads"],
        "replies": deps.replies,
        "cli_spawns": cli.call_count,
        "corpus_digest": hashlib.sha256(corpus_bytes).hexdigest(),
        "result_limit": 3,
        "rule_version": "same-owner-v1",
        "selected": selected,
        "wrong_owner": wrong,
        "per_owner": per_owner,
        "confusion_matrix": confusion,
    }
    (tmp_path / "fast-answer-hermetic-benchmark.json").write_text(json.dumps(report, indent=2))
    record_property("fast_answer_hermetic_benchmark", json.dumps(report, sort_keys=True))
    assert deps.providers == 40 and counts["reads"] == deps.replies == 20 and cli.call_count == 0
    assert p95 < 3

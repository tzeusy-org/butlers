"""Controlled retention protocol: software boundaries and real migrated PG rehearals."""

# Tests REQ-memory-retention-policy-012.
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.modules.memory import retention_drain as drain
from butlers.testing.migration import create_migrated_test_db


def plan(database="test_retention_unit"):
    now = datetime.now(UTC)
    return drain.Plan(
        uuid4(),
        "isolated",
        database,
        "fixture-owner",
        now - timedelta(days=1),
        now - timedelta(minutes=1),
        now + timedelta(minutes=9),
        1,
        10,
        10,
        60,
        2000,
        500,
        source_sha256=drain.source_identity(),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "change",
    [
        {"environment": "prod"},
        {"schema": "public"},
        {"role": "postgres"},
        {"database": "x;DELETE"},
        {"operator": "raw content with spaces"},
        {"cutoff": datetime.now()},
        {"total_rows": True},
        {"max_batches": 0},
        {"batch_rows": 1001},
        {"statement_ms": 30001},
        {"duration_seconds": 301},
    ],
)
def test_plan_refuses_unsafe_or_unbounded_bindings(change):
    with pytest.raises(drain.Refused):
        replace(plan(), **change).validate()


@pytest.mark.unit
def test_fixed_plan_digest_and_closed_output():
    p = plan()
    p.validate()
    assert p.digest == replace(p).digest
    assert replace(p, cutoff=p.cutoff - timedelta(seconds=1)).digest != p.digest
    assert "content" not in json.dumps(p.public())


@pytest.fixture
def retention_db(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        f"test_retention_{uuid4().hex[:10]}",
        chains=["core", "memory"],
        schemas={"core": "switchboard", "memory": "switchboard"},
    )


async def seed(url):
    conn = await asyncpg.connect(url)
    ids = []
    for trigger in ("external", "external", "schedule:consolidation"):
        sid, eid = uuid4(), uuid4()
        await conn.execute(
            "INSERT INTO switchboard.sessions(id,prompt,trigger_source,request_id) "
            "VALUES($1,'synthetic private prompt',$2,'fixture')",
            sid,
            trigger,
        )
        await conn.execute(
            "INSERT INTO switchboard.episodes(id,butler,session_id,content,"
            "expires_at,consolidation_status) VALUES($1,'switchboard',$2,"
            "'synthetic private source',now()-interval '30 days','consolidated')",
            eid,
            sid,
        )
        ids.append(eid)
    await conn.execute(
        "INSERT INTO switchboard.facts(subject,predicate,content,source_episode_id) "
        "VALUES('owner','fixture','derived fixture',$1)",
        ids[0],
    )
    await conn.execute(
        "INSERT INTO switchboard.rules(content,source_episode_id) "
        "VALUES('derived synthetic rule',$1)",
        ids[0],
    )
    await conn.execute(
        "INSERT INTO switchboard.memory_links(source_type,source_id,target_type,"
        "target_id,relation) VALUES('episode',$1,'episode',$2,'related_to')",
        ids[0],
        ids[1],
    )
    await conn.execute(
        "INSERT INTO switchboard.scheduled_tasks(name,cron,prompt,enabled) "
        "VALUES('retention-sentinel','0 0 * * *','synthetic sentinel',false)"
    )
    await conn.execute("SET ROLE butler_switchboard_rw")
    return conn, ids


@pytest.mark.integration
async def test_no_delete_rehearsal_and_database_write_denial(retention_db, monkeypatch):
    conn, ids = await seed(retention_db)
    p = plan(await conn.fetchval("SELECT current_database()"))
    original = drain._snapshot
    try:
        # Actual cutoff boundaries: current-time cleanup would misclassify the
        # pending six-day-old row because the fixed cutoff is a day in the past.
        for offset, status in ((0, "consolidated"), (6, "pending"), (8, "pending")):
            await conn.execute(
                "INSERT INTO switchboard.episodes(butler,session_id,content,"
                "expires_at,consolidation_status) SELECT 'switchboard',session_id,"
                "'synthetic boundary',$2,$3 FROM switchboard.episodes WHERE id=$1",
                ids[0],
                p.cutoff - timedelta(days=offset),
                status,
            )
        before = await conn.fetchval("SELECT count(*) FROM switchboard.episodes")
        good = await drain.dry_run(conn, p)
        assert good["status"] == "READ_ONLY"
        assert good["eligible"] == 3 and good["excluded"] == 1 and good["unknown"] == 0
        assert len(good["candidate_fingerprint"]) == 64
        assert "synthetic private" not in json.dumps(good)
        assert all(str(eid) not in json.dumps(good) for eid in ids)
        assert await conn.fetchval("SELECT count(*) FROM switchboard.episodes") == before
        assert await conn.fetchval("SELECT count(*) FROM switchboard.episode_tombstones") == 0

        async def attempted_write(c, p):
            await c.execute("DELETE FROM switchboard.episodes")
            return await original(c, p)

        monkeypatch.setattr(drain, "_snapshot", attempted_write)
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await drain.dry_run(conn, p)
        assert await conn.fetchval("SELECT count(*) FROM switchboard.episodes") == before
        assert not await drain.readback(conn, p)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM switchboard.scheduled_tasks "
                "WHERE name='retention-sentinel' AND enabled=false"
            )
            == 1
        )
    finally:
        await conn.close()


@pytest.mark.integration
async def test_atomic_recovery_audit_failure_replay_and_source_expired(retention_db, monkeypatch):
    conn, ids = await seed(retention_db)
    p = plan(await conn.fetchval("SELECT current_database()"))
    try:
        before = await drain.dry_run(conn, p)
        event = drain._event

        async def fail_second(c, p, payload):
            if payload["sequence"] == 2:
                await c.execute("SELECT 1/0")
            await event(c, p, payload)

        monkeypatch.setattr(drain, "_event", fail_second)
        result = await drain.rehearse_batches(conn, p, before["candidate_fingerprint"])
        assert result == {"status": "ROLLED_BACK", "committed": 1, "reason": "batch_failed"}
        from butlers.modules.memory.storage import get_links

        links = await get_links(conn, "episode", ids[0], memory_schema="switchboard")
        assert links[0]["source_episode_status"] == "expired"
        assert links[0]["target_episode_status"] == "available"
        records = await drain.readback(conn, p)
        assert [r["sequence"] for r in records] == [0, 1]
        assert records[-1]["committed_total"] == 1
        assert await conn.fetchval("SELECT count(*) FROM switchboard.episodes") == 2
        assert await conn.fetchval("SELECT count(*) FROM switchboard.episode_tombstones") == 1
        assert await conn.fetchval("SELECT count(*) FROM switchboard.memory_links") == 1
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM switchboard.facts WHERE source_episode_id=$1", ids[0]
            )
            == 1
        )
        with pytest.raises(drain.Refused, match="run_replayed"):
            await drain.rehearse_batches(conn, p, before["candidate_fingerprint"])
        assert await conn.fetchval("SELECT source_episode_id FROM switchboard.rules") == ids[0]
        monkeypatch.setattr(drain, "_event", event)
        next_plan = replace(p, run_id=uuid4())
        next_state = await drain.dry_run(conn, next_plan)
        completed = await drain.rehearse_batches(
            conn, next_plan, next_state["candidate_fingerprint"]
        )
        assert completed["status"] == "COMMITTED" and completed["committed"] == 1
        assert await conn.fetchval("SELECT count(*) FROM switchboard.episodes") == 1
        assert await conn.fetchval("SELECT count(*) FROM switchboard.episode_tombstones") == 2
        assert await conn.fetchval("SELECT count(*) FROM switchboard.memory_links") == 1
        links = await get_links(conn, "episode", ids[0], memory_schema="switchboard")
        assert links[0]["source_episode_status"] == "expired"
        assert links[0]["target_episode_status"] == "expired"
    finally:
        await conn.close()


@pytest.mark.integration
async def test_unknown_drift_fence_cancel_and_live_refusal(retention_db, monkeypatch):
    import asyncio

    conn, ids = await seed(retention_db)
    other = await asyncpg.connect(retention_db)
    await other.execute("SET ROLE butler_switchboard_rw")
    p = plan(await conn.fetchval("SELECT current_database()"))
    try:
        original = await drain.dry_run(conn, p)
        await other.fetchval("SELECT pg_advisory_lock($1)", drain.LOCK_KEY)
        with pytest.raises(drain.Refused, match="operator_busy"):
            await drain.dry_run(conn, p)
        await other.fetchval("SELECT pg_advisory_unlock($1)", drain.LOCK_KEY)
        await conn.execute(
            "UPDATE switchboard.sessions SET trigger_source='fixture-unrecognized' "
            "WHERE id=(SELECT session_id FROM switchboard.episodes WHERE id=$1)",
            ids[0],
        )
        unrecognized = await drain.dry_run(conn, p)
        assert unrecognized["status"] == "REFUSED" and unrecognized["unknown"] == 1
        await conn.execute(
            "UPDATE switchboard.sessions SET trigger_source='external' "
            "WHERE id=(SELECT session_id FROM switchboard.episodes WHERE id=$1)",
            ids[0],
        )
        await conn.execute("UPDATE switchboard.episodes SET session_id=NULL WHERE id=$1", ids[0])
        unknown = await drain.dry_run(conn, p)
        assert unknown["status"] == "REFUSED" and unknown["reason"] == "unknown_provenance"
        assert unknown["unknown"] == 1 and unknown["eligible"] == 1 and unknown["excluded"] == 1
        assert len(unknown["candidate_fingerprint"]) == 64
        assert "synthetic private" not in json.dumps(unknown)
        assert all(str(eid) not in json.dumps(unknown) for eid in ids)
        with pytest.raises(drain.Refused, match="unknown_provenance"):
            await drain.rehearse_batches(conn, p, original["candidate_fingerprint"])
        assert not await drain.readback(conn, p)
        await conn.execute("DELETE FROM switchboard.episodes WHERE id=$1", ids[0])
        with pytest.raises(drain.Refused, match="candidate_drift"):
            await drain.rehearse_batches(conn, p, original["candidate_fingerprint"])
        with pytest.raises(drain.Refused, match="live_delete_authority_unavailable"):
            await drain.rehearse_batches(
                conn, replace(p, environment="butlers-dev"), original["candidate_fingerprint"]
            )
        expired = replace(p, window_end=p.window_start + timedelta(seconds=1))
        with pytest.raises(drain.Refused, match="outside_window"):
            await drain.dry_run(conn, expired)
        oversized = await drain.dry_run(conn, replace(p, total_rows=1))
        assert oversized["status"] == "REFUSED" and oversized["reason"] == "candidate_limit"
        assert oversized["observed"] == 2 and oversized["scope_over_limit"] is True
        assert len(oversized["candidate_fingerprint"]) == 64
        original_snapshot = drain._snapshot

        async def slow_snapshot(c, p):
            await c.execute("SELECT pg_sleep(1)")
            return await original_snapshot(c, p)

        monkeypatch.setattr(drain, "_snapshot", slow_snapshot)
        with pytest.raises(asyncpg.QueryCanceledError):
            await drain.dry_run(conn, replace(p, statement_ms=20))
        entered = asyncio.Event()

        async def blocked_snapshot(c, p):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(drain, "_snapshot", blocked_snapshot)
        task = asyncio.create_task(drain.dry_run(conn, p))
        try:
            await asyncio.wait_for(entered.wait(), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        assert await other.fetchval("SELECT pg_try_advisory_lock($1)", drain.LOCK_KEY)
        await other.fetchval("SELECT pg_advisory_unlock($1)", drain.LOCK_KEY)
        assert not await drain.readback(conn, p)
        assert await conn.fetchval("SELECT count(*) FROM switchboard.episodes") == 2
    finally:
        await conn.close()
        await other.close()


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["lost_ack", "drift_after_commit"])
async def test_actual_commit_then_lost_ack_stops_and_requires_readback(retention_db, mode):
    conn, ids = await seed(retention_db)
    p = plan(await conn.fetchval("SELECT current_database()"))

    other = await asyncpg.connect(retention_db)
    await other.execute("SET ROLE butler_switchboard_rw")

    class LostAck:
        def __getattr__(self, key):
            return getattr(conn, key)

        def transaction(self, **kwargs):
            actual = conn.transaction(**kwargs)
            if kwargs.get("isolation") != "serializable":
                return actual

            class Transaction:
                async def start(self):
                    await actual.start()

                async def rollback(self):
                    await actual.rollback()

                async def commit(self):
                    await actual.commit()
                    if mode == "lost_ack":
                        raise ConnectionError("synthetic ack loss")
                    await other.execute(
                        "UPDATE switchboard.episodes SET expires_at=expires_at-interval '1 hour' "
                        "WHERE id=$1",
                        ids[1],
                    )

            return Transaction()

    try:
        before = await drain.dry_run(conn, p)
        if mode == "lost_ack":
            result = await drain.rehearse_batches(LostAck(), p, before["candidate_fingerprint"])
            assert result == {"status": "UNKNOWN", "committed": 0, "reason": "batch_failed"}
        else:
            with pytest.raises(drain.Refused, match="candidate_drift"):
                await drain.rehearse_batches(LostAck(), p, before["candidate_fingerprint"])
        assert await conn.fetchval("SELECT count(*) FROM switchboard.episodes") == 2
        events = await drain.readback(conn, p)
        assert [e["sequence"] for e in events] == [0, 1]
        assert events[-1]["committed_total"] == 1
        with pytest.raises(drain.Refused, match="run_replayed"):
            await drain.rehearse_batches(conn, p, before["candidate_fingerprint"])
    finally:
        await conn.close()
        await other.close()

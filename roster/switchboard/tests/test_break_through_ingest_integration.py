"""Real-Postgres roundtrip: ingest records one break-through hit per clause and request (bu-s11n0s.8).

The sensor is shadow-only. A clause the owner declared (here a channel clause, written the way
the owner-only store holds it) is matched through the real ``dashboard_attention.evaluate``; the
hit is one durable row and one metadata-only ``switchboard.break_through_hit`` event. A promoted
skip rule for the sender does not hide it, a replay adds nothing, and a missing evaluator never
fails the ingest.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import urlparse

import asyncpg
import pytest

from butlers.roster_imports import roster_module
from butlers.testing.migration import migration_bootstrap_db_url

ingest_v1 = roster_module("switchboard", "tools/ingestion/ingest").ingest_v1

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

HITS = "SELECT clause_id, source_request_id, channel FROM switchboard.break_through_hits"
EVENTS = (
    "SELECT payload FROM public.domain_events WHERE event_type = 'switchboard.break_through_hit'"
)
SUBJECT = "Subject: Boarding gate changed"


@pytest.fixture
def db_url(fresh_db) -> str:
    return fresh_db("core", "switchboard", schemas={"switchboard": "switchboard"})


@pytest.fixture
async def pool(db, db_url):
    """The Switchboard runtime role's pool, the one ingest runs on."""
    return await db.open(db_url, schema="switchboard")


@pytest.fixture
async def admin(db_url, postgres_container):
    """The bootstrap login: the only one that can write the owner-only clause store."""
    name = urlparse(db_url).path.lstrip("/")
    url = migration_bootstrap_db_url(postgres_container, name)
    pool = await asyncpg.create_pool(
        url.replace("postgresql+psycopg2", "postgresql"), min_size=1, max_size=2
    )
    try:
        yield pool
    finally:
        await pool.close()


def _email(message_id: str, *, sender: str = "mum@example.test", body: str = "private body"):
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "email",
            "provider": "gmail",
            "endpoint_identity": "owner@mybutler.test",
        },
        "event": {
            "external_event_id": message_id,
            "external_thread_id": "thread-1",
            "observed_at": "2026-01-15T02:00:00Z",
        },
        "sender": {"identity": sender},
        "payload": {
            "raw": {"snippet": body},
            "normalized_text": f"{SUBJECT}\n{body}",
        },
        "control": {"policy_tier": "default"},
    }


async def _clause(admin: asyncpg.Pool) -> uuid.UUID:
    return await admin.fetchval(
        "INSERT INTO public.break_through_clauses (channel, scope) "
        "VALUES ('email', 'always') RETURNING id"
    )


def _payload(raw: Any) -> dict:
    return json.loads(raw) if isinstance(raw, str) else raw


async def test_ingest_records_one_hit_and_one_metadata_only_event(
    pool: asyncpg.Pool, admin: asyncpg.Pool
) -> None:
    clause = await _clause(admin)

    result = await ingest_v1(pool, _email("<hit@example.test>"))

    assert result.status == "accepted"
    rows = await pool.fetch(HITS)
    assert [(r["clause_id"], r["source_request_id"], r["channel"]) for r in rows] == [
        (clause, result.request_id, "email")
    ]
    events = [_payload(r["payload"]) for r in await pool.fetch(EVENTS)]
    assert len(events) == 1
    assert events[0]["clause_id"] == str(clause)
    assert events[0]["source_request_id"] == str(result.request_id)
    rendered = json.dumps(events[0])
    for forbidden in ("private body", "Boarding", "mum@example.test"):
        assert forbidden not in rendered


async def test_a_replayed_request_adds_no_second_hit_or_event(
    pool: asyncpg.Pool, admin: asyncpg.Pool
) -> None:
    await _clause(admin)
    first = await ingest_v1(pool, _email("<replay@example.test>"))

    from butlers.core.break_through import observe_inbound

    again = await observe_inbound(
        pool,
        channel="email",
        sender_identity="mum@example.test",
        normalized_text=f"{SUBJECT}\nprivate body",
        raw={"snippet": "private body"},
        source_request_id=str(first.request_id),
    )

    assert again is None
    assert await pool.fetchval("SELECT count(*) FROM switchboard.break_through_hits") == 1
    assert len(await pool.fetch(EVENTS)) == 1


async def test_a_revoked_clause_records_nothing(pool: asyncpg.Pool, admin: asyncpg.Pool) -> None:
    clause = await _clause(admin)
    await admin.execute(
        "UPDATE public.break_through_clauses SET revoked_at = now() WHERE id=$1", clause
    )

    result = await ingest_v1(pool, _email("<revoked@example.test>"))

    assert result.status == "accepted"
    assert await pool.fetchval("SELECT count(*) FROM switchboard.break_through_hits") == 0
    assert await pool.fetch(EVENTS) == []


async def test_a_promoted_skip_rule_for_the_sender_does_not_hide_the_hit(
    pool: asyncpg.Pool, admin: asyncpg.Pool
) -> None:
    clause = await _clause(admin)
    await admin.execute(
        "INSERT INTO switchboard.ingestion_rules (scope, rule_type, condition, action, priority) "
        "VALUES ('global', 'sender_domain', $1::jsonb, 'skip', 1)",
        json.dumps({"domain": "example.test", "match": "exact"}),
    )

    result = await ingest_v1(pool, _email("<skipped@example.test>"))

    assert result.status == "accepted"
    assert [r["clause_id"] for r in await pool.fetch(HITS)] == [clause]
    assert len(await pool.fetch(EVENTS)) == 1


async def test_a_dark_evaluator_never_fails_ingest(pool: asyncpg.Pool, admin: asyncpg.Pool) -> None:
    """A deactivated interface is an unavailable evaluator: the message is still accepted."""
    await _clause(admin)
    await admin.execute("SELECT dashboard_attention.deactivate_interface()")

    result = await ingest_v1(pool, _email("<dark@example.test>"))

    assert result.status == "accepted"
    assert await pool.fetchval("SELECT count(*) FROM switchboard.break_through_hits") == 0


async def test_hits_are_append_only_for_the_runtime_role(
    pool: asyncpg.Pool, admin: asyncpg.Pool
) -> None:
    await _clause(admin)
    await ingest_v1(pool, _email("<durable@example.test>"))

    for statement in (
        "UPDATE switchboard.break_through_hits SET channel = 'x'",
        "DELETE FROM switchboard.break_through_hits",
        "TRUNCATE switchboard.break_through_hits",
    ):
        with pytest.raises(asyncpg.PostgresError):
            await pool.execute(statement)
    assert await pool.fetchval("SELECT count(*) FROM switchboard.break_through_hits") == 1

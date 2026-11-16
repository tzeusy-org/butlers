"""Real-Postgres roundtrip: a declared sender reaches the owner through ingest (bu-s11n0s.8).

A clause the owner declared (here a channel clause, written the way the owner-only store holds
it) is matched through the real ``dashboard_attention.evaluate``; the hit is one durable row,
one metadata-only ``switchboard.break_through_hit`` event, and one urgent candidate that the
broker authorizes by the owner's clause and delivers through the production ``notify_fn``
(here a recorder). A promoted skip rule for the sender does not hide it, a replay adds nothing,
an owner reply withdraws the line unsent, and a missing evaluator never fails the ingest.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import asyncpg
import pytest

from butlers.core import break_through
from butlers.roster_imports import roster_module
from butlers.testing.migration import migration_bootstrap_db_url

_ingest_v1 = roster_module("switchboard", "tools/ingestion/ingest").ingest_v1
delivery_cycle = roster_module("switchboard", "tools/insight/broker").delivery_cycle

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

HITS = "SELECT clause_id, source_request_id, channel FROM switchboard.break_through_hits"
EVENTS = (
    "SELECT payload FROM public.domain_events WHERE event_type = 'switchboard.break_through_hit'"
)
SUBJECT = "Subject: Boarding gate changed"


async def ingest_v1(pool: asyncpg.Pool, envelope: dict) -> Any:
    """Ingest, then wait for the background sensor the way a running daemon would have."""
    result = await _ingest_v1(pool, envelope)
    await break_through.drain()
    return result


@pytest.fixture(autouse=True)
def notify():
    """The production insight ``notify_fn``, replaced by a recorder (no real Telegram send)."""
    sent = AsyncMock(return_value={"status": "ok"})
    with patch("butlers.scheduled_jobs._build_switchboard_insight_notify_fn", lambda _pool: sent):
        yield sent


@pytest.fixture
def db_url(fresh_db) -> str:
    return fresh_db("core", "switchboard", schemas={"switchboard": "switchboard"})


@pytest.fixture
async def runtime(db, db_url):
    """The Switchboard runtime role's pool, the one ingest runs on."""
    return await db.open(db_url, schema="switchboard")


@pytest.fixture
async def admin(db_url, postgres_container):
    """The bootstrap login: the only one that can write the owner-only clause store."""
    name = urlparse(db_url).path.lstrip("/")
    url = migration_bootstrap_db_url(postgres_container, name)
    owner = await asyncpg.create_pool(
        url.replace("postgresql+psycopg2", "postgresql"), min_size=1, max_size=2
    )
    try:
        yield owner
    finally:
        await owner.close()


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
    runtime: asyncpg.Pool, admin: asyncpg.Pool
) -> None:
    clause = await _clause(admin)

    result = await ingest_v1(runtime, _email("<hit@example.test>"))

    assert result.status == "accepted"
    rows = await runtime.fetch(HITS)
    assert [(r["clause_id"], r["source_request_id"], r["channel"]) for r in rows] == [
        (clause, result.request_id, "email")
    ]
    events = [_payload(r["payload"]) for r in await runtime.fetch(EVENTS)]
    assert len(events) == 1
    assert events[0]["clause_id"] == str(clause)
    assert events[0]["source_request_id"] == str(result.request_id)
    rendered = json.dumps(events[0])
    for forbidden in ("private body", "Boarding", "mum@example.test"):
        assert forbidden not in rendered


async def test_the_owner_is_told_once_within_the_ingest_and_the_line_is_content_free(
    runtime: asyncpg.Pool, admin: asyncpg.Pool, notify: AsyncMock
) -> None:
    await _clause(admin)

    result = await ingest_v1(runtime, _email("<told@example.test>"))

    notify.assert_awaited_once()
    call = notify.await_args
    assert call is not None
    line = call.args[0]
    assert "messaged you (email)" in line
    for forbidden in ("private body", "Boarding", "mum@example.test"):
        assert forbidden not in line
    row = await runtime.fetchrow(
        "SELECT status, category, priority FROM insight_candidates WHERE dedup_key LIKE $1",
        f"switchboard:break_through:{result.request_id}:%",
    )
    assert (row["status"], row["category"], row["priority"]) == ("delivered", "break_through", 95)
    assert await runtime.fetchval(
        "SELECT published_at IS NOT NULL FROM switchboard.break_through_hits"
    )


async def test_an_owner_reply_in_the_thread_withdraws_the_line_unsent(
    runtime: asyncpg.Pool, admin: asyncpg.Pool, notify: AsyncMock
) -> None:
    await _clause(admin)
    with patch.object(break_through, "_deliver_now", AsyncMock()):
        first = await ingest_v1(runtime, _email("<unanswered@example.test>"))
        reply = _email("<reply@example.test>", sender="owner@mybutler.test")
        reply["sender"]["owner_sender_id"] = "owner@mybutler.test"
        await ingest_v1(runtime, reply)

    await delivery_cycle(runtime, notify_fn=notify, urgent_only=True)

    notify.assert_not_awaited()
    row = await runtime.fetchrow(
        "SELECT status, metadata ->> 'withdrawn_reason' AS why FROM insight_candidates "
        "WHERE dedup_key LIKE $1",
        f"switchboard:break_through:{first.request_id}:%",
    )
    assert (row["status"], row["why"]) == ("withdrawn", "premise_false")


async def test_a_clause_revoked_after_the_hit_sends_nothing(
    runtime: asyncpg.Pool, admin: asyncpg.Pool, notify: AsyncMock
) -> None:
    clause = await _clause(admin)
    with patch.object(break_through, "_deliver_now", AsyncMock()):
        await ingest_v1(runtime, _email("<revoke-after@example.test>"))
    await admin.execute(
        "UPDATE public.break_through_clauses SET revoked_at = now() WHERE id=$1", clause
    )

    await delivery_cycle(runtime, notify_fn=notify, urgent_only=True)

    notify.assert_not_awaited()


async def test_a_lost_announcement_is_retried_and_then_stays_silent(
    runtime: asyncpg.Pool, admin: asyncpg.Pool, notify: AsyncMock
) -> None:
    await _clause(admin)
    with patch.object(break_through, "_publish_event", AsyncMock(side_effect=TimeoutError)):
        await ingest_v1(runtime, _email("<lost@example.test>"))
    assert await runtime.fetchval("SELECT count(*) FROM switchboard.break_through_hits") == 1
    assert await runtime.fetchval("SELECT count(*) FROM insight_candidates") == 0

    # A replay inside the live lease stays silent; once the lease lapses the sweep announces it.
    assert await break_through.retry_unpublished(runtime) == 0
    await runtime.execute(
        "UPDATE switchboard.break_through_hits SET claimed_at = now() - interval '1 hour'"
    )
    assert await break_through.retry_unpublished(runtime) == 1

    notify.assert_awaited_once()
    assert len(await runtime.fetch(EVENTS)) == 1
    assert await break_through.retry_unpublished(runtime) == 0
    notify.assert_awaited_once()


async def test_a_replayed_request_adds_no_second_hit_or_event(
    runtime: asyncpg.Pool, admin: asyncpg.Pool
) -> None:
    await _clause(admin)
    first = await ingest_v1(runtime, _email("<replay@example.test>"))

    from butlers.core.break_through import observe_inbound

    again = await observe_inbound(
        runtime,
        channel="email",
        sender_identity="mum@example.test",
        normalized_text=f"{SUBJECT}\nprivate body",
        raw={"snippet": "private body"},
        source_request_id=str(first.request_id),
    )

    assert again is None
    assert await runtime.fetchval("SELECT count(*) FROM switchboard.break_through_hits") == 1
    assert len(await runtime.fetch(EVENTS)) == 1


async def test_a_revoked_clause_records_nothing(runtime: asyncpg.Pool, admin: asyncpg.Pool) -> None:
    clause = await _clause(admin)
    await admin.execute(
        "UPDATE public.break_through_clauses SET revoked_at = now() WHERE id=$1", clause
    )

    result = await ingest_v1(runtime, _email("<revoked@example.test>"))

    assert result.status == "accepted"
    assert await runtime.fetchval("SELECT count(*) FROM switchboard.break_through_hits") == 0
    assert await runtime.fetch(EVENTS) == []


async def test_a_promoted_skip_rule_for_the_sender_does_not_hide_the_hit(
    runtime: asyncpg.Pool, admin: asyncpg.Pool
) -> None:
    clause = await _clause(admin)
    await admin.execute(
        "INSERT INTO switchboard.ingestion_rules (scope, rule_type, condition, action, priority) "
        "VALUES ('global', 'sender_domain', $1::jsonb, 'skip', 1)",
        json.dumps({"domain": "example.test", "match": "exact"}),
    )

    result = await ingest_v1(runtime, _email("<skipped@example.test>"))

    assert result.status == "accepted"
    assert [r["clause_id"] for r in await runtime.fetch(HITS)] == [clause]
    assert len(await runtime.fetch(EVENTS)) == 1


async def test_a_dark_evaluator_never_fails_ingest(
    runtime: asyncpg.Pool, admin: asyncpg.Pool
) -> None:
    """A deactivated interface is an unavailable evaluator: the message is still accepted."""
    await _clause(admin)
    await admin.execute("SELECT dashboard_attention.deactivate_interface()")

    result = await ingest_v1(runtime, _email("<dark@example.test>"))

    assert result.status == "accepted"
    assert await runtime.fetchval("SELECT count(*) FROM switchboard.break_through_hits") == 0


async def test_hits_are_append_only_apart_from_the_announcement_lease(
    runtime: asyncpg.Pool, admin: asyncpg.Pool
) -> None:
    await _clause(admin)
    await ingest_v1(runtime, _email("<durable@example.test>"))

    for statement in (
        "UPDATE switchboard.break_through_hits SET channel = 'x'",
        "UPDATE switchboard.break_through_hits SET published_at = NULL",
        "DELETE FROM switchboard.break_through_hits",
        "TRUNCATE switchboard.break_through_hits",
    ):
        with pytest.raises(asyncpg.PostgresError):
            await runtime.execute(statement)
    await runtime.execute("UPDATE switchboard.break_through_hits SET claimed_at = now()")
    assert await runtime.fetchval("SELECT count(*) FROM switchboard.break_through_hits") == 1


async def test_a_flood_past_the_hourly_cap_folds_into_one_pending_line(
    runtime: asyncpg.Pool, admin: asyncpg.Pool, notify: AsyncMock
) -> None:
    await _clause(admin)
    for index in range(5):
        # Distinct bodies: identical text from one sender in one hour is a content-hash duplicate
        # that ingest folds before the sensor ever sees it.
        await ingest_v1(
            runtime, _email(f"<flood-{index}@example.test>", body=f"private body {index}")
        )

    assert notify.await_count == break_through.FLOOD_CAP_PER_HOUR
    folds = await runtime.fetch(
        "SELECT message, status FROM insight_candidates WHERE dedup_key LIKE '%:fold'"
    )
    assert [(r["message"], r["status"]) for r in folds] == [
        ("and 2 more from senders you declared (email) - open", "pending")
    ]
    assert (
        await runtime.fetchval(
            "SELECT count(*) FROM switchboard.break_through_hits WHERE published_at IS NOT NULL"
        )
        == 5
    )

"""Anchored reminders follow their event on real Postgres (bu-5ii4gj.10 slice R-S2)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from butlers.modules.calendar.models import AttendeeInfo, AttendeeResponseStatus, EventStatus
from butlers.modules.calendar.reminder_follow import (
    MAX_ANNOUNCE_ATTEMPTS,
    announce_pending_reminder_receipts,
)
from tests.modules.test_calendar_participants import _module, _project
from tests.modules.test_calendar_reminder_anchor import _create, _world

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


_CORE_383 = next(Path("alembic/versions/core").glob("core_383*.py"))


async def _anchored(db):
    pool, _module_, mcp, event = await _world(db)
    source = await pool.fetchval("SELECT id FROM calendar_sources WHERE lane = 'user'")
    await pool.execute("UPDATE calendar_sources SET time_role = 'owner' WHERE id = $1", source)
    created = await _create(
        mcp,
        anchor_event_id="dentist",
        anchor_original_start=event.start_at,
        offset_minutes=-60,
    )
    return pool, source, event, created["event_id"]


def _moved(event, by: timedelta):
    return event.model_copy(update={"start_at": event.start_at + by, "end_at": event.end_at + by})


async def _sync(pool, source, event, *, sweep=True):
    await _project(_module(pool), source, event)
    if sweep:  # the announcement sweep runs once per successful sync pass
        await announce_pending_reminder_receipts(pool, "relationship")


async def _state(pool, reminder_id):
    return await pool.fetchrow(
        "SELECT status, starts_at, metadata FROM calendar_events WHERE id = $1::uuid", reminder_id
    )


async def _candidates(pool):
    return await pool.fetch(
        "SELECT category, lane, premise, message, dedup_key FROM insight_candidates "
        "WHERE category = 'calendar_reminder'"
    )


async def test_a_moved_event_rebases_the_reminder_with_one_receipt_and_one_candidate(db):
    pool, source, event, reminder_id = await _anchored(db)
    moved = _moved(event, timedelta(hours=3))

    await _sync(pool, source, moved)
    await _sync(pool, source, moved)  # a replayed sync changes and announces nothing

    state = await _state(pool, reminder_id)
    assert state["starts_at"] == moved.start_at - timedelta(minutes=60)
    assert state["metadata"]["anchor"]["original_start"] == moved.start_at.isoformat()
    receipts = await pool.fetch("SELECT kind, reason FROM reminder_receipts")
    assert [(r["kind"], r["reason"]) for r in receipts] == [("rebased", "anchor_moved")]
    candidates = await _candidates(pool)
    assert len(candidates) == 1
    assert candidates[0]["premise"] is None and candidates[0]["lane"] == "digest"
    assert candidates[0]["message"].startswith("Reminder moved:")


async def test_an_unmoved_event_leaves_the_reminder_and_announces_nothing(db):
    pool, source, event, reminder_id = await _anchored(db)
    before = await _state(pool, reminder_id)

    await _sync(pool, source, event)

    assert (await _state(pool, reminder_id))["starts_at"] == before["starts_at"]
    assert await pool.fetchval("SELECT count(*) FROM reminder_receipts") == 0
    assert await _candidates(pool) == []


async def test_a_cancelled_event_retires_the_reminder_once(db):
    pool, source, event, reminder_id = await _anchored(db)
    cancelled = event.model_copy(update={"status": EventStatus.cancelled})

    await _sync(pool, source, cancelled)
    await _sync(pool, source, cancelled)

    state = await _state(pool, reminder_id)
    assert state["status"] == "cancelled"
    assert state["metadata"]["cancel_reason"] == "anchor_cancelled"
    assert await pool.fetchval("SELECT count(*) FROM reminder_receipts WHERE kind='retired'") == 1
    (candidate,) = await _candidates(pool)
    assert candidate["message"].startswith("Reminder cancelled:")


async def test_the_owner_declining_retires_the_reminder(db):
    pool, source, event, reminder_id = await _anchored(db)
    declined = event.model_copy(
        update={
            "attendees": [
                AttendeeInfo(
                    email="me@example.com",
                    self=True,
                    response_status=AttendeeResponseStatus.declined,
                )
            ]
        }
    )

    await _sync(pool, source, declined)

    assert (await _state(pool, reminder_id))["status"] == "cancelled"
    assert len(await _candidates(pool)) == 1


async def test_a_reminder_that_is_now_close_falls_back_to_the_digest_while_timed_is_dark(db):
    pool, source, event, reminder_id = await _anchored(db)
    soon = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=90)
    moved = event.model_copy(update={"start_at": soon, "end_at": soon + timedelta(hours=1)})

    await _sync(pool, source, moved)

    (candidate,) = await _candidates(pool)
    assert candidate["lane"] == "digest"
    assert (await _state(pool, reminder_id))["starts_at"] == soon - timedelta(minutes=60)


async def test_an_absolute_reminder_is_untouched_by_a_moved_event(db):
    pool, source, event, _anchored_id = await _anchored(db)
    plain = await pool.fetchval(
        "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, ends_at, "
        "status, metadata, source_butler) SELECT id, 'plain', 'Plain', 'UTC', $1, $2, "
        "'confirmed', '{\"native\": true}'::jsonb, 'relationship' FROM calendar_sources "
        "WHERE source_kind = 'internal_reminders' LIMIT 1 RETURNING id",
        event.start_at - timedelta(hours=1),
        event.start_at - timedelta(minutes=45),
    )

    await _sync(pool, source, _moved(event, timedelta(hours=2)))

    assert (await _state(pool, plain))["starts_at"] == event.start_at - timedelta(hours=1)


async def test_a_replayed_change_applies_once(db):
    from butlers.modules.calendar import reminder_follow

    pool, _source_id, event, reminder_id = await _anchored(db)
    new_start = event.start_at + timedelta(hours=3)
    async with pool.acquire() as conn:
        first = await reminder_follow._apply(conn, reminder_id, event_start=new_start, retire=False)
        again = await reminder_follow._apply(conn, reminder_id, event_start=new_start, retire=False)

    assert first is not None and again is None
    assert await pool.fetchval("SELECT count(*) FROM reminder_receipts") == 1


async def test_an_event_that_flips_back_and_forth_rebases_every_time(db):
    pool, source, event, reminder_id = await _anchored(db)

    for by in (1, 0, 1):  # 10:00 -> 11:00 -> 10:00 -> 11:00
        await _sync(pool, source, _moved(event, timedelta(hours=by)))
        state = await _state(pool, reminder_id)
        assert state["starts_at"] == event.start_at + timedelta(hours=by) - timedelta(minutes=60)

    assert await pool.fetchval("SELECT count(*) FROM reminder_receipts") == 3
    assert len(await _candidates(pool)) == 3


async def test_a_partner_calendars_decline_never_retires_the_owners_reminder(db):
    pool, source, event, reminder_id = await _anchored(db)
    partner = await pool.fetchval(
        "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id, "
        "display_name, writable, time_role, metadata) VALUES ('provider:google:partner', "
        "'provider_event', 'user', 'google', 'partner', 'Partner', false, 'household_member', "
        "'{}') RETURNING id"
    )
    declined = event.model_copy(
        update={
            "attendees": [
                AttendeeInfo(
                    email="me@example.com",
                    self=True,
                    response_status=AttendeeResponseStatus.declined,
                )
            ]
        }
    )

    await _sync(pool, partner, declined)  # the partner's copy of the same event
    await _sync(pool, partner, _moved(event, timedelta(hours=4)))

    state = await _state(pool, reminder_id)
    assert state["status"] == "confirmed"
    assert state["starts_at"] == event.start_at - timedelta(minutes=60)
    assert await pool.fetchval("SELECT count(*) FROM reminder_receipts") == 0


async def test_a_broker_outage_loses_no_announcement(db, monkeypatch):
    import importlib

    pool, source, event, reminder_id = await _anchored(db)
    broker = importlib.import_module("butlers.tools.switchboard.insight.broker")
    real = broker.propose_insight_candidate

    async def down(*args, **kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(broker, "propose_insight_candidate", down)
    await _sync(pool, source, _moved(event, timedelta(hours=3)))

    assert await pool.fetchval("SELECT count(*) FROM reminder_receipts") == 1
    assert await pool.fetchval("SELECT count(*) FROM reminder_receipts WHERE announced_at IS NULL")
    assert await _candidates(pool) == []

    monkeypatch.setattr(broker, "propose_insight_candidate", real)
    await _sync(pool, source, _moved(event, timedelta(hours=3)))  # any later follow retries it

    assert len(await _candidates(pool)) == 1
    assert not await pool.fetchval(
        "SELECT count(*) FROM reminder_receipts WHERE announced_at IS NULL"
    )


async def test_a_down_broker_costs_one_call_per_pass_and_a_refused_receipt_stops(db, monkeypatch):
    import importlib

    pool, source, event, reminder_id = await _anchored(db)
    broker = importlib.import_module("butlers.tools.switchboard.insight.broker")
    calls: list[int] = []

    async def down(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("broker down")

    monkeypatch.setattr(broker, "propose_insight_candidate", down)
    # Two moves leave two unannounced receipts; neither projecting them sweeps.
    await _sync(pool, source, _moved(event, timedelta(hours=3)), sweep=False)
    await _sync(pool, source, _moved(event, timedelta(hours=4)), sweep=False)
    assert calls == []

    await announce_pending_reminder_receipts(pool, "relationship")

    assert len(calls) == 1  # one failed call, then the pass stops
    for _ in range(2 * MAX_ANNOUNCE_ATTEMPTS + 2):
        await announce_pending_reminder_receipts(pool, "relationship")
    assert len(calls) <= 2 * MAX_ANNOUNCE_ATTEMPTS  # a capped number of tries, never unbounded
    settled = len(calls)
    await announce_pending_reminder_receipts(pool, "relationship")
    assert len(calls) == settled  # both receipts exhausted their attempts

    async def refused(*args, **kwargs):
        calls.append(1)
        return {"status": "error"}

    monkeypatch.setattr(broker, "propose_insight_candidate", refused)
    await pool.execute("UPDATE reminder_receipts SET attempts = 0, announced_at = NULL")
    await announce_pending_reminder_receipts(pool, "relationship")
    after_refusal = len(calls)
    await announce_pending_reminder_receipts(pool, "relationship")
    assert len(calls) == after_refusal  # a refused receipt is not retried


async def test_core_383_converges_a_database_that_ran_the_first_draft(db):
    import importlib.util

    pool, _source, _event, reminder_id = await _anchored(db)
    path = _CORE_383
    spec = importlib.util.spec_from_file_location("core_383_under_test", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # The first draft: no announcement columns and a UNIQUE on the transition's coordinates.
    await pool.execute("DROP TABLE reminder_receipts")
    await pool.execute(
        """
        CREATE TABLE reminder_receipts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            reminder_event_id UUID NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('rebased', 'retired')),
            revision TEXT NOT NULL,
            old_starts_at TIMESTAMPTZ NOT NULL,
            new_starts_at TIMESTAMPTZ,
            reason TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (reminder_event_id, kind, revision)
        )
        """
    )

    for statement in (migration._TABLE_DDL, *migration._CONVERGE_DDL, migration._INDEX_DDL):
        await pool.execute(statement)
        await pool.execute(statement)  # and it is idempotent

    columns = {
        r["column_name"]
        for r in await pool.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'reminder_receipts'"
        )
    }
    assert {"announced_at", "attempts"} <= columns
    for _ in range(2):  # the old UNIQUE would refuse the second identical transition
        await pool.execute(
            "INSERT INTO reminder_receipts (reminder_event_id, kind, revision, old_starts_at, "
            "reason) VALUES ($1::uuid, 'rebased', 'a>b', now(), 'anchor_moved')",
            reminder_id,
        )


def test_the_moved_line_is_in_the_owners_zone_not_utc():
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from butlers.modules.calendar import reminder_follow

    change = {
        "kind": "rebased",
        "title": "Leave for the dentist",
        "new_start": datetime(2026, 12, 1, 3, 30, tzinfo=UTC),
    }
    assert "Tue 01 Dec 12:30 JST" in reminder_follow._line(change, ZoneInfo("Asia/Tokyo"))

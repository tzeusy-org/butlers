"""Anchored reminders follow their event on real Postgres (bu-5ii4gj.10 slice R-S2)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest

from butlers.modules.calendar.models import AttendeeInfo, AttendeeResponseStatus, EventStatus
from tests.modules.test_calendar_participants import _module, _project
from tests.modules.test_calendar_reminder_anchor import _create, _world

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


async def _anchored(db):
    pool, _module_, mcp, event = await _world(db)
    source = await pool.fetchval("SELECT id FROM calendar_sources WHERE lane = 'user'")
    created = await _create(
        mcp,
        anchor_event_id="dentist",
        anchor_original_start=event.start_at,
        offset_minutes=-60,
    )
    return pool, source, event, created["event_id"]


def _moved(event, by: timedelta):
    return event.model_copy(update={"start_at": event.start_at + by, "end_at": event.end_at + by})


async def _sync(pool, source, event):
    await _project(_module(pool), source, event)


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


async def test_a_receipt_is_unique_per_reminder_kind_and_revision(db):
    from butlers.modules.calendar import reminder_follow

    pool, _source_id, event, reminder_id = await _anchored(db)
    reminder = await pool.fetchrow(
        "SELECT id, title, starts_at, ends_at, metadata -> 'anchor' AS anchor "
        "FROM calendar_events WHERE id = $1::uuid",
        reminder_id,
    )
    anchor = reminder_follow._anchor(reminder["anchor"])
    new_start = event.start_at + timedelta(hours=3)
    async with pool.acquire() as conn:
        first = await reminder_follow._apply(
            conn, reminder, anchor, event_start=new_start, retire=False
        )
        again = await reminder_follow._apply(
            conn, reminder, anchor, event_start=new_start, retire=False
        )

    assert first is not None and again is None
    assert await pool.fetchval("SELECT count(*) FROM reminder_receipts") == 1


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

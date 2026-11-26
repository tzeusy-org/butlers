"""The reminder tick rechecks an anchored reminder's event at fire (bu-5ii4gj.10 slice R-S3)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tests.modules.test_calendar_reminder_anchor import _create, _world

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


async def _due_reminder(db):
    pool, module, mcp, event = await _world(db)
    result = await _create(
        mcp,
        anchor_event_id="dentist",
        anchor_original_start=event.start_at,
        offset_minutes=-60,
    )
    # Make it due now without touching the anchor it was created from.
    await pool.execute(
        "UPDATE calendar_events SET starts_at = now() - interval '1 minute', "
        "ends_at = now() + interval '14 minutes' WHERE id = $1::uuid",
        result["event_id"],
    )
    return pool, module, event, result["event_id"]


def _message(notify: AsyncMock) -> str:
    return notify.await_args.args[0]["delivery"]["message"]


async def test_a_current_anchor_sends_the_plain_reminder(db):
    _pool, module, _event, _id = await _due_reminder(db)
    notify = AsyncMock()

    assert await module.tick("relationship", notify_fn=notify) == 1

    assert _message(notify) == "Reminder: Leave for the dentist"


async def test_a_moved_event_holds_the_reminder_unsent_and_unmarked(db):
    pool, module, event, reminder_id = await _due_reminder(db)
    await pool.execute(
        "UPDATE calendar_events SET starts_at = starts_at + interval '2 hours', "
        "ends_at = ends_at + interval '2 hours' WHERE origin_ref = 'dentist'"
    )
    notify = AsyncMock()

    assert await module.tick("relationship", notify_fn=notify) == 0

    notify.assert_not_awaited()
    assert not await pool.fetchval(
        "SELECT metadata ? 'last_notified_at' FROM calendar_events WHERE id = $1::uuid",
        reminder_id,
    )
    # Moved back: the anchor is current again and the reminder is still owed.
    await pool.execute(
        "UPDATE calendar_events SET starts_at = $1, ends_at = $2 WHERE origin_ref = 'dentist'",
        event.start_at,
        event.end_at,
    )
    assert await module.tick("relationship", notify_fn=notify) == 1


async def test_a_cancelled_event_holds_the_reminder(db):
    pool, module, _event, _id = await _due_reminder(db)
    await pool.execute(
        "UPDATE calendar_events SET status = 'cancelled' WHERE origin_ref = 'dentist'"
    )
    notify = AsyncMock()

    assert await module.tick("relationship", notify_fn=notify) == 0

    notify.assert_not_awaited()


async def test_a_missing_anchor_row_still_fires_stamped_in_the_owner_zone(db, monkeypatch):
    from zoneinfo import ZoneInfo

    async def tokyo(_pool):
        return ZoneInfo("Asia/Tokyo")

    monkeypatch.setattr("butlers.jobs.briefing.owner_briefing_zone", tokyo)
    pool, module, _event, reminder_id = await _due_reminder(db)
    await pool.execute("DELETE FROM calendar_events WHERE origin_ref = 'dentist'")
    await pool.execute(
        "UPDATE calendar_events SET updated_at = '2026-12-01T03:30:00+00' WHERE id = $1::uuid",
        reminder_id,
    )
    notify = AsyncMock()

    assert await module.tick("relationship", notify_fn=notify) == 1

    assert _message(notify) == (
        "Reminder: Leave for the dentist (as of 12:30 JST; event not rechecked)"
    )


async def _recurring_instance_reminder(db):
    """A reminder anchored to a moved occurrence, built from the real Google mapping."""
    from tests.modules.test_calendar_participants import _module, _project
    from tests.modules.test_calendar_reminder_anchor import _google

    pool, module, mcp, event = await _world(db)
    original = (event.start_at + timedelta(days=7)).replace(microsecond=0)
    moved = original + timedelta(hours=3)
    source = await pool.fetchval("SELECT id FROM calendar_sources LIMIT 1")
    exception_id = "standup_" + original.strftime("%Y%m%dT%H%M%SZ")
    await _project(
        _module(pool), source, _google("standup", event.start_at, recurrence=["RRULE:FREQ=WEEKLY"])
    )
    await _project(_module(pool), source, _google(exception_id, moved))
    result = await _create(
        mcp, anchor_event_id="standup", anchor_original_start=original, offset_minutes=-30
    )
    await pool.execute(
        "UPDATE calendar_events SET starts_at = now() - interval '1 minute', "
        "ends_at = now() + interval '14 minutes' WHERE id = $1::uuid",
        result["event_id"],
    )
    return pool, module, moved, exception_id


async def test_a_recurring_instance_anchor_fires_while_the_instance_is_where_it_was(db):
    pool, module, _moved, _ref = await _recurring_instance_reminder(db)
    notify = AsyncMock()

    assert await module.tick("relationship", notify_fn=notify) == 1

    assert _message(notify) == "Reminder: Leave for the dentist"


async def test_a_recurring_instance_that_moves_again_holds_the_reminder(db):
    pool, module, moved, ref = await _recurring_instance_reminder(db)
    await pool.execute(
        "UPDATE calendar_events SET starts_at = $2::timestamptz, "
        "ends_at = $2::timestamptz + interval '1 hour' WHERE origin_ref = $1",
        ref,
        moved + timedelta(hours=2),
    )
    notify = AsyncMock()

    assert await module.tick("relationship", notify_fn=notify) == 0

    notify.assert_not_awaited()


async def test_the_owner_role_copy_decides_not_a_partners_copy(db):
    pool, module, _event, reminder_id = await _due_reminder(db)
    # A partner calendar carries its own copy of the same event, at a different time.
    partner = await pool.fetchval(
        "INSERT INTO calendar_sources (id, source_key, source_kind, lane, provider, calendar_id, "
        "display_name, writable, time_role, metadata) VALUES ('00000000-0000-0000-0000-000000000001', "
        "'provider:google:partner', "
        "'provider_event', 'user', 'google', 'partner', 'Partner', false, 'household_member', '{}') "
        "RETURNING id"
    )
    await pool.execute(
        "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, ends_at, "
        "status, metadata, source_butler) SELECT $1, origin_ref, title, timezone, "
        "starts_at + interval '5 hours',"
        " ends_at + interval '5 hours', status, metadata, source_butler FROM calendar_events "
        "WHERE origin_ref = 'dentist'",
        partner,
    )
    await pool.execute("UPDATE calendar_sources SET time_role = 'owner' WHERE id <> $1", partner)
    # Rewrite the owner's row so it sits after the partner's in the heap: an unordered read
    # would hit the partner's copy first.
    await pool.execute(
        "UPDATE calendar_events SET title = title WHERE source_id <> $1 AND origin_ref = 'dentist'",
        partner,
    )
    notify = AsyncMock()

    # The owner's own copy has not moved: the reminder fires, whichever row the DB returns first.
    assert await module.tick("relationship", notify_fn=notify) == 1


async def test_an_absolute_reminder_is_never_rechecked(db):
    pool, module, mcp, event = await _world(db)
    await _create(mcp, due_at=datetime.now(UTC) - timedelta(hours=1))
    await pool.execute(
        "UPDATE calendar_events SET status = 'cancelled' WHERE origin_ref = 'dentist'"
    )
    notify = AsyncMock()

    assert await module.tick("relationship", notify_fn=notify) == 1

    assert _message(notify) == "Reminder: Leave for the dentist"

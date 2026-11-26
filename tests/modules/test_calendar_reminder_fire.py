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


async def test_a_missing_anchor_row_still_fires_stamped_as_of(db):
    pool, module, _event, _id = await _due_reminder(db)
    await pool.execute("DELETE FROM calendar_events WHERE origin_ref = 'dentist'")
    notify = AsyncMock()

    assert await module.tick("relationship", notify_fn=notify) == 1

    message = _message(notify)
    assert message.startswith("Reminder: Leave for the dentist (as of ")
    assert "UTC; event not rechecked)" in message


async def test_an_absolute_reminder_is_never_rechecked(db):
    pool, module, mcp, event = await _world(db)
    await _create(mcp, due_at=datetime.now(UTC) - timedelta(hours=1))
    await pool.execute(
        "UPDATE calendar_events SET status = 'cancelled' WHERE origin_ref = 'dentist'"
    )
    notify = AsyncMock()

    assert await module.tick("relationship", notify_fn=notify) == 1

    assert _message(notify) == "Reminder: Leave for the dentist"

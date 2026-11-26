"""Anchored reminders through the FastMCP tool on real Postgres (bu-5ii4gj.10 slice R-S1)."""

from __future__ import annotations

import shutil
from datetime import timedelta

import pytest
from fastmcp import FastMCP

from butlers.testing.mcp import tool_fn
from tests.modules.test_calendar_participants import _event, _module, _person, _project, _source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


async def _world(db):
    pool = await db("core", "memory", "relationship", "switchboard", schema="relationship")
    for table in ("calendar_events", "calendar_sources", "public.entities"):
        await pool.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608
    await _person(pool, "Me", "me@example.com", roles=["owner"])
    event = _event("dentist")
    await _project(_module(pool), await _source(pool), event)
    module = _module(pool)
    mcp = FastMCP("anchor-test")
    await module.register_tools(
        mcp=mcp,
        config={"provider": "google", "timezone": "UTC"},
        db=None,
        butler_name="relationship",
    )
    module._db = _module(pool)._db
    module._butler_name = "relationship"
    return pool, module, mcp, event


async def _create(mcp, **kwargs):
    return await (await tool_fn(mcp, "reminder_create"))(title="Leave for the dentist", **kwargs)


async def test_an_anchored_reminder_is_derived_from_its_event(db):
    pool, _module_, mcp, event = await _world(db)

    result = await _create(
        mcp,
        anchor_event_id="dentist",
        anchor_original_start=event.start_at,
        offset_minutes=-60,
    )

    assert result["status"] == "created"
    assert result["starts_at"] == (event.start_at - timedelta(minutes=60)).isoformat()
    stored = await pool.fetchrow(
        "SELECT starts_at, metadata FROM calendar_events WHERE id = $1::uuid", result["event_id"]
    )
    assert stored["starts_at"] == event.start_at - timedelta(minutes=60)
    assert stored["metadata"]["anchor"] == {
        "event_origin_ref": "dentist",
        "original_start": event.start_at.isoformat(),
        "offset_minutes": -60,
    }
    assert stored["metadata"]["native"] is True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"anchor_event_id": "nope", "offset_minutes": -5}, "does not name a live event"),
        ({"anchor_event_id": "dentist", "offset_minutes": 20161}, "within 14 days"),
        ({"anchor_event_id": "dentist"}, "offset_minutes is required"),
        ({"offset_minutes": -5}, "anchor_event_id is required"),
    ],
)
async def test_a_bad_anchor_is_refused_and_writes_nothing(db, kwargs, message):
    pool, _module_, mcp, event = await _world(db)
    kwargs = {"anchor_original_start": event.start_at, **kwargs}

    with pytest.raises(ValueError, match=message):
        await _create(mcp, **kwargs)

    assert (
        await pool.fetchval("SELECT count(*) FROM calendar_events WHERE metadata ? 'anchor'") == 0
    )


async def test_an_anchor_at_the_wrong_start_or_on_a_cancelled_event_is_refused(db):
    pool, _module_, mcp, event = await _world(db)

    with pytest.raises(ValueError, match="does not name a live event"):
        await _create(
            mcp,
            anchor_event_id="dentist",
            anchor_original_start=event.start_at + timedelta(hours=1),
            offset_minutes=-60,
        )
    await pool.execute(
        "UPDATE calendar_events SET status = 'cancelled' WHERE origin_ref = 'dentist'"
    )
    with pytest.raises(ValueError, match="does not name a live event"):
        await _create(
            mcp,
            anchor_event_id="dentist",
            anchor_original_start=event.start_at,
            offset_minutes=-60,
        )


async def test_an_absolute_reminder_is_unchanged_and_both_forms_together_are_refused(db):
    pool, _module_, mcp, event = await _world(db)

    plain = await _create(mcp, due_at=event.start_at)
    assert plain["status"] == "created" and plain["anchor"] is None
    assert (
        await pool.fetchval(
            "SELECT metadata ? 'anchor' FROM calendar_events WHERE id = $1::uuid", plain["event_id"]
        )
        is False
    )
    with pytest.raises(ValueError, match="do not pass both"):
        await _create(
            mcp,
            due_at=event.start_at,
            anchor_event_id="dentist",
            anchor_original_start=event.start_at,
            offset_minutes=-5,
        )
    with pytest.raises(ValueError, match="due_at is required"):
        await _create(mcp)


async def test_a_moved_recurring_instance_is_anchored_by_its_original_start(db):
    pool, _module_, mcp, event = await _world(db)
    original = event.start_at + timedelta(days=7)
    moved = original + timedelta(hours=3)
    await pool.execute(
        "UPDATE calendar_events SET recurrence_rule = 'RRULE:FREQ=WEEKLY' "
        "WHERE origin_ref = 'dentist'"
    )
    await pool.execute(
        "INSERT INTO calendar_event_instances (event_id, source_id, origin_instance_ref, "
        "timezone, starts_at, ends_at, status, is_exception) "
        "SELECT id, source_id, 'dentist_' || to_char($1::timestamptz AT TIME ZONE 'UTC', "
        "'YYYYMMDD\"T\"HH24MISS\"Z\"'), 'UTC', $2::timestamptz, $2::timestamptz + interval '1 hour', 'confirmed', true "
        "FROM calendar_events WHERE origin_ref = 'dentist'",
        original,
        moved,
    )

    created = await _create(
        mcp,
        anchor_event_id="dentist",
        anchor_original_start=original,
        offset_minutes=-30,
    )

    due = await pool.fetchval(
        "SELECT starts_at FROM calendar_events WHERE id = $1::uuid", created["event_id"]
    )
    assert due == moved - timedelta(minutes=30)

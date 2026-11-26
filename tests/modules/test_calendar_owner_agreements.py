"""The owner's agreement snapshot on real Postgres (bu-5ii4gj.10 slice A-S1). Synthetic data only."""

from __future__ import annotations

import shutil
from datetime import timedelta

import pytest

from butlers.modules.calendar.agreements import owner_agreement
from butlers.modules.calendar.models import AttendeeInfo, AttendeeResponseStatus
from tests.modules.test_calendar_participants import _event, _module, _person, _project, _source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_ME = "me@example.com"


def _attendees(response=AttendeeResponseStatus.accepted, *, organizer_self=False):
    return [
        AttendeeInfo(email=_ME, self=True, response_status=response, organizer=organizer_self),
        AttendeeInfo(email="alice@known.test", response_status=AttendeeResponseStatus.accepted),
    ]


async def _world(db):
    pool = await db("core", "memory", "relationship", "switchboard", schema="relationship")
    for table in ("calendar_events", "calendar_sources", "public.entities"):
        await pool.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608
    await _person(pool, "Me", _ME, roles=["owner"])
    await _person(pool, "Alice", "alice@known.test")
    return pool, await _source(pool)


async def _agreements(pool):
    return await pool.fetch(
        "SELECT agreed_starts_at, agreed_ends_at, location_digest, attendees_digest, "
        "response_status FROM calendar_owner_agreements WHERE ended_at IS NULL"
    )


async def test_accepting_records_the_terms_once_and_a_resync_writes_nothing(db):
    pool, source = await _world(db)
    event = _event(attendees=_attendees())
    await _project(_module(pool), source, event)
    await _project(_module(pool), source, event)

    rows = await _agreements(pool)

    assert len(rows) == 1
    assert rows[0]["agreed_starts_at"] == event.start_at
    assert rows[0]["response_status"] == "accepted"
    # Digests only: no address or location is stored.
    assert "alice" not in repr(dict(rows[0]))


async def test_a_moved_event_keeps_the_terms_the_owner_agreed_to(db):
    pool, source = await _world(db)
    event = _event(attendees=_attendees())
    await _project(_module(pool), source, event)
    moved = event.model_copy(
        update={
            "start_at": event.start_at + timedelta(hours=1),
            "end_at": event.end_at + timedelta(hours=1),
        }
    )
    await _project(_module(pool), source, moved)

    rows = await _agreements(pool)

    assert len(rows) == 1 and rows[0]["agreed_starts_at"] == event.start_at


async def test_declining_ends_the_agreement(db):
    pool, source = await _world(db)
    event = _event(attendees=_attendees())
    await _project(_module(pool), source, event)
    declined = event.model_copy(update={"attendees": _attendees(AttendeeResponseStatus.declined)})
    await _project(_module(pool), source, declined)

    assert await _agreements(pool) == []


async def test_an_event_the_owner_organised_or_has_not_answered_has_no_agreement(db):
    pool, source = await _world(db)
    await _project(_module(pool), source, _event("a", attendees=_attendees(organizer_self=True)))
    await _project(
        _module(pool),
        source,
        _event("b", attendees=_attendees(AttendeeResponseStatus.needs_action)),
    )

    assert await _agreements(pool) == []


def test_the_pure_rule_reads_the_owner_seat():
    base = {"source_type": "provider_event"}
    seat = {"email": _ME, "self": True, "responseStatus": "tentative"}
    other = {"email": "alice@known.test", "responseStatus": "accepted"}
    assert owner_agreement({**base, "attendees": [seat, other]}) == (
        "tentative",
        ["alice@known.test"],
    )
    assert owner_agreement({**base, "attendees": [other]}) is None
    assert owner_agreement({**base, "attendees": [{**seat, "responseStatus": "declined"}]}) is None


async def test_the_current_terms_follow_a_move_while_the_agreed_terms_stay(db):
    pool, source = await _world(db)
    event = _event(attendees=_attendees())
    await _project(_module(pool), source, event)
    moved = event.model_copy(
        update={
            "start_at": event.start_at + timedelta(hours=2),
            "end_at": event.end_at + timedelta(hours=2),
            "location": "Room 9",
        }
    )
    await _project(_module(pool), source, moved)

    row = await pool.fetchrow("SELECT * FROM calendar_owner_agreements")

    assert row["agreed_starts_at"] == event.start_at
    assert row["current_starts_at"] == moved.start_at and row["current_ends_at"] == moved.end_at
    assert row["current_location_digest"] is not None
    assert row["location_digest"] != row["current_location_digest"]


async def test_declining_leaves_a_tombstone_and_accepting_again_starts_a_fresh_agreement(db):
    pool, source = await _world(db)
    event = _event(attendees=_attendees())
    await _project(_module(pool), source, event)
    declined = event.model_copy(update={"attendees": _attendees(AttendeeResponseStatus.declined)})
    await _project(_module(pool), source, declined)

    assert await pool.fetchval("SELECT count(*) FROM calendar_owner_agreements") == 1
    assert await pool.fetchval("SELECT ended_at IS NOT NULL FROM calendar_owner_agreements")

    moved = event.model_copy(
        update={
            "start_at": event.start_at + timedelta(hours=3),
            "end_at": event.end_at + timedelta(hours=3),
        }
    )
    await _project(_module(pool), source, moved)
    rows = await _agreements(pool)

    assert len(rows) == 1 and rows[0]["agreed_starts_at"] == moved.start_at


async def test_an_unchanged_resync_leaves_the_last_change_time_alone_and_a_move_advances_it(db):
    pool, source = await _world(db)
    event = _event(attendees=_attendees())
    await _project(_module(pool), source, event)
    first = await pool.fetchval("SELECT current_observed_at FROM calendar_owner_agreements")

    await _project(_module(pool), source, event)
    assert await pool.fetchval("SELECT current_observed_at FROM calendar_owner_agreements") == first

    moved = event.model_copy(
        update={
            "start_at": event.start_at + timedelta(hours=1),
            "end_at": event.end_at + timedelta(hours=1),
        }
    )
    await _project(_module(pool), source, moved)
    assert await pool.fetchval("SELECT current_observed_at FROM calendar_owner_agreements") > first

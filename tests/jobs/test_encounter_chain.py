"""One chained scenario: participants projection, then the encounter reducer, then prep.

bu-5ii4gj.3.1. Nothing here is hand-inserted into the ledger: three occurrences of one person are
projected the way the provider sync does (attested by the owner's debrief, nobody vouches, declined
by them), the reducer writes ``relationship.encounters`` from them, and ``calendar_prep`` then reads
"last met" and "last scheduled" from those rows. A linked native reminder is never an encounter.
Synthetic people only.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest

from butlers.jobs.encounter_ledger import run_encounter_reducer
from butlers.modules.calendar.models import AttendeeInfo, AttendeeResponseStatus
from tests.jobs.test_prep_participants import _prep, _seed
from tests.modules.test_calendar_participants import _OWN, _event, _module, _project
from tests.modules.test_calendar_reminder_integration import _make_module

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

#: The prep job's frozen "now" (see ``_prep``); the meeting being prepped starts at 10:00 that day.
_PREP_NOW = datetime(2026, 12, 1, 4, 0, tzinfo=UTC)


def _past(ref: str, days_ago: int, alice: AttendeeResponseStatus):
    start = datetime(2026, 12, 1, 9, tzinfo=UTC) - timedelta(days=days_ago)
    attendees = [
        AttendeeInfo(email=_OWN, self=True, response_status=AttendeeResponseStatus.accepted),
        AttendeeInfo(email="alice@known.test", response_status=alice),
    ]
    return _event(ref, attendees=attendees).model_copy(
        update={"start_at": start, "end_at": start + timedelta(hours=1)}
    )


async def _starts(pool, ref: str) -> datetime:
    return await pool.fetchval("SELECT starts_at FROM calendar_events WHERE origin_ref = $1", ref)


async def _native_reminder(pool, alice):
    """A reminder made by the real reminder insert path, linked to Alice.

    Real reminders are ``{"native": true}`` with no ``source_type``, on the internal reminders
    source. It also gets the participant record Alice has on her real meeting, so the only thing
    keeping it out of the ledger is what kind of event it is.
    """
    module = _make_module(pool)
    module._projection_tables_available_cache = True
    reminder, _row = await module._insert_reminder_to_calendar_events(
        title="Call Alice back",
        body=None,
        starts_at=datetime(2026, 11, 30, 9, tzinfo=UTC),
        ends_at=datetime(2026, 11, 30, 9, 15, tzinfo=UTC),
        timezone="UTC",
        recurrence_rule=None,
        entity_ids=[alice],
    )
    await pool.execute(
        "INSERT INTO calendar_event_participants (event_id, email_norm, role, response_status, "
        "entity_id, resolution, resolver_version) "
        "SELECT $1, email_norm, role, response_status, entity_id, resolution, resolver_version "
        "FROM calendar_event_participants WHERE entity_id = $2 LIMIT 1",
        reminder,
        alice,
    )
    return reminder


async def test_prep_reads_the_reducers_own_rows_for_three_occurrences_and_a_reminder(db):
    pool, alice, upcoming = await _seed(db)
    source = await pool.fetchval("SELECT id FROM calendar_sources")
    # The owner's own calendar, so the role gate admits everything on it: only the event's
    # own kind can keep the reminder out of the ledger.
    await pool.execute("UPDATE calendar_sources SET time_role = 'owner'")
    accepted, declined = AttendeeResponseStatus.accepted, AttendeeResponseStatus.declined
    await _project(
        _module(pool),
        source,
        _past("attested", 12, accepted),
        _past("unvouched", 8, accepted),
        _past("declined", 3, declined),
    )
    attested_start = await _starts(pool, "attested")
    attested = await pool.fetchval("SELECT id FROM calendar_events WHERE origin_ref = 'attested'")
    await pool.execute(
        "INSERT INTO meeting_debriefs (event_id, occurrence_start, occurrence_end, state) "
        "VALUES ($1, $2::timestamptz, $2::timestamptz + interval '1 hour', 'none_agreed')",
        attested,
        attested_start,
    )
    reminder = await _native_reminder(pool, alice)

    result = await run_encounter_reducer(pool, now=_PREP_NOW)

    assert result["complete"] is True
    rows = {
        r["origin_ref"]: (r["state"], r["basis"], r["occurrence_start"])
        for r in await pool.fetch(
            "SELECT ce.origin_ref, e.state, e.basis, e.occurrence_start "
            "FROM relationship.encounters e JOIN calendar_events ce ON ce.id = e.event_id "
            "WHERE e.entity_id = $1",
            alice,
        )
    }
    assert rows["attested"][:2] == ("happened_attested", "owner_debrief")
    assert rows["unvouched"][:2] == ("unknown", "schedule")
    assert rows["declined"][:2] == ("declined_by_them", "rsvp")
    assert not await pool.fetchval(
        "SELECT count(*) FROM relationship.encounters WHERE event_id = $1", reminder
    ), "a native reminder is not an encounter"

    attendee = (await _prep(pool, upcoming))["attendees"][0]

    # Prep's answers are the reducer's rows, read back: the attested occurrence is "last met",
    # the unvouched one "last scheduled" and the decline is neither.
    assert attendee["last_met"] == rows["attested"][2].isoformat()
    assert attendee["last_met_basis"] == "you_confirmed"
    assert attendee["last_scheduled"] == rows["unvouched"][2].isoformat()


async def test_the_reducer_writes_no_row_for_a_linked_native_reminder(db):
    pool, alice, _upcoming = await _seed(db)
    source = await pool.fetchval("SELECT id FROM calendar_sources")
    await pool.execute("UPDATE calendar_sources SET time_role = 'owner'")
    await _project(_module(pool), source, _past("met", 5, AttendeeResponseStatus.accepted))
    reminder = await _native_reminder(pool, alice)

    result = await run_encounter_reducer(pool, now=_PREP_NOW)

    assert result["complete"] is True
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM relationship.encounters WHERE event_id = $1", reminder
        )
        == 0
    )


async def _second_source(pool, time_role: str):
    return await pool.fetchval(
        "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id, "
        "display_name, writable, metadata, time_role) VALUES ('provider:google:shared', "
        "'provider_event', 'user', 'google', 'shared', 'Shared', false, '{}', $1) RETURNING id",
        time_role,
    )


@pytest.mark.parametrize(
    ("debrief_state", "kept"),
    [
        ("did_not_happen", "shared"),
        ("moved", "shared"),
        ("none_agreed", "shared"),
        ("captured", "shared"),
        ("pending", "primary"),
        ("expired", "primary"),
    ],
)
async def test_any_answered_debrief_keeps_its_copy_but_pending_or_expired_do_not(
    db, debrief_state, kept
):
    """One meeting on two calendars is one encounter; the owner's word decides which copy."""
    pool, _alice, _upcoming = await _seed(db)
    primary = await pool.fetchval("SELECT id FROM calendar_sources")
    await pool.execute("UPDATE calendar_sources SET time_role = 'owner' WHERE id = $1", primary)
    shared = await _second_source(pool, "other_person")
    meeting = _past("same-invite", 4, AttendeeResponseStatus.accepted)
    await _project(_module(pool), primary, meeting)
    await _project(_module(pool), shared, meeting)
    copies = {
        r["key"]: r["id"]
        for r in await pool.fetch(
            "SELECT ce.id, CASE WHEN ce.source_id = $1 THEN 'primary' ELSE 'shared' END AS key "
            "FROM calendar_events ce WHERE ce.origin_ref = 'same-invite'",
            primary,
        )
    }
    assert set(copies) == {"primary", "shared"}
    await pool.execute(
        "INSERT INTO meeting_debriefs (event_id, occurrence_start, occurrence_end, state) "
        "VALUES ($1, $2::timestamptz, $2::timestamptz + interval '1 hour', $3)",
        copies["shared"],
        await _starts(pool, "same-invite"),
        debrief_state,
    )

    await run_encounter_reducer(pool, now=_PREP_NOW)

    rows = await pool.fetch(
        "SELECT e.event_id FROM relationship.encounters e "
        "JOIN calendar_events ce ON ce.id = e.event_id WHERE ce.origin_ref = 'same-invite'"
    )
    assert {r["event_id"] for r in rows} == {copies[kept]}

"""E2b of bu-5ii4gj.3: prep and the meeting card read "last met" from the encounter ledger.

Real Postgres: ledger rows are written the way the reducer writes them, then the prep job and the
meeting card run over them. Synthetic people only.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from butlers.jobs import calendar_prep as prep_module
from butlers.jobs import meeting_card as card
from butlers.jobs.calendar_prep import _last_met_fields
from butlers.jobs.person_time import PersonTime, read_person_time
from tests.jobs.test_prep_participants import _card, _prep, _seed
from tests.modules.test_calendar_participants import _event, _module, _project

_DOCKER = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")


def _ago(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def _row(pool, entity_id, state, basis, when, *, event=None) -> None:
    await pool.execute(
        "INSERT INTO relationship.encounters "
        "(entity_id, event_id, occurrence_start, series_key, state, basis) "
        "VALUES ($1, $2, $3, 'series', $4, $5)",
        entity_id,
        event or uuid.uuid4(),
        when,
        state,
        basis,
    )


async def _link(pool, ref: str, *entity_ids) -> None:
    """The owner-visible link the interim rule reads (the projection does not write it)."""
    for entity_id in entity_ids:
        await pool.execute(
            "INSERT INTO calendar_event_entities (event_id, entity_id) "
            "SELECT id, $2 FROM calendar_events WHERE origin_ref = $1",
            ref,
            entity_id,
        )


async def _past_meeting(pool, alice, days_ago: int) -> datetime:
    """A past co-attended meeting Alice accepted, projected the way the provider sync does."""
    source = await pool.fetchval("SELECT id FROM calendar_sources")
    start = (datetime.now(UTC) - timedelta(days=days_ago)).replace(microsecond=0)
    event = _event(f"past-{days_ago}").model_copy(
        update={"start_at": start, "end_at": start + timedelta(hours=1), "title": "Old sync"}
    )
    await _project(_module(pool), source, event)
    await _link(pool, f"past-{days_ago}", alice)
    return start


@pytest.mark.parametrize(
    ("state", "basis", "label"),
    [
        ("happened_attested", "owner_debrief", "you_confirmed"),
        ("happened_corroborated", "chronicler", "evidence"),
    ],
)
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_DOCKER
async def test_prep_last_met_comes_from_a_vouched_for_encounter(db, state, basis, label):
    pool, alice, event_id = await _seed(db)
    when = _ago(10)
    await _row(pool, alice, state, basis, when)

    attendee = (await _prep(pool, event_id))["attendees"][0]

    assert attendee["last_met"] == when.isoformat()
    assert attendee["last_met_basis"] == label
    assert attendee["last_scheduled"] is None


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_DOCKER
async def test_a_meeting_nobody_vouches_for_is_last_scheduled_never_last_met(db, monkeypatch):
    pool, alice, event_id = await _seed(db)
    when = _ago(10)
    await _row(pool, alice, "unknown", "schedule", when)
    await _row(pool, alice, "declined_by_them", "rsvp", _ago(2))

    attendee = (await _prep(pool, event_id))["attendees"][0]

    assert attendee["last_met"] is None and attendee["last_met_basis"] is None
    assert attendee["last_scheduled"] == when.isoformat()  # a decline is not even scheduled


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_DOCKER
async def test_a_person_the_ledger_does_not_cover_keeps_the_interim_rule(db):
    pool, alice, event_id = await _seed(db)
    past = await _past_meeting(pool, alice, 150)
    # A stray row for somebody else: the ledger exists, but not for Alice.
    await _row(pool, uuid.uuid4(), "happened_attested", "owner_debrief", _ago(3))

    attendee = (await _prep(pool, event_id))["attendees"][0]

    assert attendee["last_met"] == past.isoformat()
    assert attendee["last_met_basis"] == "interim"
    assert attendee["last_scheduled"] is None


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_DOCKER
async def test_an_unreadable_ledger_is_not_an_empty_one(db, monkeypatch, caplog):
    pool, alice, event_id = await _seed(db)
    past = await _past_meeting(pool, alice, 20)
    await _row(pool, alice, "happened_attested", "owner_debrief", _ago(10))

    async def broken(*_args: Any) -> Any:
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(prep_module, "read_person_time", broken)
    attendee = (await _prep(pool, event_id))["attendees"][0]

    assert attendee["last_met"] == past.isoformat()  # the interim rule answers
    assert attendee["last_met_basis"] == "interim"
    assert "encounter ledger unreadable" in caplog.text


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_DOCKER
async def test_history_older_than_the_ledger_window_survives_a_scheduled_only_row(db):
    """Met five months ago, on tomorrow's invite: the ledger holds only the scheduled row."""
    pool, alice, event_id = await _seed(db)
    past = await _past_meeting(pool, alice, 150)
    await _row(pool, alice, "scheduled", "schedule", _ago(-1))

    attendee = (await _prep(pool, event_id))["attendees"][0]

    assert attendee["last_met"] == past.isoformat()
    assert attendee["last_met_basis"] == "interim"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_DOCKER
async def test_the_interim_fallback_never_reaches_into_the_ledger_window(db):
    """A recent meeting the ledger says did not happen is not resurrected by the fallback."""
    pool, alice, event_id = await _seed(db)
    await _past_meeting(pool, alice, 10)
    await _row(pool, alice, "did_not_happen", "owner_debrief", _ago(10))

    attendee = (await _prep(pool, event_id))["attendees"][0]

    assert attendee["last_met"] is None and attendee["last_met_basis"] is None


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_DOCKER
async def test_a_vouched_for_meeting_in_the_window_beats_older_history(db):
    pool, alice, event_id = await _seed(db)
    await _past_meeting(pool, alice, 150)
    recent = _ago(10)
    await _row(pool, alice, "happened_attested", "owner_debrief", recent)

    attendee = (await _prep(pool, event_id))["attendees"][0]

    assert attendee["last_met"] == recent.isoformat()
    assert attendee["last_met_basis"] == "you_confirmed"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_DOCKER
async def test_one_covered_and_one_uncovered_attendee_in_one_envelope(db):
    from butlers.core.identity.attendees import RESOLVER_VERSION  # noqa: F401
    from tests.modules.test_calendar_participants import _person

    pool, alice, _event_id = await _seed(db)
    bob = await _person(pool, "Bob", "bob@known.test")
    from butlers.modules.calendar.models import AttendeeInfo, AttendeeResponseStatus

    source = await pool.fetchval("SELECT id FROM calendar_sources")
    attendees = [
        AttendeeInfo(
            email="me@example.com", self=True, response_status=AttendeeResponseStatus.accepted
        ),
        AttendeeInfo(email="alice@known.test", response_status=AttendeeResponseStatus.accepted),
        AttendeeInfo(email="bob@known.test", response_status=AttendeeResponseStatus.accepted),
    ]
    both = _event("both", attendees=attendees)
    await _project(_module(pool), source, both)
    start = (datetime.now(UTC) - timedelta(days=150)).replace(microsecond=0)
    old = both.model_copy(
        update={"event_id": "old-both", "start_at": start, "end_at": start + timedelta(hours=1)}
    )
    await _project(_module(pool), source, old)
    await _link(pool, "old-both", alice, bob)
    recent = _ago(10)
    await _row(pool, alice, "happened_attested", "owner_debrief", recent)  # Bob has no row

    event_id = await pool.fetchval("SELECT id FROM calendar_events WHERE origin_ref = 'both'")
    by_name = {a["name"]: a for a in (await _prep(pool, event_id))["attendees"]}

    assert by_name["Alice"]["last_met"] == recent.isoformat()
    assert by_name["Alice"]["last_met_basis"] == "you_confirmed"
    assert by_name["Bob"]["last_met"] == start.isoformat()
    assert by_name["Bob"]["last_met_basis"] == "interim"


async def test_an_unrecognised_basis_shows_a_neutral_chip_and_logs(caplog):
    entity = uuid.uuid4()
    when = datetime(2026, 11, 1, 9, tzinfo=UTC)

    class _Executor:
        async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
            if "DISTINCT en.entity_id" in sql:
                return [{"entity_id": entity}]
            if "happened_attested" in args[1]:
                return [
                    {
                        "entity_id": entity,
                        "occurrence_start": when,
                        "basis": "telepathy",
                        "title": "T",
                    }
                ]
            return []

    result = await read_person_time(_Executor(), [entity], datetime(2026, 11, 20, tzinfo=UTC))

    assert result[entity].last_met_basis == "recorded"
    assert "unrecognised basis" in caplog.text


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_DOCKER
async def test_a_non_active_person_is_never_reported(db):
    pool, alice, _event_id = await _seed(db)
    await _row(pool, alice, "happened_attested", "owner_debrief", _ago(10))
    assert alice in await read_person_time(pool, [alice], datetime.now(UTC))

    await pool.execute("UPDATE public.entities SET posture = 'memorial' WHERE id = $1", alice)

    assert await read_person_time(pool, [alice], datetime.now(UTC)) == {}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_DOCKER
async def test_the_card_says_last_met_only_for_a_vouched_for_meeting(db, monkeypatch):
    pool, alice, event_id = await _seed(db)
    await _row(pool, alice, "unknown", "schedule", _ago(10))
    await _prep(pool, event_id)  # the card reads the envelope the prep job wrote

    message = await _card(pool, monkeypatch)

    assert "last scheduled" in message and "last met" not in message


def test_last_met_fields_prefer_the_ledger_and_label_the_interim_rule():
    when = datetime(2026, 11, 1, 9, tzinfo=UTC)
    covered = PersonTime(covered=True, last_scheduled=when, last_scheduled_event="Sync")
    # Covered, nothing vouched for in the window: older history fills last_met as interim.
    assert _last_met_fields((when, "Old"), covered) == {
        "last_met": when.isoformat(),
        "last_met_event": "Old",
        "last_met_basis": "interim",
        "last_scheduled": when.isoformat(),
        "last_scheduled_event": "Sync",
    }
    assert _last_met_fields(None, covered)["last_met"] is None
    interim = _last_met_fields((when, "Old"), None)
    assert interim["last_met"] == when.isoformat() and interim["last_met_basis"] == "interim"
    assert _last_met_fields(None, None)["last_met_basis"] is None


def test_the_cards_time_note_follows_the_basis():
    note = card._time_note
    assert note({"last_met": "2026-08-03T05:00:00+00:00"}) == " (last met 3 Aug)"
    assert (
        note({"last_met": "2026-08-03T05:00:00+00:00", "last_met_basis": "interim"})
        == " (last met 3 Aug, unconfirmed)"
    )
    assert note({"last_met": None, "last_scheduled": "2026-08-04T05:00:00+00:00"}) == (
        " (last scheduled 4 Aug)"
    )
    assert note({}) == ""

"""S2 of bu-5ii4gj.3: prep, meeting card and workspace people read participant records.

Real seam on Postgres: a Google payload is projected (owner, a known person, a stranger and a
room), then the prep job, the meeting card and the workspace people read run over what the
projection stored. Synthetic people and addresses only.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from butlers.api.read_models.calendar_workspace import query_calendar_entry_people
from butlers.core.state import state_get
from butlers.jobs import meeting_card as card
from butlers.jobs.calendar_prep import prep_key, run_relationship_calendar_prep_contribution
from butlers.modules.calendar import participants
from butlers.modules.calendar.models import AttendeeInfo
from tests.modules.test_calendar_participants import (
    _event,
    _module,
    _person,
    _project,
    _source,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_START = datetime(2026, 12, 1, 10, tzinfo=UTC)
_NOW = _START - timedelta(minutes=18)


class _Proposer:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, pool: Any, **kwargs: Any) -> dict[str, str]:
        self.messages.append(kwargs["message"])
        return {"status": "accepted"}


async def _withdraw(pool: Any, **kwargs: Any) -> int:
    return 0


class _Db:
    """The dashboard's fan-out, answered by the one test pool."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def fan_out_with_status(self, sql: str, args: tuple, *, butler_names: Any = None):
        return {"relationship": await self.pool.fetch(sql, *args)}, []


async def _seed(db, *, attendees=None):
    pool = await db("core", "memory", "relationship", "switchboard", schema="relationship")
    for table in ("calendar_events", "calendar_sources", "public.entities"):
        await pool.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608
    await _person(pool, "Me", "me@example.com", roles=["owner"])
    alice = await _person(pool, "Alice", "alice@known.test")
    source = await _source(pool)
    await _project(_module(pool), source, _event(attendees=attendees))
    event_id = await pool.fetchval("SELECT id FROM calendar_events")
    return pool, alice, event_id


async def _prep(pool, event_id):
    with patch(
        "butlers.jobs.calendar_prep._now", return_value=datetime(2026, 12, 1, 4, 0, tzinfo=UTC)
    ):
        await run_relationship_calendar_prep_contribution(pool, None)
    return await state_get(pool, prep_key(str(event_id)))


async def _card(pool, monkeypatch) -> str:
    monkeypatch.setattr(card, "MEETING_CARD_LIVE", True)
    proposer = _Proposer()
    await card.run_meeting_card(
        pool, insight_proposer=proposer, insight_withdrawer=_withdraw, now=_NOW
    )
    assert len(proposer.messages) == 1
    return proposer.messages[0]


async def test_a_projected_meeting_reaches_prep_and_the_card_by_name(db, monkeypatch):
    pool, alice, event_id = await _seed(db)
    envelope = await _prep(pool, event_id)
    assert [a["name"] for a in envelope["attendees"]] == ["Alice"]
    assert envelope["attendees"][0]["entity_id"] == str(alice)
    assert envelope["attendees_unavailable"] is False
    assert envelope["unresolved_attendees"] == 1  # the stranger, counted never named
    text = repr(envelope)
    assert "stranger" not in text and "resource.calendar" not in text and "Me" not in text.split()

    message = await _card(pool, monkeypatch)
    assert "With: Alice" in message and "+1 not identified" in message
    assert "No prep context yet." not in message
    assert "stranger" not in message and "resource" not in message.lower()


async def test_an_ambiguous_address_names_no_one_anywhere(db, monkeypatch):
    pool, _alice, event_id = await _seed(db)
    await _person(pool, "Alicia", "alice@known.test")
    await _project(_module(pool), await pool.fetchval("SELECT id FROM calendar_sources"), _event())
    envelope = await _prep(pool, event_id)
    assert envelope["attendees"] == []
    assert envelope["unresolved_attendees"] == 2  # the ambiguous address and the stranger
    message = await _card(pool, monkeypatch)
    assert "Alice" not in message and "Alicia" not in message
    assert "+2 not identified" in message


async def test_rows_that_are_not_resolved_read_as_unavailable_never_empty(db, monkeypatch):
    pool, _alice, event_id = await _seed(db)
    await pool.execute("DELETE FROM calendar_event_participants")
    envelope = await _prep(pool, event_id)
    assert envelope["attendees_unavailable"] is True and envelope["attendees"] == []
    assert "Attendees unavailable." in await _card(pool, monkeypatch)
    people = await query_calendar_entry_people(
        _Db(pool), event_ids=[event_id], butlers=["relationship"]
    )
    assert event_id in people.attendees_unavailable


async def test_the_workspace_names_the_known_person_and_counts_the_rest(db):
    pool, alice, event_id = await _seed(db)
    people = await query_calendar_entry_people(
        _Db(pool), event_ids=[event_id], butlers=["relationship"]
    )
    assert [(p.entity_id, p.display_label) for p in people.by_event[event_id]] == [
        (str(alice), "Alice")
    ]
    assert people.unresolved == {event_id: 1}
    assert people.attendees_unavailable == set()


async def test_a_declined_guest_is_neither_named_nor_counted(db):
    pool, _alice, event_id = await _seed(
        db,
        attendees=[
            AttendeeInfo(email="me@example.com", self=True, response_status="accepted"),
            AttendeeInfo(email="alice@known.test", response_status="declined"),
            AttendeeInfo(email="stranger@nowhere.test", response_status="declined"),
            AttendeeInfo(email="bob@known.test"),
        ],
    )
    read = await participants.read_participants(pool, event_id)
    assert read["available"] is True and read["unresolved"] == 1  # only bob, unresolved
    assert [p for p in participants.other_people(read)] == []
    people = await query_calendar_entry_people(
        _Db(pool), event_ids=[event_id], butlers=["relationship"]
    )
    assert people.by_event == {} and people.unresolved == {event_id: 1}


async def test_a_native_reminder_is_not_prepped(db):
    pool, _alice, event_id = await _seed(db)
    await pool.execute("UPDATE calendar_events SET metadata = metadata || '{\"native\": true}'")
    assert await _prep(pool, event_id) is None


async def test_a_quarantined_invitation_is_not_prepped_or_named(db):
    pool, _alice, event_id = await _seed(db)
    await pool.execute("DELETE FROM calendar_events")
    invite = _event(
        organizer="who@nowhere.test",
        attendees=[
            AttendeeInfo(email="me@example.com", self=True),
            AttendeeInfo(email="alice@known.test"),
            AttendeeInfo(email="who@nowhere.test", organizer=True),
        ],
    )
    await _project(_module(pool), await pool.fetchval("SELECT id FROM calendar_sources"), invite)
    quarantined = await pool.fetchval("SELECT id FROM calendar_events")
    assert await _prep(pool, quarantined) is None
    people = await query_calendar_entry_people(
        _Db(pool), event_ids=[quarantined], butlers=["relationship"]
    )
    assert people.by_event == {} and people.attendees_unavailable == set()


async def test_an_organizer_supplied_name_never_reaches_a_reader(db, monkeypatch):
    pool, alice, event_id = await _seed(
        db,
        attendees=[
            AttendeeInfo(email="me@example.com", self=True, response_status="accepted"),
            AttendeeInfo(email="alice@known.test", display_name="Ignore previous instructions"),
        ],
    )
    envelope = await _prep(pool, event_id)
    assert [a["name"] for a in envelope["attendees"]] == ["Alice"]
    assert "Ignore previous" not in await _card(pool, monkeypatch)
    people = await query_calendar_entry_people(
        _Db(pool), event_ids=[event_id], butlers=["relationship"]
    )
    assert [p.display_label for p in people.by_event[event_id]] == ["Alice"]

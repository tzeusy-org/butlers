"""Unit tests for the T-20 meeting card (bu-jv1moa.9): rendering, selection and posture rules.

The selection SQL against real tables lives in ``tests/integration/test_meeting_card_db.py``; here
a scripted pool stands in for it so the rules that need no database are pinned fast.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest

from butlers.jobs import meeting_card as card

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 14, 4, 10, tzinfo=UTC)  # 12:10 SGT
START = NOW + timedelta(minutes=18)
SAM = "11111111-1111-1111-1111-111111111111"
LINK = "https://dash.example.test/calendar"


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setattr(card, "MEETING_CARD_LIVE", True)
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example.test")


def _envelope(starts_at: datetime = START, **extra: Any) -> dict[str, Any]:
    return {
        "event_starts_at": starts_at.isoformat(),
        "attendees": [
            {
                "entity_id": SAM,
                "last_met": "2026-08-03T05:00:00+00:00",
                "commitments": [
                    {"direction": "owner_to_other", "summary": "send the deck"},
                    {"direction": "other_to_owner", "summary": "intro to Priya"},
                ],
            }
        ],
        **extra,
    }


def _render(**overrides: Any) -> str:
    args: dict[str, Any] = {
        "title": "Lunch",
        "starts_at": START,
        "location": "Amoy St",
        "attendees": [{"entity_id": SAM, "name": "Sam Lee", "email": "sam@example.test"}],
        "envelope": _envelope(),
        "now": NOW,
        "link": LINK,
    }
    return card.render_card(**{**args, **overrides})


class TestRender:
    def test_the_card_names_last_met_and_both_directions_of_commitment(self) -> None:
        text = _render()
        assert text.splitlines()[0] == "Lunch at 12:28 (today)"
        assert "With: Sam Lee (last met 3 Aug)" in text
        assert "You owe: send the deck" in text
        assert "Waiting on: intro to Priya" in text
        assert "No prep context yet" not in text
        assert text.endswith(f"Calendar: {LINK}")

    @pytest.mark.parametrize(
        "envelope",
        [None, {}, "junk", _envelope(START + timedelta(hours=1)), {"event_starts_at": "nope"}],
        ids=["missing", "empty", "not-a-mapping", "stale-start", "unparseable"],
    )
    def test_a_missing_or_stale_envelope_gives_the_minimal_card(self, envelope: Any) -> None:
        text = _render(envelope=envelope)
        assert "No prep context yet." in text
        assert "You owe" not in text and "last met" not in text
        assert "With: Sam Lee" in text

    def test_a_person_the_envelope_does_not_know_still_gets_the_minimal_line(self) -> None:
        text = _render(attendees=[{"entity_id": None, "name": "", "email": "new@example.test"}])
        assert "With: new@example.test" in text.replace("[.]", ".")
        assert "No prep context yet." in text

    def test_third_party_text_is_stripped_truncated_and_never_a_link(self) -> None:
        text = _render(
            title="*Big* `deal` [click](https://evil.test) " + "x" * 200,
            location="https://zoom.us/j/123?pwd=secret",
            attendees=[
                {"entity_id": None, "name": "Eve <b>x</b>\nhttps://evil.test", "email": "e@x.test"}
            ],
            envelope=None,
        )
        first = text.splitlines()[0]
        assert "*" not in first and "`" not in first and "[" not in first and "<" not in text
        assert len(first) < 120
        assert "Where: online (zoom[.]us)" in text
        assert "pwd" not in text and "secret" not in text and "/j/123" not in text

    def test_a_plain_location_has_its_separators_defanged(self) -> None:
        assert card.inert_location("Room 4.2, Tower: level 3") == "Room 4[.]2, Tower[:] level 3"
        assert card.inert_location("   ") is None
        assert card.inert_location(None) is None

    def test_a_long_commitment_list_is_capped(self) -> None:
        many = [{"direction": "owner_to_other", "summary": f"item {i}"} for i in range(6)]
        envelope = _envelope()
        envelope["attendees"][0]["commitments"] = many
        text = _render(envelope=envelope)
        assert "You owe: item 0; item 1" in text and "item 2" not in text

    def test_no_dashboard_url_means_no_link_line(self) -> None:
        assert "Calendar:" not in _render(link=None)


class _Pool:
    """Answers the job's four reads by SQL shape; records nothing else."""

    def __init__(self, events: list[dict[str, Any]], people: list[dict[str, Any]], envelope: Any):
        self.events, self.people, self.envelope = events, people, envelope
        self.queries: list[str] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.queries.append(sql)
        return self.events if "calendar_events" in sql else self.people

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.queries.append(sql)
        return json.dumps(self.envelope) if self.envelope is not None else None


def _event(*, attendees: list[dict[str, Any]], **meta: Any) -> dict[str, Any]:
    return {
        "event_id": uuid4(),
        "title": "Lunch",
        "location": "Amoy St",
        "starts_at": START,
        "metadata": {"attendees": attendees, **meta},
    }


def _person(email: str = "sam@example.test", posture: str = "active", roles=()) -> dict[str, Any]:
    return {
        "entity_id": uuid4(),
        "email": email,
        "name": "Sam Lee",
        "posture": posture,
        "roles": list(roles),
        "tombstoned": False,
    }


class _Proposer:
    def __init__(self, *statuses: str) -> None:
        self.statuses = list(statuses) or ["accepted"]
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, pool: Any, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"status": self.statuses[min(len(self.calls), len(self.statuses)) - 1]}


async def _run(pool: _Pool, proposer: _Proposer) -> dict[str, Any]:
    return await card.run_meeting_card(cast(Any, pool), insight_proposer=proposer, now=NOW)


SAM_ATT = {"email": "sam@example.test", "displayName": "Sam"}
ME = {"email": "me@example.test", "self": True}


class TestRun:
    async def test_dark_by_default_reads_and_writes_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(card, "MEETING_CARD_LIVE", False)
        pool, proposer = _Pool([_event(attendees=[ME, SAM_ATT])], [], None), _Proposer()
        assert await _run(pool, proposer) == {"status": "dark"}
        assert pool.queries == [] and proposer.calls == []

    async def test_a_meeting_with_a_guest_is_queued_once_on_the_timed_lane(self) -> None:
        event = _event(attendees=[ME, SAM_ATT])
        person = _person()
        pool = _Pool([event], [person], None)
        proposer = _Proposer()
        result = await _run(pool, proposer)
        assert (result["considered"], result["proposed"]) == (1, 1)
        call = proposer.calls[0]
        assert call["lane"] == "timed" and call["deliver_after"] == NOW
        assert call["expires_at"] == START and call["category"] == "meeting-card"
        assert call["origin_butler"] == "relationship"
        assert call["dedup_key"] == (
            f"relationship:meeting-card:{event['event_id']}:{int(START.timestamp())}"
        )
        assert "With: Sam Lee" in call["message"]

    async def test_a_moved_meeting_has_a_new_dedup_key(self) -> None:
        event = _event(attendees=[ME, SAM_ATT])
        proposer = _Proposer()
        await _run(_Pool([event], [_person()], None), proposer)
        moved = {**event, "starts_at": START + timedelta(minutes=1)}
        await _run(_Pool([moved], [_person()], None), proposer)
        assert proposer.calls[0]["dedup_key"] != proposer.calls[1]["dedup_key"]

    async def test_a_duplicate_proposal_is_counted_not_resent(self) -> None:
        pool = _Pool([_event(attendees=[ME, SAM_ATT])], [_person()], None)
        result = await _run(pool, _Proposer("duplicate"))
        assert (result["proposed"], result["duplicate"]) == (0, 1)

    @pytest.mark.parametrize(
        "attendees,meta",
        [
            ([ME], {}),
            ([ME, {"email": "gone@example.test", "response_status": "declined"}], {}),
            ([ME, {"email": "room@example.test", "resource": True}], {}),
            ([{**ME, "response_status": "declined"}, SAM_ATT], {}),
            ([ME, SAM_ATT], {"transparency": "transparent"}),
            ([ME, SAM_ATT], {"butler_generated": True}),
        ],
        ids=["solo", "guest-declined", "room-only", "owner-declined", "transparent", "generated"],
    )
    async def test_events_without_a_real_other_attendee_are_not_carded(
        self, attendees: list[dict[str, Any]], meta: dict[str, Any]
    ) -> None:
        pool, proposer = (
            _Pool([_event(attendees=attendees, **meta)], [_person()], None),
            _Proposer(),
        )
        result = await _run(pool, proposer)
        assert proposer.calls == [] and result["proposed"] == 0

    @pytest.mark.parametrize("posture", ["no_contact", "memorial", "quiet"])
    async def test_a_non_active_person_is_never_named_but_the_meeting_is_still_carded(
        self, posture: str
    ) -> None:
        envelope = _envelope()
        pool = _Pool([_event(attendees=[ME, SAM_ATT])], [_person(posture=posture)], envelope)
        proposer = _Proposer()
        await _run(pool, proposer)
        message = proposer.calls[0]["message"]
        assert "Sam" not in message and "You owe" not in message and "last met" not in message
        assert message.splitlines()[0].startswith("Lunch at")

    async def test_the_owners_own_entity_is_never_listed(self) -> None:
        pool = _Pool([_event(attendees=[ME, SAM_ATT])], [_person(roles=("owner",))], None)
        proposer = _Proposer()
        await _run(pool, proposer)
        assert "With:" not in proposer.calls[0]["message"]

    async def test_a_broker_error_is_counted_and_never_raises(self) -> None:
        class Boom(_Proposer):
            async def __call__(self, pool: Any, **kwargs: Any) -> dict[str, str]:
                raise RuntimeError("broker down")

        pool = _Pool([_event(attendees=[ME, SAM_ATT])], [_person()], None)
        result = await _run(pool, Boom())
        assert (result["proposed"], result["failed"]) == (0, 1)

    async def test_only_the_lead_window_is_queried(self) -> None:
        seen: list[tuple[Any, ...]] = []

        class Spy(_Pool):
            async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
                if "calendar_events" in sql:
                    seen.append(args)
                return await super().fetch(sql, *args)

        await _run(Spy([], [], None), _Proposer())
        assert seen == [(NOW + card.MIN_LEAD, NOW + card.CARD_LEAD)]

"""Real-Postgres meeting card (bu-jv1moa.9): selection SQL, prep envelope read, posture.

Runs the core, memory and relationship Alembic chains, so ``calendar_events``,
``calendar_event_instances``, ``public.entities`` and ``public.state`` are production shapes. The
insight broker is a recording proposer; its queue lives in another schema and delivery is not
under test. People are synthetic.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from butlers.core.state import state_set
from butlers.jobs import meeting_card as card
from butlers.jobs.calendar_prep import prep_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_TRUNCATE = [
    "public.state",
    "public.entities",
    "calendar_event_entities",
    "calendar_events",
]


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setattr(card, "MEETING_CARD_LIVE", True)
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example.test")


@pytest.fixture
def card_url(fresh_db) -> str:
    return fresh_db("core", "memory", "relationship")


@pytest.fixture
async def card_pool(db, card_url: str):
    p = await db.open(card_url, schema="relationship", max_size=1)
    for table in _TRUNCATE:
        await p.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608
    return p


class _Proposer:
    def __init__(self, *statuses: str) -> None:
        self.statuses = list(statuses) or ["accepted"]
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, pool: asyncpg.Pool, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"status": status}


async def _person(pool, name: str, email: str, *, posture: str = "active") -> uuid.UUID:
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, 'person') "
        "RETURNING id",
        name,
    )
    await pool.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
        "VALUES ($1, 'has-email', $2, 'literal', 'test')",
        entity_id,
        email,
    )
    if posture != "active":
        await pool.execute(
            "UPDATE public.entities SET posture = $2 WHERE id = $1", entity_id, posture
        )
    return entity_id


async def _event(
    pool,
    starts_at: datetime,
    *,
    attendees: list[dict] | None = None,
    title: str = "Lunch",
    location: str | None = "Amoy St",
    status: str = "confirmed",
    all_day: bool = False,
) -> uuid.UUID:
    source_id = await pool.fetchval(
        "INSERT INTO calendar_sources (source_key, source_kind) VALUES ($1, 'provider_event') "
        "RETURNING id",
        f"test-{uuid.uuid4().hex[:8]}",
    )
    return await pool.fetchval(
        """
        INSERT INTO calendar_events
            (source_id, origin_ref, title, location, timezone, starts_at, ends_at, all_day,
             status, metadata, source_butler)
        VALUES ($1, $2, $3, $4, 'UTC', $5, $6, $7, $8, $9::text::jsonb, 'relationship')
        RETURNING id
        """,
        source_id,
        uuid.uuid4().hex,
        title,
        location,
        starts_at,
        starts_at + timedelta(hours=1),
        all_day,
        status,
        json.dumps({"attendees": attendees or []}),
    )


def _guests(*others: str) -> list[dict]:
    return [
        {"email": "owner@example.test", "self": True, "response_status": "accepted"},
        *({"email": e, "response_status": "accepted"} for e in others),
    ]


class _Withdrawer:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def __call__(
        self, pool: asyncpg.Pool, *, origin_butler: str, dedup_keys: list[str], **_: Any
    ) -> int:
        self.keys.extend(dedup_keys)
        return len(dedup_keys)


async def _run(pool, proposer: _Proposer, now: datetime, withdrawer=None) -> dict[str, Any]:
    return await card.run_meeting_card(
        pool, insight_proposer=proposer, insight_withdrawer=withdrawer or _Withdrawer(), now=now
    )


NOW = datetime.now(UTC).replace(microsecond=0)


async def test_a_meeting_inside_the_window_gets_one_card_with_its_prep(card_pool) -> None:
    sam = await _person(card_pool, "Sam Lee", "sam@example.test")
    start = NOW + timedelta(minutes=15)
    event = await _event(card_pool, start, attendees=_guests("sam@example.test"))
    await state_set(
        card_pool,
        prep_key(str(event)),
        {
            "event_starts_at": start.isoformat(),
            "attendees": [
                {
                    "entity_id": str(sam),
                    "last_met": "2026-08-03T05:00:00+00:00",
                    "commitments": [{"direction": "owner_to_other", "summary": "send the deck"}],
                }
            ],
        },
    )
    proposer = _Proposer()
    result = await _run(card_pool, proposer, NOW)
    assert (result["considered"], result["proposed"]) == (1, 1)
    call = proposer.calls[0]
    assert call["lane"] == "timed" and call["expires_at"] == start
    assert call["dedup_key"] == f"relationship:meeting-card:{event}:{int(start.timestamp())}"
    assert "With: Sam Lee (last met 3 Aug)" in call["message"]
    assert "You owe: send the deck" in call["message"]


async def test_a_stale_envelope_gives_the_minimal_card(card_pool) -> None:
    sam = await _person(card_pool, "Sam Lee", "sam@example.test")
    start = NOW + timedelta(minutes=15)
    event = await _event(card_pool, start, attendees=_guests("sam@example.test"))
    await state_set(
        card_pool,
        prep_key(str(event)),
        {
            "event_starts_at": (start - timedelta(hours=3)).isoformat(),
            "attendees": [{"entity_id": str(sam), "commitments": []}],
        },
    )
    proposer = _Proposer()
    await _run(card_pool, proposer, NOW)
    assert "No prep context yet." in proposer.calls[0]["message"]


async def test_only_the_lead_window_is_selected(card_pool) -> None:
    await _person(card_pool, "Sam Lee", "sam@example.test")
    guests = _guests("sam@example.test")
    await _event(card_pool, NOW + timedelta(minutes=45), attendees=guests)
    await _event(card_pool, NOW + timedelta(seconds=20), attendees=guests)
    await _event(card_pool, NOW - timedelta(minutes=5), attendees=guests)
    await _event(card_pool, NOW + timedelta(minutes=10), attendees=guests, status="cancelled")
    await _event(card_pool, NOW + timedelta(minutes=10), attendees=guests, all_day=True)
    proposer = _Proposer()
    result = await _run(card_pool, proposer, NOW)
    assert result["considered"] == 0 and proposer.calls == []


@pytest.mark.parametrize("posture", ["no_contact", "memorial"])
async def test_a_non_active_person_is_not_named(card_pool, posture: str) -> None:
    await _person(card_pool, "Sam Lee", "sam@example.test", posture=posture)
    await _event(card_pool, NOW + timedelta(minutes=15), attendees=_guests("sam@example.test"))
    proposer = _Proposer()
    await _run(card_pool, proposer, NOW)
    assert "Sam" not in proposer.calls[0]["message"]
    assert "With:" not in proposer.calls[0]["message"]


async def test_solo_and_declined_meetings_are_not_carded(card_pool) -> None:
    await _event(card_pool, NOW + timedelta(minutes=10), attendees=_guests())
    await _event(
        card_pool,
        NOW + timedelta(minutes=12),
        attendees=[
            {"email": "owner@example.test", "self": True, "response_status": "accepted"},
            {"email": "gone@example.test", "response_status": "declined"},
        ],
    )
    proposer = _Proposer()
    result = await _run(card_pool, proposer, NOW)
    assert result["considered"] == 0 and proposer.calls == []


async def test_a_moved_meeting_is_proposed_under_a_new_key(card_pool) -> None:
    await _person(card_pool, "Sam Lee", "sam@example.test")
    start = NOW + timedelta(minutes=15)
    event = await _event(card_pool, start, attendees=_guests("sam@example.test"))
    proposer = _Proposer()
    await _run(card_pool, proposer, NOW)
    await card_pool.execute(
        "UPDATE calendar_events SET starts_at = $2 WHERE id = $1",
        event,
        start + timedelta(minutes=2),
    )
    await _run(card_pool, proposer, NOW)
    assert proposer.calls[0]["dedup_key"] != proposer.calls[1]["dedup_key"]


async def test_the_owners_other_address_alone_is_not_a_guest(card_pool) -> None:
    owner = await _person(card_pool, "Owner", "alt@example.test")
    await card_pool.execute(
        "UPDATE public.entities SET roles = ARRAY['owner'] WHERE id = $1", owner
    )
    await _event(card_pool, NOW + timedelta(minutes=15), attendees=_guests("alt@example.test"))
    proposer = _Proposer()
    result = await _run(card_pool, proposer, NOW)
    assert result["considered"] == 0 and proposer.calls == []


async def test_a_moved_meeting_withdraws_its_old_card_through_the_broker(
    card_pool, monkeypatch
) -> None:
    """Real broker and real candidates table: the old card is withdrawn, the new one pending."""
    from butlers.roster_imports import roster_module

    timed = roster_module("switchboard", "tools/insight/timed")
    broker = roster_module("switchboard", "tools/insight/broker")
    monkeypatch.setattr(timed, "TIMED_INSIGHTS_LIVE", True)
    await card_pool.execute("TRUNCATE TABLE public.insight_candidates CASCADE")
    await _person(card_pool, "Sam Lee", "sam@example.test")
    start = NOW + timedelta(minutes=15)
    event = await _event(card_pool, start, attendees=_guests("sam@example.test"))

    async def run() -> dict[str, Any]:
        return await card.run_meeting_card(
            card_pool,
            insight_proposer=broker.propose_insight_candidate,
            insight_withdrawer=broker.withdraw_pending_candidates,
            now=NOW,
        )

    first = await run()
    moved = start + timedelta(minutes=3)
    await card_pool.execute("UPDATE calendar_events SET starts_at = $2 WHERE id = $1", event, moved)
    second = await run()

    rows = await card_pool.fetch(
        "SELECT dedup_key, status, metadata FROM public.insight_candidates ORDER BY created_at"
    )
    assert (first["proposed"], second["proposed"], second["withdrawn"]) == (1, 1, 1)
    assert [r["status"] for r in rows] == ["withdrawn", "pending"]
    assert rows[1]["dedup_key"].endswith(f":{int(moved.timestamp())}")

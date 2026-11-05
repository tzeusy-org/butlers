"""Tests for butlers.jobs.calendar_prep — relationship prep contribution job.

Covers the relationship ``calendar_prep_contribution`` deterministic job:
- one per-event envelope written for each entity-linked upcoming event
- attendees resolved with name + Dunbar-tier letter-mark + notes + last-met
- honest empty-state (``has_context=false`` when no attendee resolves)
- prune of stale per-event keys for events outside the window (no-op when none)
- zero LLM (handler takes only ``(pool, job_args)``; no spawner)

The DB-free tests patch ``state_set`` / ``state_list`` / ``state_delete`` to capture
writes/deletes and answer the job's ``pool.fetch`` reads with queued rows in call
order; the commitment filter/ordering/cap test runs on real Postgres.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.core.state import state_get
from butlers.jobs.calendar_prep import (
    MAX_COMMITMENTS_PER_ATTENDEE,
    PREP_KEY_PREFIX,
    prep_key,
    run_relationship_calendar_prep_contribution,
)
from butlers.testing.fake_pool import FakePool
from tests.jobs.calendar_contribution_harness import (
    ContributionStateStore,
    patch_state,
    source_reads,
)

pytestmark = pytest.mark.unit


_MODULE = "butlers.jobs.calendar_prep"


def _prep_reads(
    *,
    events: list[dict[str, Any]],
    names: list[dict[str, Any]] | None = None,
    tiers: list[dict[str, Any]] | None = None,
    notes: list[dict[str, Any]] | None = None,
    last_met: list[dict[str, Any]] | None = None,
    commitments: list[list[dict[str, Any]] | BaseException] | None = None,
) -> FakePool:
    """The job's reads in order.

    Upcoming linked events; then, when any attendee is linked, names, Dunbar
    tiers, notes and last-met; then one owner_conditions commitment read per
    resolved attendee, in event attendee order.
    """
    if not any(event["entity_ids"] for event in events):
        return source_reads(events)
    return source_reads(
        events, names or [], tiers or [], notes or [], last_met or [], *(commitments or [])
    )


async def test_populated_writes_attendee_context():
    """An entity-linked event yields one envelope with resolved attendee context."""
    event_id = uuid4()
    alice, bob = uuid4(), uuid4()
    pool = _prep_reads(
        events=[
            {
                "event_id": event_id,
                "title": "Team lunch",
                "starts_at": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
                "entity_ids": [alice, bob],
            }
        ],
        names=[
            {"id": alice, "name": "Alice Tan"},
            {"id": bob, "name": "Bob Lee"},
        ],
        tiers=[{"entity_id": alice, "content": "5"}],
        notes=[
            {
                "entity_id": alice,
                "predicate": "contact_note",
                "content": "Allergic to shellfish",
                "importance": 8.0,
                "ts": datetime(2026, 1, 1, tzinfo=UTC),
            }
        ],
        last_met=[
            {
                "entity_id": alice,
                "starts_at": datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
                "title": "Quarterly sync",
            }
        ],
        commitments=[[], []],
    )
    cap = ContributionStateStore()

    with patch_state(_MODULE, cap):
        result = await run_relationship_calendar_prep_contribution(pool, None)

    assert result["events_written"] == 1
    assert result["attendees"] == 2

    envelope = cap.store[prep_key(str(event_id))]
    assert envelope["butler"] == "relationship"
    assert envelope["event_id"] == str(event_id)
    assert envelope["has_context"] is True
    attendees = envelope["attendees"]
    assert [a["name"] for a in attendees] == ["Alice Tan", "Bob Lee"]  # name-sorted
    alice_att = attendees[0]
    assert alice_att["dunbar_tier"] == 5
    assert alice_att["notes"] == [{"kind": "contact_note", "text": "Allergic to shellfish"}]
    assert alice_att["last_met"] == "2026-05-01T09:00:00+00:00"
    assert alice_att["last_met_event"] == "Quarterly sync"
    # Bob has no tier / notes / last-met → honest nulls + empty lists.
    bob_att = attendees[1]
    assert bob_att["dunbar_tier"] is None
    assert bob_att["notes"] == []
    assert bob_att["last_met"] is None
    assert bob_att["message_context"] == []
    assert bob_att["commitments"] == []


async def _seed_linked_event(
    pool: asyncpg.Pool, title: str, starts_at: datetime
) -> tuple[Any, Any]:
    """One entity-linked upcoming event; returns ``(event_id, attendee_entity_id)``."""
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ('Alice Tan', 'person') RETURNING id"
    )
    source_id = await pool.fetchval(
        "INSERT INTO calendar_sources (source_key, source_kind) "
        "VALUES ('prep-test', 'provider_event') RETURNING id"
    )
    event_id = await pool.fetchval(
        """
        INSERT INTO calendar_events
            (source_id, origin_ref, title, timezone, starts_at, ends_at, source_butler)
        VALUES ($1, 'prep-test-event', $2, 'UTC', $3, $4, 'relationship')
        RETURNING id
        """,
        source_id,
        title,
        starts_at,
        starts_at + timedelta(hours=1),
    )
    await pool.execute(
        "INSERT INTO calendar_event_entities (event_id, entity_id) VALUES ($1, $2)",
        event_id,
        entity_id,
    )
    return event_id, entity_id


async def _seed_commitment(
    pool: asyncpg.Pool,
    *,
    n: int,
    counterparty: Any,
    level: str,
    last_confirmed_at: datetime,
    fingerprint: str,
    condition_class: str = "commitment",
    resolved: bool = False,
) -> None:
    """One owner_conditions commitment row; ``n`` orders the ``id`` tiebreak."""
    metadata = {
        "class": condition_class,
        "kind": "promise",
        "direction": "owner_to_other",
        "deadline": f"2026-07-{n % 28 + 1:02d}T09:00:00+00:00",
        "counterparty_entity_id": str(counterparty),
    }
    await pool.execute(
        """
        INSERT INTO public.owner_conditions
            (id, source, fingerprint, episode, state, escalation_level, last_confirmed_at,
             summary, metadata, resolved_at, recovered_after_s)
        VALUES ($1, $2, $3, 1, $4, $5, $6, $7, $8::text::jsonb,
                CASE WHEN $9 THEN now() END, CASE WHEN $9 THEN 1.0 END)
        """,
        UUID(int=n + 1),
        f"prep-test-{n}",
        fingerprint,
        "resolved" if resolved else "open",
        level,
        last_confirmed_at,
        f"Commitment {n}",
        json.dumps(metadata),
        resolved,
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_commitments_are_escalation_sorted_and_capped_per_attendee(db):
    """REQ-calendar-overlay-aggregation-005: select a capped L3-to-L0 commitment set.

    Real Postgres: the open/aging, commitment-class, per-counterparty filter and the
    escalation / recency / fingerprint / id ordering with its LIMIT are the subject.
    """
    pool = await db("core", "memory", "relationship", schema="relationship", max_size=1)
    event_id, alice = await _seed_linked_event(
        pool, "Team lunch", datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
    )
    ranked = [
        ("L0", 12, "fingerprint-z"),
        ("L3", 1, "fingerprint-a"),
        ("L0", 10, "fingerprint-a"),
        ("L2", 2, "fingerprint-b"),
        ("L0", 10, "fingerprint-a"),
        ("L1", 3, "fingerprint-c"),
        ("L0", 9, "fingerprint-d"),
        ("L0", 8, "fingerprint-e"),
        ("L0", 7, "fingerprint-f"),
        ("L0", 6, "fingerprint-g"),
        ("L0", 5, "fingerprint-h"),
        ("L0", 4, "fingerprint-i"),
    ]
    assert len(ranked) == MAX_COMMITMENTS_PER_ATTENDEE + 2
    for n, (level, recency, fingerprint) in enumerate(ranked):
        await _seed_commitment(
            pool,
            n=n,
            counterparty=alice,
            level=level,
            last_confirmed_at=datetime(2026, 6, 1 + recency, tzinfo=UTC),
            fingerprint=fingerprint,
        )
    # L3 decoys that would sort first if the state, class or counterparty filter broke.
    newest = datetime(2026, 6, 30, tzinfo=UTC)
    await _seed_commitment(
        pool,
        n=20,
        counterparty=alice,
        level="L3",
        last_confirmed_at=newest,
        fingerprint="decoy-resolved",
        resolved=True,
    )
    await _seed_commitment(
        pool,
        n=21,
        counterparty=alice,
        level="L3",
        last_confirmed_at=newest,
        fingerprint="decoy-class",
        condition_class="obligation",
    )
    await _seed_commitment(
        pool,
        n=22,
        counterparty=uuid4(),
        level="L3",
        last_confirmed_at=newest,
        fingerprint="decoy-counterparty",
    )

    with patch(f"{_MODULE}.today_sgt", return_value=date(2026, 6, 21)):
        await run_relationship_calendar_prep_contribution(pool, None)

    envelope = await state_get(pool, prep_key(str(event_id)))
    commitments = envelope["attendees"][0]["commitments"]
    assert [commitment["summary"] for commitment in commitments] == [
        "Commitment 1",
        "Commitment 3",
        "Commitment 5",
        "Commitment 0",
        "Commitment 2",
        "Commitment 4",
        "Commitment 6",
        "Commitment 7",
        "Commitment 8",
        "Commitment 9",
    ]
    assert commitments[0] == {
        "kind": "promise",
        "direction": "owner_to_other",
        "summary": "Commitment 1",
        "deadline": "2026-07-02T09:00:00+00:00",
        "escalation_level": "L3",
        "fingerprint": "fingerprint-a",
    }


async def test_commitment_query_failure_keeps_existing_context_and_logs_warning(caplog):
    """REQ-calendar-overlay-aggregation-005: commitment failure preserves prep context."""
    event_id = uuid4()
    alice = uuid4()
    pool = _prep_reads(
        events=[
            {
                "event_id": event_id,
                "title": "Team lunch",
                "starts_at": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
                "entity_ids": [alice],
            }
        ],
        names=[{"id": alice, "name": "Alice Tan"}],
        tiers=[{"entity_id": alice, "content": "5"}],
        notes=[
            {
                "entity_id": alice,
                "predicate": "contact_note",
                "content": "Allergic to shellfish",
                "importance": 8.0,
                "ts": datetime(2026, 1, 1, tzinfo=UTC),
            }
        ],
        last_met=[
            {
                "entity_id": alice,
                "starts_at": datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
                "title": "Quarterly sync",
            }
        ],
        commitments=[RuntimeError("owner_conditions unavailable")],
    )
    cap = ContributionStateStore()

    with patch_state(_MODULE, cap), caplog.at_level("WARNING", logger="butlers.jobs.calendar_prep"):
        await run_relationship_calendar_prep_contribution(pool, None)

    attendee = cap.store[prep_key(str(event_id))]["attendees"][0]
    assert attendee["dunbar_tier"] == 5
    assert attendee["notes"] == [{"kind": "contact_note", "text": "Allergic to shellfish"}]
    assert attendee["last_met"] == "2026-05-01T09:00:00+00:00"
    assert attendee["last_met_event"] == "Quarterly sync"
    assert attendee["commitments"] == []
    warning = next(record for record in caplog.records if record.levelname == "WARNING")
    assert str(event_id) in warning.message
    assert str(alice) in warning.message


async def test_empty_when_no_events():
    """No entity-linked events in the window → no writes (honest empty cache)."""
    pool = _prep_reads(events=[])
    cap = ContributionStateStore()

    with patch_state(_MODULE, cap):
        result = await run_relationship_calendar_prep_contribution(pool, None)

    assert result["events_written"] == 0
    assert result["attendees"] == 0
    assert cap.store == {}


async def test_unresolved_attendee_yields_empty_context():
    """An event whose linked entity is not in the registry → has_context=false."""
    event_id = uuid4()
    ghost = uuid4()
    pool = _prep_reads(
        events=[
            {
                "event_id": event_id,
                "title": "Mystery meeting",
                "starts_at": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
                "entity_ids": [ghost],
            }
        ],
        names=[],  # ghost entity not resolvable
    )
    cap = ContributionStateStore()

    with patch_state(_MODULE, cap):
        result = await run_relationship_calendar_prep_contribution(pool, None)

    assert result["events_written"] == 1
    envelope = cap.store[prep_key(str(event_id))]
    assert envelope["has_context"] is False
    assert envelope["attendees"] == []


async def test_prune_removes_stale_event_keys():
    """Prep keys for events no longer in the window are pruned; live keys kept."""
    live_event = uuid4()
    stale_event = uuid4()
    alice = uuid4()
    pool = _prep_reads(
        events=[
            {
                "event_id": live_event,
                "title": "Still upcoming",
                "starts_at": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
                "entity_ids": [alice],
            }
        ],
        names=[{"id": alice, "name": "Alice Tan"}],
        commitments=[[]],
    )
    # Seed a stale envelope from a previous run for an event no longer in range.
    cap = ContributionStateStore(
        seed={f"{PREP_KEY_PREFIX}{stale_event}": {"butler": "relationship"}}
    )

    with patch_state(_MODULE, cap):
        result = await run_relationship_calendar_prep_contribution(pool, None)

    assert result["pruned"] == 1
    assert f"{PREP_KEY_PREFIX}{stale_event}" in cap.deleted
    assert prep_key(str(live_event)) in cap.store
    assert f"{PREP_KEY_PREFIX}{stale_event}" not in cap.store


async def test_handler_signature_is_zero_llm():
    """The job is a deterministic (pool, job_args) handler — no spawner/LLM."""
    import inspect

    sig = inspect.signature(run_relationship_calendar_prep_contribution)
    assert list(sig.parameters) == ["pool", "job_args"]

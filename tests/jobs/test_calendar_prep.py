"""Tests for butlers.jobs.calendar_prep — relationship prep contribution job.

Covers the relationship ``calendar_prep_contribution`` deterministic job:
- one per-event envelope written for each entity-linked upcoming event
- attendees resolved with name + Dunbar-tier letter-mark + notes + last-met
- honest empty-state (``has_context=false`` when no attendee resolves)
- prune of stale per-event keys for events outside the window (no-op when none)
- zero LLM (handler takes only ``(pool, job_args)``; no spawner)

No real database required — ``state_set`` / ``state_list`` / ``state_delete`` are
patched to capture writes/deletes, and ``pool.fetch`` is routed by SQL text.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from butlers.jobs.calendar_prep import (
    PREP_KEY_PREFIX,
    prep_key,
    run_relationship_calendar_prep_contribution,
)

pytestmark = pytest.mark.unit


class _FakePool:
    """Routes ``pool.fetch`` to the prep job's five queries by SQL content."""

    def __init__(
        self,
        *,
        events: list[dict[str, Any]] | None = None,
        names: list[dict[str, Any]] | None = None,
        tiers: list[dict[str, Any]] | None = None,
        notes: list[dict[str, Any]] | None = None,
        last_met: list[dict[str, Any]] | None = None,
    ) -> None:
        self._events = events or []
        self._names = names or []
        self._tiers = tiers or []
        self._notes = notes or []
        self._last_met = last_met or []
        self.fetch_calls: list[str] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append(sql)
        if "array_agg(DISTINCT cee.entity_id)" in sql:
            return self._events
        if "FROM public.entities" in sql:
            return self._names
        if "dunbar_tier_override" in sql:
            return self._tiers
        if "predicate = ANY($2::text[])" in sql:
            return self._notes
        if "DISTINCT ON (cee.entity_id)" in sql:
            return self._last_met
        raise AssertionError(f"unexpected query: {sql}")


class _StateCapture:
    """Captures state_set/state_list/state_delete into an in-memory dict."""

    def __init__(self, seed: dict[str, Any] | None = None) -> None:
        self.store: dict[str, Any] = dict(seed or {})
        self.deleted: list[str] = []

    async def state_set(self, _pool: Any, key: str, value: Any) -> int:
        self.store[key] = value
        return 1

    async def state_list(self, _pool: Any, *, prefix: str) -> list[str]:
        return [k for k in self.store if k.startswith(prefix)]

    async def state_delete(self, _pool: Any, key: str) -> None:
        self.store.pop(key, None)
        self.deleted.append(key)


def _patch_state(capture: _StateCapture):
    return patch.multiple(
        "butlers.jobs.calendar_prep",
        state_set=capture.state_set,
        state_list=capture.state_list,
        state_delete=capture.state_delete,
    )


async def test_populated_writes_attendee_context():
    """An entity-linked event yields one envelope with resolved attendee context."""
    event_id = uuid4()
    alice, bob = uuid4(), uuid4()
    pool = _FakePool(
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
    )
    cap = _StateCapture()

    with _patch_state(cap):
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


async def test_empty_when_no_events():
    """No entity-linked events in the window → no writes (honest empty cache)."""
    pool = _FakePool(events=[])
    cap = _StateCapture()

    with _patch_state(cap):
        result = await run_relationship_calendar_prep_contribution(pool, None)

    assert result["events_written"] == 0
    assert result["attendees"] == 0
    assert cap.store == {}


async def test_unresolved_attendee_yields_empty_context():
    """An event whose linked entity is not in the registry → has_context=false."""
    event_id = uuid4()
    ghost = uuid4()
    pool = _FakePool(
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
    cap = _StateCapture()

    with _patch_state(cap):
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
    pool = _FakePool(
        events=[
            {
                "event_id": live_event,
                "title": "Still upcoming",
                "starts_at": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
                "entity_ids": [alice],
            }
        ],
        names=[{"id": alice, "name": "Alice Tan"}],
    )
    # Seed a stale envelope from a previous run for an event no longer in range.
    cap = _StateCapture(seed={f"{PREP_KEY_PREFIX}{stale_event}": {"butler": "relationship"}})

    with _patch_state(cap):
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

"""Tests for the email/message-context prep contribution job (messenger / travel).

Covers ``run_email_calendar_prep_contribution`` (and its messenger/travel
wrappers) in ``butlers.jobs.calendar_prep``:
- one per-event envelope written per entity-linked event that has recent threads
- each attendee carries a ``message_context`` list of recent email threads
- events with NO recent message context are skipped (honest empty-state)
- thread cap per attendee + snippet/subject fallback
- prune of stale per-event keys
- fail-open when ``switchboard.message_inbox`` is unreadable
- zero LLM (handlers take only ``(pool, job_args)``; no spawner)

No real database required — ``state_*`` helpers are patched and ``pool.fetch`` is
routed by SQL text.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from butlers.jobs.calendar_prep import (
    MAX_THREADS_PER_ATTENDEE,
    PREP_KEY_PREFIX,
    prep_key,
    run_email_calendar_prep_contribution,
    run_messenger_calendar_prep_contribution,
    run_travel_calendar_prep_contribution,
)

pytestmark = pytest.mark.unit


class _FakePool:
    """Routes ``pool.fetch`` to the message-context job's three queries by SQL."""

    def __init__(
        self,
        *,
        events: list[dict[str, Any]] | None = None,
        names: list[dict[str, Any]] | None = None,
        threads: list[dict[str, Any]] | None = None,
        threads_raise: bool = False,
    ) -> None:
        self._events = events or []
        self._names = names or []
        self._threads = threads or []
        self._threads_raise = threads_raise
        self.fetch_calls: list[str] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append(sql)
        if "array_agg(DISTINCT cee.entity_id)" in sql:
            return self._events
        if "FROM public.entities" in sql:
            return self._names
        if "FROM switchboard.message_inbox" in sql:
            if self._threads_raise:
                raise RuntimeError("permission denied for table message_inbox")
            return self._threads
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


def _thread_row(
    *,
    entity_id: Any,
    thread_identity: str,
    subject: str | None,
    latest_text: str,
    last_message_at: datetime,
    message_count: int,
) -> dict[str, Any]:
    return {
        "entity_id": str(entity_id),
        "thread_identity": thread_identity,
        "subject": subject,
        "latest_text": latest_text,
        "last_message_at": last_message_at,
        "message_count": message_count,
    }


async def test_populated_writes_message_context():
    """An attendee with recent email threads gets a message_context panel."""
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
        threads=[
            _thread_row(
                entity_id=alice,
                thread_identity="thread-1",
                subject="Lunch plans",
                latest_text="  See you   Thursday!  ",
                last_message_at=datetime(2026, 6, 20, 9, 0, tzinfo=UTC),
                message_count=3,
            ),
            # Bob has no threads → he is excluded from the email envelope.
        ],
    )
    cap = _StateCapture()

    with _patch_state(cap):
        result = await run_messenger_calendar_prep_contribution(pool, None)

    assert result["butler"] == "messenger"
    assert result["events_written"] == 1
    assert result["attendees"] == 1
    assert result["threads"] == 1

    envelope = cap.store[prep_key(str(event_id))]
    assert envelope["butler"] == "messenger"
    assert envelope["event_id"] == str(event_id)
    assert envelope["has_context"] is True
    attendees = envelope["attendees"]
    assert [a["name"] for a in attendees] == ["Alice Tan"]  # bob excluded (no threads)
    ctx = attendees[0]["message_context"]
    assert len(ctx) == 1
    assert ctx[0]["channel"] == "email"
    assert ctx[0]["thread_id"] == "thread-1"
    assert ctx[0]["subject"] == "Lunch plans"
    assert ctx[0]["snippet"] == "See you Thursday!"  # whitespace collapsed
    assert ctx[0]["last_message_at"] == "2026-06-20T09:00:00+00:00"
    assert ctx[0]["message_count"] == 3
    # The email envelope only carries the message-context slot for attendees.
    assert attendees[0]["notes"] == []
    assert attendees[0]["last_met"] is None


async def test_event_with_no_threads_is_skipped():
    """An entity-linked event with no recent threads writes no envelope."""
    event_id = uuid4()
    alice = uuid4()
    pool = _FakePool(
        events=[
            {
                "event_id": event_id,
                "title": "Solo block",
                "starts_at": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
                "entity_ids": [alice],
            }
        ],
        names=[{"id": alice, "name": "Alice Tan"}],
        threads=[],  # no message context for anyone
    )
    cap = _StateCapture()

    with _patch_state(cap):
        result = await run_travel_calendar_prep_contribution(pool, None)

    assert result["butler"] == "travel"
    assert result["events_written"] == 0
    assert cap.store == {}


async def test_thread_cap_and_subject_fallback():
    """Threads are capped per attendee; missing subject falls back to the snippet."""
    event_id = uuid4()
    alice = uuid4()
    threads = [
        _thread_row(
            entity_id=alice,
            thread_identity=f"thread-{i}",
            subject="" if i == 0 else f"Subject {i}",
            latest_text=f"Body of message {i}",
            last_message_at=datetime(2026, 6, 20 - i, 9, 0, tzinfo=UTC),
            message_count=1,
        )
        for i in range(MAX_THREADS_PER_ATTENDEE + 2)
    ]
    pool = _FakePool(
        events=[
            {
                "event_id": event_id,
                "title": "Catch up",
                "starts_at": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
                "entity_ids": [alice],
            }
        ],
        names=[{"id": alice, "name": "Alice Tan"}],
        threads=threads,
    )
    cap = _StateCapture()

    with _patch_state(cap):
        await run_email_calendar_prep_contribution(pool, None, butler_name="messenger")

    ctx = cap.store[prep_key(str(event_id))]["attendees"][0]["message_context"]
    assert len(ctx) == MAX_THREADS_PER_ATTENDEE  # capped
    # First thread had an empty subject → snippet-derived fallback (not "(no subject)").
    assert ctx[0]["subject"] == "Body of message 0"


async def test_fail_open_when_message_inbox_unreadable():
    """A switchboard.message_inbox read error surfaces no context (no raise)."""
    event_id = uuid4()
    alice = uuid4()
    pool = _FakePool(
        events=[
            {
                "event_id": event_id,
                "title": "Catch up",
                "starts_at": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
                "entity_ids": [alice],
            }
        ],
        names=[{"id": alice, "name": "Alice Tan"}],
        threads_raise=True,
    )
    cap = _StateCapture()

    with _patch_state(cap):
        result = await run_email_calendar_prep_contribution(pool, None, butler_name="travel")

    # Fail-open: no message context → no envelope, but the job completes.
    assert result["events_written"] == 0
    assert cap.store == {}


async def test_prune_removes_stale_event_keys():
    """Prep keys for events without current message context are pruned."""
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
        threads=[
            _thread_row(
                entity_id=alice,
                thread_identity="thread-1",
                subject="Hi",
                latest_text="hello",
                last_message_at=datetime(2026, 6, 20, 9, 0, tzinfo=UTC),
                message_count=1,
            )
        ],
    )
    cap = _StateCapture(seed={f"{PREP_KEY_PREFIX}{stale_event}": {"butler": "messenger"}})

    with _patch_state(cap):
        result = await run_email_calendar_prep_contribution(pool, None, butler_name="messenger")

    assert result["pruned"] == 1
    assert f"{PREP_KEY_PREFIX}{stale_event}" in cap.deleted
    assert prep_key(str(live_event)) in cap.store


async def test_handler_signatures_are_zero_llm():
    """The messenger/travel handlers are deterministic (pool, job_args) — no LLM."""
    import inspect

    for fn in (
        run_messenger_calendar_prep_contribution,
        run_travel_calendar_prep_contribution,
    ):
        sig = inspect.signature(fn)
        assert list(sig.parameters) == ["pool", "job_args"]

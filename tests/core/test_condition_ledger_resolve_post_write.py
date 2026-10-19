"""Explicit resolution runs its post_write hook in the resolving transaction (bu-q7vx1q.32).

The engine's ``resolve_condition`` mirrors ``reconcile_snapshot``: a caller-supplied
``post_write`` runs inside the same transaction, after the resolution write and before
commit. The owner-condition facade uses it to enqueue premise amendments, so an explicit
resolution amends the delivered insight exactly like a snapshot resolution does.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core import condition_ledger, insight_premise, owner_conditions
from butlers.core.condition_ledger import ConditionTransition

pytestmark = pytest.mark.unit

_RESOLVED_AT = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)


def _transition() -> ConditionTransition:
    return ConditionTransition(
        condition_id=uuid.uuid4(),
        source="finance:bill-overdue",
        fingerprint="fp-1",
        episode=1,
        state="resolved",
        transition="resolved",
        escalation_level="L0",
        next_reescalate_at=None,
        resolved_at=_RESOLVED_AT,
    )


def _pool(events: list[str], *, active: bool = True) -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=_RESOLVED_AT)
    conn.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()} if active else None)

    @asynccontextmanager
    async def transaction():
        events.append("begin")
        try:
            yield
        except BaseException:
            events.append("rollback")
            raise
        events.append("commit")

    conn.transaction = transaction

    @asynccontextmanager
    async def acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = acquire
    return pool


async def _resolve(pool, monkeypatch, events, **kwargs):
    async def fake_resolve_episode(conn, row, **_kw):
        events.append("resolved-write")
        return _transition()

    monkeypatch.setattr(condition_ledger, "_resolve_episode", fake_resolve_episode)
    return await condition_ledger.resolve_condition(
        pool, table="public.owner_conditions", source="s", fingerprint="fp-1", **kwargs
    )


async def test_post_write_runs_inside_the_transaction_after_the_resolution_write(monkeypatch):
    events: list[str] = []
    seen: list[list[ConditionTransition]] = []

    async def hook(conn, transitions):
        events.append("hook")
        seen.append(transitions)

    result = await _resolve(_pool(events), monkeypatch, events, post_write=hook)

    assert events == ["begin", "resolved-write", "hook", "commit"]
    assert seen == [[result]]


async def test_a_hook_failure_rolls_the_resolution_back(monkeypatch):
    events: list[str] = []

    async def hook(conn, transitions):
        raise RuntimeError("hook failed")

    with pytest.raises(RuntimeError, match="hook failed"):
        await _resolve(_pool(events), monkeypatch, events, post_write=hook)

    assert events == ["begin", "resolved-write", "rollback"]


async def test_no_active_episode_means_no_hook_and_no_transition(monkeypatch):
    events: list[str] = []
    hook = AsyncMock()

    result = await _resolve(_pool(events, active=False), monkeypatch, events, post_write=hook)

    assert result is None
    hook.assert_not_awaited()


async def test_without_a_hook_resolution_is_unchanged(monkeypatch):
    events: list[str] = []

    result = await _resolve(_pool(events), monkeypatch, events)

    assert isinstance(result, ConditionTransition)
    assert events == ["begin", "resolved-write", "commit"]


async def test_the_owner_condition_facade_enqueues_amendments_through_the_engine(monkeypatch):
    calls: list[dict] = []

    async def fake_engine(pool, **kwargs):
        calls.append(kwargs)
        return "transition"

    monkeypatch.setattr(owner_conditions, "_resolve_condition", fake_engine)

    await owner_conditions.resolve_condition(AsyncMock(), source="s", fingerprint="fp-1")

    assert calls[0]["post_write"] is insight_premise.enqueue_premise_amendments


async def test_the_facade_hook_enqueues_exactly_the_resolved_transition():
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=1)

    @asynccontextmanager
    async def transaction():
        yield

    conn.transaction = transaction
    transition = _transition()

    queued = await insight_premise.enqueue_premise_amendments(conn, [transition])

    assert queued == 1
    args = conn.fetchval.await_args.args
    assert args[1:4] == (transition.source, transition.fingerprint, _RESOLVED_AT)

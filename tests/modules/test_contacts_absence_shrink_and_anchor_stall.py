"""Telegram absence deletion: set-based shrink guard and anchor-stall visibility.

[bu-8qxebh]
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from butlers.modules.contacts.backfill import ContactBackfillEngine
from butlers.modules.contacts.sync import (
    ANCHOR_STALL_ERROR,
    DEFAULT_FAILURE_ERROR,
    CanonicalContact,
    ContactsSyncError,
    ContactsSyncState,
    ContactsSyncStateStore,
)


class _Conn:
    def __init__(self, candidates):
        self._candidates = candidates
        self.deleted_ids: list[str] = []
        self.executes: list[tuple] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetch(self, sql, *args):
        if sql.startswith("UPDATE"):
            self.deleted_ids = list(args[2])
            return [{"external_contact_id": i, "local_entity_id": None} for i in args[2]]
        return self._candidates

    async def execute(self, sql, *args):
        self.executes.append((sql, args))


class _Pool:
    def __init__(self, candidates=()):
        self.conn = _Conn(list(candidates))
        self.fetchrow = AsyncMock()
        self.fetch = AsyncMock(return_value=[])

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def _row(ext_id):
    return {
        "external_contact_id": ext_id,
        "local_entity_id": None,
        "source_etag": None,
        "last_seen_at": None,
        "deleted_at": None,
    }


def _setup(active_ids):
    baseline = {i: _row(i) for i in active_ids}
    absent = [_row(i) for i in sorted(active_ids)]
    pool = _Pool(absent)
    engine = ContactBackfillEngine(pool, provider="telegram", account_id="acct")
    store = ContactsSyncStateStore(pool)
    state = ContactsSyncState()
    store.lock_state = AsyncMock(return_value=state)
    store.save = AsyncMock()
    return engine, pool, store, state, baseline


async def _finalize(engine, store, state, baseline, present_ids):
    contacts = [CanonicalContact(external_id=i) for i in present_ids]
    return await engine.finalize_sync(
        contacts=contacts,
        mode="full",
        baseline=baseline,
        expected_state=state,
        next_state=state,
        state_store=store,
    )


async def test_additions_cannot_offset_removals():
    active = [f"old{i}" for i in range(10)]
    engine, pool, store, state, baseline = _setup(active)
    # 10 removed + 10 added: count-neutral, but every active link is absent.
    present = [f"new{i}" for i in range(10)]
    assert await _finalize(engine, store, state, baseline, present) == 0
    assert pool.conn.deleted_ids == []


async def test_removal_at_threshold_is_still_accepted():
    active = [f"c{i}" for i in range(10)]
    engine, pool, store, state, baseline = _setup(active)
    present = active[:5] + [f"new{i}" for i in range(7)]  # 5 of 10 removed == 0.5
    candidates = [_row(i) for i in active[5:]]
    pool.conn._candidates = candidates
    assert await _finalize(engine, store, state, baseline, present) == 5


async def test_removal_just_over_threshold_is_refused_despite_additions():
    active = [f"c{i}" for i in range(10)]
    engine, pool, store, state, baseline = _setup(active)
    present = active[:4] + [f"new{i}" for i in range(20)]  # 6 of 10 removed
    assert await _finalize(engine, store, state, baseline, present) == 0
    assert pool.conn.deleted_ids == []


async def test_empty_active_set_deletes_nothing():
    engine, pool, store, state, baseline = _setup([])
    assert await _finalize(engine, store, state, baseline, ["new0"]) == 0
    assert pool.conn.deleted_ids == []


@pytest.mark.parametrize("anchor_row", [{"local_entity_id": "x", "live": False}])
async def test_merged_anchor_stall_records_distinct_operator_visible_error(anchor_row):
    engine, pool, store, state, baseline = _setup(["a"])
    pool.fetchrow.return_value = anchor_row
    with pytest.raises(ContactsSyncError):
        await engine(CanonicalContact(external_id="a"))
    await engine.record_failure(state_store=store, expected_state=state, expired_cursor=False)
    sql, args = pool.conn.executes[-1]
    assert "last_error=$4" in sql
    assert args[-1] == ANCHOR_STALL_ERROR


async def test_ordinary_failure_keeps_generic_error_and_flag_resets():
    engine, pool, store, state, baseline = _setup(["a"])
    pool.fetchrow.return_value = {"local_entity_id": None, "live": False}
    with pytest.raises(ContactsSyncError):
        await engine(CanonicalContact(external_id="a"))
    await engine.record_failure(state_store=store, expected_state=state, expired_cursor=False)
    assert pool.conn.executes[-1][1][-1] == ANCHOR_STALL_ERROR
    # A later unrelated failure must not inherit the stall marker.
    await engine.record_failure(state_store=store, expected_state=state, expired_cursor=False)
    assert pool.conn.executes[-1][1][-1] == DEFAULT_FAILURE_ERROR


async def test_entity_vanished_after_anchor_check_records_stall_error():
    engine, pool, store, state, _ = _setup(["a"])
    pool.fetchrow.return_value = {"local_entity_id": str(uuid.uuid4()), "live": True}
    pool.fetchval = AsyncMock(return_value=None)
    with pytest.raises(ContactsSyncError):
        await engine(CanonicalContact(external_id="a"))
    await engine.record_failure(state_store=store, expected_state=state, expired_cursor=False)
    assert pool.conn.executes[-1][1][-1] == ANCHOR_STALL_ERROR

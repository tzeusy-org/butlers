"""The ordinary deferred flush owns plain pending rows only (bu-kqnum.3.6.1).

Slice A of strict-owner-telegram-wake-recovery (time-aware-delivery "Fenced Wake-Recovery
Cohorts Are Scheduler-Ineligible", task 3.3a). A row in a ``release_*`` protocol state must
never be selected, expired or transitioned by the ordinary loop. Slice B1 (bu-kqnum.3.6.2): the
whole pass runs under the schema's deferred-release lock, which wake-recovery prepare also takes,
so prepare can never reserve a row the flush has fetched and is mid-send.

The pool here interprets the four statements the flush issues by their WHERE clauses: a
statement that drops the ``status = 'pending'`` guard therefore changes what it does to a
protocol row, which is what these tests observe. Its ``acquire()`` connection models the
session advisory lock. Real-Postgres coverage of the same fence is
in ``tests/integration/test_deferred_flush_protocol_fence_roundtrip.py`` (Docker; CI-only).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.scheduler import _PARTIAL_DEFERRED, _tick_deferred_notification_pass

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
_PROTOCOL = (
    "release_prepared",
    "release_committed",
    "release_retained_dnd",
    "release_retained_unavailable",
    "release_retained_oversize",
    "release_retained_mismatch",
    "release_retained_commit_error",
)


def _row(status: str, *, age: timedelta, recipient: str = "owner", wake: bool = False) -> dict:
    label = f"{status} {uuid.uuid4().hex[:6]}"
    return {
        "id": uuid.uuid4(),
        "butler_name": "health",
        "channel": "telegram",
        "message": label,
        "priority": "medium",
        "envelope": {
            "schema_version": "notify.v1",
            "origin_butler": "health",
            "delivery": {
                "intent": "send",
                "channel": "telegram",
                "message": label,
                "recipient": recipient,
            },
        },
        "deliver_at": _NOW - age,
        "status": status,
        "delivered_at": None,
        "wake_hold_kind": "owner_attention_quiet_hours" if wake else None,
    }


class _LockConn:
    """One pooled connection; its advisory-lock calls toggle the pool's lock state."""

    def __init__(self, pool: _FlushPool) -> None:
        self.pool = pool

    async def fetchval(self, sql: str, *args: Any) -> bool:
        assert "pg_try_advisory_lock(hashtext('deferred-release:'" in sql
        if self.pool.held_elsewhere:
            return False
        self.pool.locked = True
        return True

    async def execute(self, sql: str, *args: Any) -> str:
        assert "pg_advisory_unlock(hashtext('deferred-release:'" in sql
        self.pool.locked = False
        return "SELECT 1"


class _FlushPool:
    """In-memory deferred_notifications that honours the WHERE clause of each statement."""

    def __init__(self, rows: list[dict], *, held_elsewhere: bool = False) -> None:
        self.rows = rows
        self.statements: list[str] = []
        self.held_elsewhere = held_elsewhere
        self.locked = False
        self.unlocked_statements: list[str] = []

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_LockConn]:
        yield _LockConn(self)

    def _record(self, sql: str) -> None:
        self.statements.append(sql)
        if not self.locked:
            self.unlocked_statements.append(sql)

    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        self._record(sql)
        if "id = ANY" in sql:  # partial-delivery quarantine reconcile
            fenced = "NOT LIKE 'release\\_%'" in sql
            return [
                dict(r)
                for r in self.rows
                if str(r["id"]) in args[0] and not (fenced and r["status"].startswith("release_"))
            ]
        guarded = "status = 'pending'" in sql
        return [
            dict(r)
            for r in self.rows
            if r["deliver_at"] <= args[0] and (r["status"] == "pending" or not guarded)
        ]

    async def execute(self, sql: str, *args: Any) -> str:
        self._record(sql)
        guarded = "AND status = 'pending'" in sql or "WHERE status = 'pending'" in sql
        if "SET status = 'expired'" in sql:
            targets = [r for r in self.rows if r["deliver_at"] < args[0]]
            new = {"status": "expired"}
        elif "SET status = 'cancelled'" in sql:
            targets = [r for r in self.rows if r["id"] in args[0]]
            new = {"status": "cancelled"}
        else:
            ids = args[0] if isinstance(args[0], list) else [args[0]]
            targets = [r for r in self.rows if r["id"] in ids]
            new = {"status": "delivered", "delivered_at": args[1]}
        for row in targets:
            if row["status"] == "pending" or not guarded:
                row.update(new)
        return f"UPDATE {len(targets)}"


async def _flush(pool: _FlushPool, notify_fn: AsyncMock) -> int:
    with patch("butlers.core.attention_ledger.record_attention_event", AsyncMock()):
        return await _tick_deferred_notification_pass(pool, _NOW, notify_fn=notify_fn)


def _statuses(pool: _FlushPool) -> dict[str, str]:
    return {r["message"]: r["status"] for r in pool.rows}


@pytest.mark.parametrize("state", _PROTOCOL)
@pytest.mark.parametrize("age", [timedelta(minutes=5), timedelta(hours=30)], ids=["due", "stale"])
async def test_a_protocol_state_row_is_never_selected_expired_or_transitioned(
    state: str, age: timedelta
) -> None:
    held = _row(state, age=age, wake=True)
    pool = _FlushPool([held])
    notify_fn = AsyncMock()

    delivered = await _flush(pool, notify_fn)

    assert delivered == 0
    notify_fn.assert_not_awaited()
    assert held["status"] == state and held["delivered_at"] is None


async def test_plain_pending_rows_deliver_and_expire_beside_protocol_rows() -> None:
    due = _row("pending", age=timedelta(minutes=5), recipient="a", wake=True)
    stale = _row("pending", age=timedelta(hours=30), recipient="b", wake=True)
    legacy = _row("pending", age=timedelta(minutes=5), recipient="c")
    legacy_stale = _row("pending", age=timedelta(hours=30), recipient="d")
    prepared = _row("release_prepared", age=timedelta(minutes=5), recipient="e", wake=True)
    committed = _row("release_committed", age=timedelta(hours=30), recipient="f", wake=True)
    pool = _FlushPool([due, stale, legacy, legacy_stale, prepared, committed])
    notify_fn = AsyncMock()

    delivered = await _flush(pool, notify_fn)

    assert delivered == 2
    assert [call.args[0]["delivery"]["recipient"] for call in notify_fn.await_args_list] == [
        "a",
        "c",
    ]
    assert _statuses(pool) == {
        due["message"]: "delivered",
        stale["message"]: "expired",
        legacy["message"]: "delivered",
        legacy_stale["message"]: "expired",
        prepared["message"]: "release_prepared",
        committed["message"]: "release_committed",
    }


async def test_the_flush_is_skipped_while_another_session_holds_the_release_lock() -> None:
    row = _row("pending", age=timedelta(minutes=5), wake=True)
    pool = _FlushPool([row], held_elsewhere=True)
    notify_fn = AsyncMock()

    assert await _flush(pool, notify_fn) == 0

    notify_fn.assert_not_awaited()
    assert pool.statements == []
    assert row["status"] == "pending"


async def test_every_read_send_and_status_write_runs_inside_the_release_lock() -> None:
    first = _row("pending", age=timedelta(minutes=5), recipient="a", wake=True)
    second = _row("pending", age=timedelta(minutes=6), recipient="a", wake=True)
    solo = _row("pending", age=timedelta(minutes=5), recipient="b")
    pool = _FlushPool([first, second, solo])
    _PARTIAL_DEFERRED.mark(pool, str(uuid.uuid4()), {"partial_delivery": True})
    held_during_send: list[bool] = []

    async def send(_envelope: dict) -> None:
        held_during_send.append(pool.locked)

    assert await _flush(pool, AsyncMock(side_effect=send)) == 3

    assert held_during_send == [True, True]
    assert pool.statements and pool.unlocked_statements == []
    assert not pool.locked


@pytest.mark.parametrize("composed", [False, True], ids=["solo", "composed"])
async def test_a_row_cancelled_while_its_send_is_in_flight_stays_cancelled(composed: bool) -> None:
    rows = [_row("pending", age=timedelta(minutes=5), wake=True)]
    if composed:
        rows.append(_row("pending", age=timedelta(minutes=6), wake=True))
    pool = _FlushPool(rows)

    async def cancel_while_sending(_envelope: dict) -> None:
        rows[-1]["status"] = "cancelled"

    await _flush(pool, AsyncMock(side_effect=cancel_while_sending))

    assert rows[-1]["status"] == "cancelled" and rows[-1]["delivered_at"] is None
    if composed:
        assert rows[0]["status"] == "delivered"


async def test_a_quarantined_send_later_reserved_by_the_protocol_is_left_to_it() -> None:
    row = _row("release_prepared", age=timedelta(minutes=5), wake=True)
    pool = _FlushPool([row])
    _PARTIAL_DEFERRED.mark(pool, str(row["id"]), {"partial_delivery": True})
    record = AsyncMock()

    with patch("butlers.core.attention_ledger.record_attention_event", record):
        await _tick_deferred_notification_pass(pool, _NOW, notify_fn=AsyncMock())

    record.assert_not_awaited()
    assert row["status"] == "release_prepared"
    assert not _PARTIAL_DEFERRED.contains(pool, str(row["id"]))

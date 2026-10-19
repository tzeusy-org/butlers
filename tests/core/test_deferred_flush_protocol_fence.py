"""The ordinary deferred flush owns plain pending rows only (bu-kqnum.3.6.1).

Slice A of strict-owner-telegram-wake-recovery (time-aware-delivery "Fenced Wake-Recovery
Cohorts Are Scheduler-Ineligible", task 3.3). A row in a ``release_*`` protocol state must
never be selected, expired or transitioned by the ordinary loop, and a row reserved after the
due-fetch must not have its status overwritten by the flush's own status writes.

The pool here interprets the four statements the flush issues by their WHERE clauses: a
statement that drops the ``status = 'pending'`` guard therefore changes what it does to a
protocol row, which is what these tests observe. Real-Postgres coverage of the same fence is
in ``tests/integration/test_deferred_flush_protocol_fence_roundtrip.py`` (Docker; CI-only).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.scheduler import _tick_deferred_notification_pass

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


class _FlushPool:
    """In-memory deferred_notifications that honours the WHERE clause of each statement."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.statements: list[str] = []

    async def fetchval(self, sql: str, *args: Any) -> bool:
        return True

    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        self.statements.append(sql)
        guarded = "status = 'pending'" in sql
        return [
            dict(r)
            for r in self.rows
            if r["deliver_at"] <= args[0] and (r["status"] == "pending" or not guarded)
        ]

    async def execute(self, sql: str, *args: Any) -> str:
        self.statements.append(sql)
        guarded = "AND status = 'pending'" in sql or "WHERE status = 'pending'" in sql
        if "SET status = 'expired'" in sql:
            targets = [r for r in self.rows if r["deliver_at"] < args[0]]
            new = {"status": "expired"}
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


async def test_a_row_reserved_after_the_due_fetch_keeps_its_protocol_status() -> None:
    row = _row("pending", age=timedelta(minutes=5), wake=True)
    pool = _FlushPool([row])

    async def reserve_while_sending(_envelope: dict) -> None:
        row["status"] = "release_prepared"

    await _flush(pool, AsyncMock(side_effect=reserve_while_sending))

    assert row["status"] == "release_prepared" and row["delivered_at"] is None


async def test_a_composed_group_keeps_a_member_reserved_after_the_due_fetch() -> None:
    first = _row("pending", age=timedelta(minutes=5), wake=True)
    second = _row("pending", age=timedelta(minutes=6), wake=True)
    pool = _FlushPool([first, second])

    async def reserve_while_sending(_envelope: dict) -> None:
        second["status"] = "release_prepared"

    await _flush(pool, AsyncMock(side_effect=reserve_while_sending))

    assert first["status"] == "delivered"
    assert second["status"] == "release_prepared" and second["delivered_at"] is None

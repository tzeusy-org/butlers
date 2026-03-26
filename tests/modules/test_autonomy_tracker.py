"""Tests for the autonomy tracker — fingerprinting, approval history, velocity.

Covers:
- compute_fingerprint: determinism, key-order independence, tool_name inclusion
- record_approval: DB insertion with correct fields
- get_approval_count: zero for new fingerprint, correct count
- check_promotion_threshold: creates suggestion at threshold, prevents duplicates
- update_velocity / get_velocity: rolling avg and fast_approval flag
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.modules.approvals.autonomy_tracker import (
    compute_fingerprint,
    get_approval_count,
    get_velocity,
    record_approval,
    update_velocity,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fingerprint tests (task 2.1, 2.2)
# ---------------------------------------------------------------------------


def test_fingerprint_determinism():
    """Same tool and args always produce the same fingerprint."""
    fp1 = compute_fingerprint("send_telegram", {"chat_id": "mom_123", "text": "hello"})
    fp2 = compute_fingerprint("send_telegram", {"chat_id": "mom_123", "text": "hello"})
    assert fp1 == fp2


def test_fingerprint_different_args_produce_different_hashes():
    """Different arg values produce different fingerprints."""
    fp1 = compute_fingerprint("send_telegram", {"chat_id": "mom_123", "text": "hello"})
    fp2 = compute_fingerprint("send_telegram", {"chat_id": "dad_456", "text": "hello"})
    assert fp1 != fp2


def test_fingerprint_key_order_independence():
    """Arg key order does not affect fingerprint."""
    fp1 = compute_fingerprint("notify", {"channel": "email", "to": "a@b.com"})
    fp2 = compute_fingerprint("notify", {"to": "a@b.com", "channel": "email"})
    assert fp1 == fp2


def test_fingerprint_tool_name_is_part_of_hash():
    """Different tool names produce different fingerprints even with same args."""
    fp1 = compute_fingerprint("send_telegram", {"to": "mom"})
    fp2 = compute_fingerprint("send_email", {"to": "mom"})
    assert fp1 != fp2


def test_fingerprint_is_sha256_hex():
    """Fingerprint is a 64-char lowercase hex string."""
    fp = compute_fingerprint("my_tool", {"key": "value"})
    assert len(fp) == 64
    assert fp == fp.lower()
    int(fp, 16)  # raises ValueError if not valid hex


# ---------------------------------------------------------------------------
# Mock helper for DB
# ---------------------------------------------------------------------------


class MockPool:
    """Minimal asyncpg pool mock for autonomy tracker tests."""

    def __init__(self) -> None:
        self.history_rows: list[dict[str, Any]] = []
        self.suggestion_rows: list[dict[str, Any]] = []
        self.rule_rows: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {}

    async def execute(self, query: str, *args: Any) -> None:
        if "INSERT INTO autonomy_approval_history" in query:
            self.history_rows.append(
                {
                    "id": args[0],
                    "pattern_fingerprint": args[1],
                    "tool_name": args[2],
                    "tool_args": args[3],
                    "action_id": args[4],
                    "approved_at": args[5],
                    "time_to_decision_seconds": args[6],
                }
            )
        elif "INSERT INTO autonomy_suggestions" in query:
            row: dict[str, Any] = {
                "id": args[0],
                "suggestion_type": args[1],
                "pattern_fingerprint": args[2],
                "tool_name": args[3],
                "representative_args": args[4],
                "status": args[5],
                "approval_count_at_creation": args[6],
                "created_at": args[7],
                "resulting_rule_id": args[8] if len(args) > 8 else None,
                "decided_at": None,
                "decided_by": None,
                "cooldown_until": None,
                "dismissal_reason": None,
            }
            self.suggestion_rows.append(row)
        elif "INSERT INTO approval_events" in query:
            self.events.append({"event_type": args[0]})
        elif "UPDATE state" in query or "INSERT INTO state" in query:
            pass

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "SELECT COUNT" in query and "autonomy_approval_history" in query:
            fp = args[0]
            count = sum(1 for r in self.history_rows if r["pattern_fingerprint"] == fp)
            return {"cnt": count}

        if "SELECT id FROM approval_rules" in query:
            tool = args[0]
            for r in self.rule_rows:
                if r.get("tool_name") == tool and r.get("active", True):
                    return {"id": r["id"]}
            return None

        if "autonomy_suggestions" in query and "pattern_fingerprint" in query:
            fp = args[0]
            matches = [
                r
                for r in self.suggestion_rows
                if r["pattern_fingerprint"] == fp and r.get("suggestion_type") == "promotion"
            ]
            if matches:
                return dict(matches[-1])
            return None

        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "approval_rules" in query and "arg_constraints" in query:
            tool = args[0]
            return [dict(r) for r in self.rule_rows if r.get("tool_name") == tool]
        if "autonomy_approval_history" in query and "time_to_decision_seconds" in query:
            fp = args[0]
            limit = args[1] if len(args) > 1 else 10
            rows = [
                r
                for r in reversed(self.history_rows)
                if r["pattern_fingerprint"] == fp and r["time_to_decision_seconds"] is not None
            ]
            return [
                {"time_to_decision_seconds": r["time_to_decision_seconds"]} for r in rows[:limit]
            ]
        return []

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "SELECT value FROM state" in query:
            key = args[0]
            val = self.state.get(key)
            return json.dumps(val) if val is not None else None
        if "INSERT INTO state" in query or "ON CONFLICT" in query:
            key = args[0]
            val = json.loads(args[1])
            self.state[key] = val
            return 1
        return None


class MockAction:
    """Minimal PendingAction-like mock."""

    def __init__(
        self,
        tool_name: str = "test_tool",
        tool_args: dict[str, Any] | None = None,
        requested_at: datetime | None = None,
        decided_at: datetime | None = None,
    ) -> None:
        self.id = uuid.uuid4()
        self.tool_name = tool_name
        self.tool_args = tool_args or {}
        self.requested_at = requested_at or datetime.now(UTC)
        self.decided_at = decided_at or datetime.now(UTC)


# ---------------------------------------------------------------------------
# record_approval tests (task 3.1, 3.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_approval_inserts_history_row():
    """record_approval inserts a row in autonomy_approval_history."""
    pool = MockPool()
    action = MockAction(
        tool_name="send_telegram",
        tool_args={"chat_id": "mom_123", "text": "hello"},
        requested_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        decided_at=datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC),
    )

    await record_approval(pool, action)

    assert len(pool.history_rows) == 1
    row = pool.history_rows[0]
    fp = compute_fingerprint("send_telegram", {"chat_id": "mom_123", "text": "hello"})
    assert row["pattern_fingerprint"] == fp
    assert row["tool_name"] == "send_telegram"
    assert row["action_id"] == action.id
    assert row["time_to_decision_seconds"] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_record_approval_time_to_decision():
    """time_to_decision_seconds is decided_at minus requested_at."""
    pool = MockPool()
    requested = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    decided = datetime(2026, 1, 1, 10, 0, 45, tzinfo=UTC)
    action = MockAction(requested_at=requested, decided_at=decided)

    await record_approval(pool, action)

    assert pool.history_rows[0]["time_to_decision_seconds"] == pytest.approx(45.0)


# ---------------------------------------------------------------------------
# get_approval_count tests (task 3.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_approval_count_zero_for_new_fingerprint():
    """Returns 0 for a fingerprint with no history."""
    pool = MockPool()
    count = await get_approval_count(pool, "nonexistent_fingerprint")
    assert count == 0


@pytest.mark.asyncio
async def test_get_approval_count_returns_correct_count():
    """Returns the exact count of history rows for a fingerprint."""
    pool = MockPool()
    fp = compute_fingerprint("notify", {"to": "mom@example.com"})

    # Manually add 3 history rows
    for _ in range(3):
        action = MockAction(tool_name="notify", tool_args={"to": "mom@example.com"})
        await record_approval(pool, action)

    count = await get_approval_count(pool, fp)
    assert count == 3


# ---------------------------------------------------------------------------
# check_promotion_threshold tests (task 3.3, 3.5)
# ---------------------------------------------------------------------------


class _ThresholdConfig:
    promotion_threshold = 5
    suggestion_cooldown_days = 30


@pytest.mark.asyncio
async def test_check_promotion_threshold_creates_suggestion_at_threshold():
    """Creates a suggestion when approval count reaches the threshold."""
    pool = MockPool()
    fp = compute_fingerprint("send_telegram", {"chat_id": "mom"})

    # Seed history to exactly the threshold
    for _ in range(5):
        pool.history_rows.append(
            {
                "pattern_fingerprint": fp,
                "tool_name": "send_telegram",
                "time_to_decision_seconds": 10.0,
            }
        )

    from butlers.modules.approvals.autonomy_tracker import check_promotion_threshold

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        await check_promotion_threshold(
            pool=pool,
            pattern_fingerprint=fp,
            tool_name="send_telegram",
            tool_args={"chat_id": "mom"},
            config=_ThresholdConfig(),
        )

    assert len(pool.suggestion_rows) == 1
    assert pool.suggestion_rows[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_check_promotion_threshold_below_threshold_no_suggestion():
    """No suggestion is created when count is below the threshold."""
    pool = MockPool()
    fp = compute_fingerprint("send_telegram", {"chat_id": "mom"})

    for _ in range(3):  # below threshold of 5
        pool.history_rows.append(
            {
                "pattern_fingerprint": fp,
                "tool_name": "send_telegram",
                "time_to_decision_seconds": 10.0,
            }
        )

    from butlers.modules.approvals.autonomy_tracker import check_promotion_threshold

    await check_promotion_threshold(
        pool=pool,
        pattern_fingerprint=fp,
        tool_name="send_telegram",
        tool_args={"chat_id": "mom"},
        config=_ThresholdConfig(),
    )

    assert len(pool.suggestion_rows) == 0


@pytest.mark.asyncio
async def test_check_promotion_threshold_no_duplicate_for_pending_suggestion():
    """No duplicate suggestion is created when a pending one already exists."""
    pool = MockPool()
    fp = compute_fingerprint("send_telegram", {"chat_id": "mom"})

    for _ in range(5):
        pool.history_rows.append({"pattern_fingerprint": fp, "time_to_decision_seconds": 10.0})

    # Pre-seed a pending suggestion
    pool.suggestion_rows.append(
        {
            "id": uuid.uuid4(),
            "pattern_fingerprint": fp,
            "status": "pending",
            "suggestion_type": "promotion",
            "cooldown_until": None,
        }
    )

    from butlers.modules.approvals.autonomy_tracker import check_promotion_threshold

    await check_promotion_threshold(
        pool=pool,
        pattern_fingerprint=fp,
        tool_name="send_telegram",
        tool_args={"chat_id": "mom"},
        config=_ThresholdConfig(),
    )

    # Still only 1 suggestion (the pre-seeded one)
    assert len(pool.suggestion_rows) == 1


@pytest.mark.asyncio
async def test_check_promotion_threshold_respects_cooldown():
    """No suggestion created when dismissed suggestion is within cooldown."""
    pool = MockPool()
    fp = compute_fingerprint("send_telegram", {"chat_id": "mom"})

    for _ in range(5):
        pool.history_rows.append({"pattern_fingerprint": fp, "time_to_decision_seconds": 10.0})

    # Pre-seed a dismissed suggestion with active cooldown
    pool.suggestion_rows.append(
        {
            "id": uuid.uuid4(),
            "pattern_fingerprint": fp,
            "status": "dismissed",
            "suggestion_type": "promotion",
            "cooldown_until": datetime.now(UTC) + timedelta(days=15),  # still in cooldown
        }
    )

    from butlers.modules.approvals.autonomy_tracker import check_promotion_threshold

    await check_promotion_threshold(
        pool=pool,
        pattern_fingerprint=fp,
        tool_name="send_telegram",
        tool_args={"chat_id": "mom"},
        config=_ThresholdConfig(),
    )

    assert len(pool.suggestion_rows) == 1  # no new suggestion


# ---------------------------------------------------------------------------
# update_velocity / get_velocity tests (task 4.1–4.3)
# ---------------------------------------------------------------------------


class _VelocityConfig:
    velocity_window = 3


@pytest.mark.asyncio
async def test_update_velocity_stores_avg_seconds():
    """Velocity is stored with correct avg_seconds after update."""
    pool = MockPool()
    fp = compute_fingerprint("notify", {"to": "mom@example.com"})

    # Seed history
    now = datetime.now(UTC)
    pool.history_rows = [
        {"pattern_fingerprint": fp, "time_to_decision_seconds": 10.0, "approved_at": now},
        {"pattern_fingerprint": fp, "time_to_decision_seconds": 20.0, "approved_at": now},
        {"pattern_fingerprint": fp, "time_to_decision_seconds": 30.0, "approved_at": now},
    ]

    await update_velocity(pool, pool, fp, _VelocityConfig())

    velocity = await get_velocity(pool, fp)
    assert velocity is not None
    assert velocity["avg_seconds"] == pytest.approx(20.0)
    assert velocity["sample_count"] == 3
    assert velocity["fast_approval"] is False


@pytest.mark.asyncio
async def test_update_velocity_fast_approval_flag():
    """fast_approval is True when avg_seconds < 5."""
    pool = MockPool()
    fp = compute_fingerprint("quick_tool", {})

    now = datetime.now(UTC)
    pool.history_rows = [
        {"pattern_fingerprint": fp, "time_to_decision_seconds": 2.0, "approved_at": now},
        {"pattern_fingerprint": fp, "time_to_decision_seconds": 3.0, "approved_at": now},
    ]

    await update_velocity(pool, pool, fp, _VelocityConfig())

    velocity = await get_velocity(pool, fp)
    assert velocity["fast_approval"] is True
    assert velocity["avg_seconds"] == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_get_velocity_returns_none_for_unknown_fingerprint():
    """get_velocity returns None when no data is stored."""
    pool = MockPool()
    result = await get_velocity(pool, "unknown_fp")
    assert result is None

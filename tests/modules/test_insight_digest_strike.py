"""No-Docker tests for in-place strikes of resolved lines in delivered digests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.tools.switchboard.insight import broker

pytestmark = pytest.mark.unit

_DIGEST = (
    "Daily Insights (3):\n"
    "1. [Finance] Bill is overdue\n"
    "2. [Finance] Bill is overdue soon\n"
    "3. [Health] Refill due"
)
_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _ref(line: str, text: str = _DIGEST, index: int = 1) -> dict:
    return {
        "channel": "telegram",
        "standalone": False,
        "digest_line_index": index,
        "chat_id": "123",
        "provider_message_id": 77,
        "line_text": line,
        "delivery_text": text,
    }


def _row(rid: str, line: str, text: str = _DIGEST) -> dict:
    return {
        "id": rid,
        "attempts": 0,
        "candidate_id": f"c-{rid}",
        "message": line,
        "origin_butler": "finance",
        "priority": 80,
        "dedup_key": rid,
        "premise": None,
        "delivery_ref": json.dumps(_ref(line, text)),
    }


def _pool(applied: list[dict] | None = None) -> MagicMock:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=applied or [])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


def test_a_prefix_line_strikes_only_its_own_whole_row():
    text, anchored = broker.strike_digest_lines(_DIGEST, [("Bill is overdue", "Resolved")])
    assert anchored == [True]
    assert "1. [Finance] ~~Bill is overdue~~ Resolved\n2. [Finance] Bill is overdue soon" in text


def test_a_duplicated_line_is_not_anchored():
    dup = "1. [A] Same\n2. [B] Same"
    text, anchored = broker.strike_digest_lines(dup, [("Same", "Resolved")])
    assert (text, anchored) == (dup, [False])


async def test_a_duplicate_line_folds_instead_of_editing():
    dup = "Daily Insights (2):\n1. [A] Same\n2. [B] Same"
    amend = AsyncMock(return_value={"status": "edited"})
    counts = {"applied": 0, "folded": 0, "retry": 0}
    await broker._settle_amendment_unit(
        _pool(), amend, _ref("Same", dup), [_row("a", "Same", dup)], _NOW, counts
    )
    amend.assert_not_awaited()
    assert counts == {"applied": 0, "folded": 1, "retry": 0}


async def test_an_oversized_digest_folds_instead_of_a_truncated_edit():
    big = "Daily Insights (2):\n1. [A] Line one\n2. [A] " + "x" * 4100
    amend = AsyncMock(return_value={"status": "edited"})
    counts = {"applied": 0, "folded": 0, "retry": 0}
    await broker._settle_amendment_unit(
        _pool(), amend, _ref("Line one", big), [_row("a", "Line one", big)], _NOW, counts
    )
    amend.assert_not_awaited()
    assert counts["folded"] == 1 and counts["applied"] == 0


async def test_an_earlier_applied_strike_survives_a_later_edit():
    earlier = {
        "summary": "Resolved 10:00 UTC",
        "delivery_ref": json.dumps(_ref("Refill due", index=3)),
    }
    amend = AsyncMock(return_value={"status": "edited"})
    counts = {"applied": 0, "folded": 0, "retry": 0}
    pool = _pool([earlier])
    pool.fetchrow = AsyncMock(return_value=None)
    await broker._settle_amendment_unit(
        pool, amend, _ref("Bill is overdue"), [_row("a", "Bill is overdue")], _NOW, counts
    )
    sent = amend.await_args.args[1]
    assert "~~Refill due~~ Resolved 10:00 UTC" in sent
    assert "~~Bill is overdue~~ Resolved" in sent
    assert "~~Bill is overdue soon~~" not in sent
    assert counts["applied"] == 1

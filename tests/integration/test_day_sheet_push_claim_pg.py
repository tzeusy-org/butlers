# Spec: REQ-butler-general-010
"""The per-date delivery claim over a real state table (bu-5ii4gj.2 S3): one winner, TTL takeover."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, date, datetime

import pytest

from butlers.jobs import day_sheet_push as job

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

DAY = date(2026, 11, 24)
NOW = datetime(2026, 11, 23, 14, 40, tzinfo=UTC)


async def test_concurrent_claims_have_exactly_one_winner(db) -> None:
    pool = await db("core")
    wins = await asyncio.gather(*(job._claim(pool, DAY, NOW) for _ in range(8)))
    assert sum(1 for w in wins if w) == 1


async def test_a_released_claim_can_be_taken_again(db) -> None:
    pool = await db("core")
    token = await job._claim(pool, DAY, NOW)
    assert token
    assert not await job._claim(pool, DAY, NOW)
    await job._release(pool, DAY, token)
    assert await job._claim(pool, DAY, NOW)


async def test_only_the_holder_can_release(db) -> None:
    pool = await db("core")
    token = await job._claim(pool, DAY, NOW)
    assert token
    await job._release(pool, DAY, "someone-else")
    assert not await job._claim(pool, DAY, NOW)  # still held
    await job._release(pool, DAY, token)
    assert await job._claim(pool, DAY, NOW)


async def test_an_expired_claim_is_taken_over(db) -> None:
    pool = await db("core")
    assert await job._claim(pool, DAY, NOW)
    await pool.execute(
        "UPDATE state SET updated_at = now() - interval '11 minutes' WHERE key = $1",
        f"{job.CLAIM_PREFIX}{DAY.isoformat()}",
    )
    assert await job._claim(pool, DAY, NOW)


async def test_a_delivered_marker_ends_the_date(db) -> None:
    pool = await db("core")
    assert not await job._date_delivered(pool, DAY, NOW)
    await job._write_markers(pool, DAY, {"date": "2026-11-24", "outcome": "delivered"})
    assert await job._date_delivered(pool, DAY, NOW)

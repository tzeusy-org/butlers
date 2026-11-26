"""Real-Postgres: an owner-clock transition re-anchors only owner-local schedules (bu-5ii4gj.5 P5)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.core.scheduler.timing import _next_run
from butlers.perception import clock_reanchor as ra
from butlers.perception import flags
from butlers.perception.clock import ClockValue

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

HOME = ClockValue("Asia/Singapore", "home")
TOKYO = ClockValue("Asia/Tokyo", "owner_confirmed_booking")
NOW = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
AGO = NOW - timedelta(hours=1)


@pytest.fixture
async def pool(db):
    return await db("core", schemas={"core": "general"}, schema="general")


async def _seed(pool, name, *, cron="0 9 * * *", tz="UTC", task_type="cron"):
    nxt = _next_run(cron, timezone=HOME.zone, now=NOW)
    await pool.execute(
        "INSERT INTO scheduled_tasks (name, cron, prompt, dispatch_mode, timezone, task_type, "
        "next_run_at, enabled) VALUES ($1, $2, 'p', 'prompt', $3, $4, $5, true)",
        name,
        cron,
        tz,
        task_type,
        nxt,
    )
    return nxt


async def _next(pool, name):
    return await pool.fetchval("SELECT next_run_at FROM scheduled_tasks WHERE name = $1", name)


async def _run(pool, **kw):
    args = {"old_clock": HOME, "new_clock": TOKYO, "transition_at": AGO, "now": NOW}
    args.update(kw)
    return await ra.run_clock_transition({"general": pool}, **args)


async def test_only_owner_local_rows_move(pool, monkeypatch) -> None:
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    local_before = await _seed(pool, "owner_local")
    pinned_before = await _seed(pool, "pinned", tz="Europe/London")

    summary = await _run(pool)

    assert summary["moved"] == 1 and summary["kept_pinned"] == 1 and summary["failed"] == []
    moved = await _next(pool, "owner_local")
    assert moved != local_before
    assert moved.astimezone(ZoneInfo("Asia/Tokyo")).hour == 9
    assert await _next(pool, "pinned") == pinned_before


async def test_a_claim_that_wins_the_race_is_not_overwritten(pool, monkeypatch) -> None:
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    old = await _seed(pool, "raced")
    rows = [dict(r) for r in await pool.fetch(ra._ROWS_SQL)]
    plan = ra.plan_reanchor(
        rows, old_zone=HOME.zone, new_zone=TOKYO.zone, transition_at=AGO, now=NOW
    )
    assert len(plan.moves) == 1
    # The dispatcher claims the occurrence between the read and the write.
    claimed = old + timedelta(days=1)
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $1 WHERE name = 'raced'", claimed)

    assert await ra.apply_reanchor(pool, plan) == 0
    assert await _next(pool, "raced") == claimed


async def test_dark_until_the_owner_clock_is_live_and_unknown_moves_nothing(
    pool, monkeypatch
) -> None:
    before = await _seed(pool, "idle")
    assert await _run(pool) == {"live": False}
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    summary = await _run(pool, new_clock=None)
    assert summary["moved"] == 0 and summary["reason"] == "clock_unknown"
    assert await _next(pool, "idle") == before

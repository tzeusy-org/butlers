"""Real-Postgres: an owner-clock transition re-anchors only owner-local schedules (bu-5ii4gj.5 P5)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.core.scheduler.dispatch import tick
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
NOW = datetime.now(UTC)  # real clock: the dispatcher in the fire-once test uses it too
AGO = NOW - timedelta(hours=1)


async def _general(db):
    """The general butler's schema on the shared test database (the factory, not a fixture)."""
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
    args = {
        "old_clock": HOME,
        "new_clock": TOKYO,
        "settings_zone": HOME.zone,
        "transition_at": AGO,
        "now": NOW,
    }
    args.update(kw)
    return await ra.run_clock_transition({"general": pool}, **args)


async def test_only_owner_local_rows_move(db, monkeypatch) -> None:
    pool = await _general(db)
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    local_before = await _seed(pool, "owner_local")
    pinned_before = await _seed(pool, "pinned", tz="Europe/London")

    summary = await _run(pool)

    assert summary["complete"] and summary["failed"] == []
    assert summary["per_schema"]["general"] == {"moved": 1, "kept_pinned": 1, "skipped": 0}
    moved = await _next(pool, "owner_local")
    assert moved != local_before
    assert moved.astimezone(ZoneInfo("Asia/Tokyo")).hour == 9
    assert await _next(pool, "pinned") == pinned_before


async def test_a_claim_that_wins_the_race_is_not_overwritten(db, monkeypatch) -> None:
    pool = await _general(db)
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
    db, monkeypatch
) -> None:
    pool = await _general(db)
    before = await _seed(pool, "idle")
    assert await _run(pool) == {"live": False}
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    summary = await _run(pool, new_clock=None)
    assert summary["per_schema"]["general"]["moved"] == 0 and summary["reason"] == "clock_unknown"
    assert await _next(pool, "idle") == before


async def _fire_once(pool, name, **tick_kwargs) -> datetime:
    """Make the occurrence due, tick once, and return the recomputed ``next_run_at``."""
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = now() - interval '1 second' WHERE name = $1",
        name,
    )
    fired = []

    async def dispatch(**kwargs):
        fired.append(kwargs)

    await tick(pool, dispatch, default_timezone=HOME.zone, **tick_kwargs)
    assert fired, "the due occurrence should have fired"
    return await _next(pool, name)


def _hour(value: datetime, zone: str) -> int:
    return value.astimezone(ZoneInfo(zone)).hour


async def test_a_reanchored_row_stays_on_the_travel_zone_after_it_fires(db, monkeypatch) -> None:
    pool = await _general(db)
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    real_now = NOW
    await _seed(pool, "brief")
    await _run(pool, now=real_now, transition_at=real_now - timedelta(hours=1))
    assert _hour(await _next(pool, "brief"), TOKYO.zone) == 9

    async def read_clock():
        return TOKYO

    after = await _fire_once(pool, "brief", owner_zone_resolver=ra.owner_zone_resolver(read_clock))

    assert _hour(after, TOKYO.zone) == 9  # still 09:00 Tokyo after one firing


async def test_without_the_shared_resolver_the_row_would_snap_back_home(db, monkeypatch) -> None:
    pool = await _general(db)
    # The control: the dispatcher's own default is the settings zone, which is why the resolver exists.
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    real_now = NOW
    await _seed(pool, "brief")
    await _run(pool, now=real_now, transition_at=real_now - timedelta(hours=1))

    after = await _fire_once(pool, "brief")

    assert _hour(after, HOME.zone) == 9 and _hour(after, TOKYO.zone) != 9


async def test_an_unreadable_or_missing_schema_makes_the_run_incomplete(db, monkeypatch) -> None:
    pool = await _general(db)
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    await _seed(pool, "present")

    summary = await _run(pool, expected=["general", "travel"])  # no pool for travel

    assert summary["complete"] is False and summary["failed"] == ["travel"]
    assert summary["per_schema"]["general"]["moved"] == 1  # readable schemas still move, per schema


async def test_the_resolver_ignores_home_and_unknown_clocks(monkeypatch) -> None:
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)

    async def home():
        return HOME

    async def unknown():
        return None

    assert await ra.owner_zone_resolver(home)("Asia/Singapore") == "Asia/Singapore"
    assert await ra.owner_zone_resolver(unknown)("Asia/Singapore") == "Asia/Singapore"
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", False)

    async def tokyo():
        return TOKYO

    assert await ra.owner_zone_resolver(tokyo)("Asia/Singapore") == "Asia/Singapore"  # dark


async def test_a_utc_fallback_clock_moves_nothing(db, monkeypatch) -> None:
    pool = await _general(db)
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    before = await _seed(pool, "stays")

    summary = await _run(pool, new_clock=ClockValue("UTC", "utc_fallback"))

    assert summary["reason"] == "same_zone"
    assert await _next(pool, "stays") == before


async def test_a_return_home_re_anchors_back_through_the_same_rule(db, monkeypatch) -> None:
    pool = await _general(db)
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    await _seed(pool, "brief")
    await _run(pool)  # out to Tokyo
    assert _hour(await _next(pool, "brief"), TOKYO.zone) == 9

    await _run(pool, old_clock=TOKYO, new_clock=HOME)  # the clock is home again

    assert _hour(await _next(pool, "brief"), HOME.zone) == 9


async def test_one_unreadable_schema_is_reported_and_the_others_still_move(db, monkeypatch) -> None:
    pool = await _general(db)
    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    await _seed(pool, "fine")

    class Broken:
        async def fetch(self, *_a, **_k):
            raise OSError("schema unreadable")

    summary = await ra.run_clock_transition(
        {"general": pool, "travel": Broken()},
        old_clock=HOME,
        new_clock=TOKYO,
        settings_zone=HOME.zone,
        transition_at=AGO,
        now=NOW,
    )

    assert summary["complete"] is False and summary["failed"] == ["travel"]
    assert summary["per_schema"]["general"]["moved"] == 1


async def test_a_hung_owner_zone_read_does_not_hold_the_tick(db, monkeypatch) -> None:
    import asyncio

    from butlers.core.scheduler import dispatch

    pool = await _general(db)
    await _seed(pool, "brief")
    monkeypatch.setattr(dispatch, "_OWNER_ZONE_TIMEOUT_S", 0.05)

    async def hang(_zone: str) -> str:
        await asyncio.sleep(3600)
        return "Asia/Tokyo"

    after = await _fire_once(pool, "brief", owner_zone_resolver=hang)

    assert _hour(after, HOME.zone) == 9  # the default zone, not a hung or half-read answer

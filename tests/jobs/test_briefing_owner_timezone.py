"""Health briefing day window follows the owner's configured timezone (bu-dyb9ca).

The Health contribution job used to hard-code UTC+8 for the dose day window and the
missed / due-later split.  It now reads the shared ``settings.general`` timezone and
falls back to SGT only when the owner never saved one, the settings cannot be read, or
the stored name is not a valid zone.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from butlers.jobs.briefing import owner_briefing_zone, run_health_briefing_contribution

SGT = timezone(timedelta(hours=8))
# 2026-03-25 13:55 UTC is 06:55 in Los Angeles (PDT) and 21:55 in Singapore.
_INSTANT = datetime(2026, 3, 25, 13, 55, tzinfo=UTC)
_DAY = date(2026, 3, 25)


def _pool(stored, rows=()):
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=stored)
    pool.fetch = AsyncMock(side_effect=[list(rows), [{"cnt": 0}]])
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


async def _run(pool, instant=_INSTANT):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant

    write = AsyncMock()
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DAY),
        patch("butlers.jobs.briefing.datetime", _Frozen),
        patch("butlers.jobs.briefing._write_contribution", write),
    ):
        result = await run_health_briefing_contribution(pool, None)
    return result, write.call_args[0][1]


def _med(name, frequency, schedule):
    return {"name": name, "frequency": frequency, "schedule": schedule, "doses_taken": 0}


async def test_day_window_is_owner_local_midnight_to_midnight():
    pool = _pool({"timezone": "America/Los_Angeles"})

    await _run(pool)

    start, end = pool.fetch.call_args_list[0][0][1:3]
    assert start == datetime(2026, 3, 25, 7, 0, tzinfo=UTC)  # 00:00 PDT
    assert end == datetime(2026, 3, 26, 7, 0, tzinfo=UTC)
    # The taken-doses query uses the same window.
    assert pool.fetch.call_args_list[1][0][1:3] == (start, end)


async def test_a_dose_just_before_owner_midnight_is_yesterday_and_just_after_is_today():
    pool = _pool({"timezone": "America/Los_Angeles"})

    await _run(pool)

    start, end = pool.fetch.call_args_list[0][0][1:3]
    just_before = datetime(2026, 3, 25, 6, 59, 59, tzinfo=UTC)  # 23:59:59 PDT on the 24th
    just_after = datetime(2026, 3, 25, 7, 0, 1, tzinfo=UTC)  # 00:00:01 PDT on the 25th
    assert not (start <= just_before < end)
    assert start <= just_after < end
    # The same instants straddle SGT's boundary elsewhere: the SGT window would be wrong.
    sgt_start = datetime(2026, 3, 24, 16, 0, tzinfo=UTC)
    assert start != sgt_start


async def test_missed_and_due_later_split_uses_owner_local_time():
    pool = _pool(
        {"timezone": "America/Los_Angeles"},
        rows=[_med("Morning pill", "daily", ["06:00"]), _med("Evening pill", "daily", ["20:00"])],
    )

    result, envelope = await _run(pool)

    # 06:55 PDT: the 06:00 dose is overdue, the 20:00 dose is still ahead.  Read in SGT
    # (21:55) the evening dose would wrongly be reported missed.
    assert result["missed_doses"] == 1
    texts = {h["text"]: h["priority"] for h in envelope["highlights"]}
    assert texts == {
        "Missed dose(s) today: Morning pill": "high",
        "Doses due later today: Evening pill": "low",
    }


@pytest.mark.parametrize(
    "stored",
    [None, {}, {"timezone": ""}, {"timezone": "Not/AZone"}, "garbage"],
    ids=["unset", "empty-settings", "blank", "invalid-zone", "wrong-shape"],
)
async def test_falls_back_to_sgt_when_no_usable_owner_timezone(stored):
    pool = _pool(stored)

    await _run(pool)

    start, end = pool.fetch.call_args_list[0][0][1:3]
    assert start == datetime(2026, 3, 24, 16, 0, tzinfo=UTC)  # 2026-03-25 00:00 SGT
    assert end == datetime(2026, 3, 25, 16, 0, tzinfo=UTC)


async def test_falls_back_to_sgt_when_the_settings_read_fails():
    pool = _pool(None)
    pool.fetchval = AsyncMock(side_effect=RuntimeError("settings unavailable"))

    zone = await owner_briefing_zone(pool)

    assert zone is SGT or zone.utcoffset(None) == timedelta(hours=8)


async def test_owner_zone_is_the_stored_iana_zone():
    pool = _pool({"timezone": "Europe/Berlin"})

    zone = await owner_briefing_zone(pool)

    assert isinstance(zone, ZoneInfo)
    assert zone.key == "Europe/Berlin"


@pytest.mark.parametrize(
    ("instant", "start", "end", "hours"),
    [
        # US spring forward: 02:00 PST jumps to 03:00 PDT, so the local day is 23 hours.
        (
            datetime(2026, 3, 8, 20, 0, tzinfo=UTC),
            datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
            datetime(2026, 3, 9, 7, 0, tzinfo=UTC),
            23,
        ),
        # US fall back: the local day is 25 hours.
        (
            datetime(2026, 11, 1, 20, 0, tzinfo=UTC),
            datetime(2026, 11, 1, 7, 0, tzinfo=UTC),
            datetime(2026, 11, 2, 8, 0, tzinfo=UTC),
            25,
        ),
    ],
    ids=["spring-forward-23h", "fall-back-25h"],
)
async def test_dst_transition_day_window_is_the_actual_local_day(instant, start, end, hours):
    pool = _pool({"timezone": "America/Los_Angeles"})

    await _run(pool, instant)

    got_start, got_end = pool.fetch.call_args_list[0][0][1:3]
    assert (got_start, got_end) == (start, end)
    assert got_end - got_start == timedelta(hours=hours)


docker_available = shutil.which("docker") is not None


async def test_owner_zone_read_is_schema_qualified_to_public_state():
    """A revert to the unqualified ``state`` read fails here, without Docker."""
    pool = _pool({"timezone": "Europe/Berlin"})

    await owner_briefing_zone(pool)

    sql = pool.fetchval.call_args[0][0]
    assert "public.state" in sql


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
async def test_owner_zone_comes_from_public_state_not_the_butlers_own_state_table(
    provisioned_postgres_pool,
) -> None:
    """The job's pool resolves an unqualified ``state`` to ``health.state``.

    Both tables hold a ``settings.general`` row with a different timezone; only the
    shared ``public.state`` row is the owner's setting.
    """
    create = (
        "CREATE TABLE IF NOT EXISTS {}.state (key TEXT PRIMARY KEY, value JSONB NOT NULL, "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), version INTEGER NOT NULL DEFAULT 1)"
    )
    async with provisioned_postgres_pool(schema="health") as pool:
        await pool.execute("CREATE SCHEMA IF NOT EXISTS health")
        await pool.execute(create.format("public"))
        await pool.execute(create.format("health"))
        await pool.execute(
            "INSERT INTO public.state (key, value) VALUES ('settings.general', $1)",
            {"timezone": "Europe/Berlin"},
        )
        await pool.execute(
            "INSERT INTO health.state (key, value) VALUES ('settings.general', $1)",
            {"timezone": "America/Los_Angeles"},
        )

        zone = await owner_briefing_zone(pool)

        assert isinstance(zone, ZoneInfo)
        assert zone.key == "Europe/Berlin"

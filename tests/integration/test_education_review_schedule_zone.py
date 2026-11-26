"""A spaced-repetition review fires at the instant it was scheduled for, on real Postgres.

``spaced_repetition_record_response`` creates a one-shot cron schedule for the next review through
the real core ``schedule_create`` (bu-5ii4gj.5). The schedule's ``next_run_at`` and the cron the
scheduler would re-read in the owner's zone must both name the instant the review was scheduled
for, whatever zone the owner is in.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from butlers.core.scheduler.crud import schedule_create as core_schedule_create
from butlers.core.scheduler.crud import schedule_delete as core_schedule_delete
from butlers.core.scheduler.timing import _effective_schedule_timezone, _next_run
from butlers.tools.education import spaced_repetition as sr

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


def _instants() -> list[datetime]:
    """Review moments derived from the real clock.

    The real ``schedule_create`` anchors croniter at the wall clock, so a fixed past instant would
    return next year's date once real time passes it. Both are comfortably ahead of now:
    - 12:00Z (20:00 in Singapore) at least two days out;
    - 19:00Z on the last day of the month after next: its Singapore day and month are not its UTC
      ones (03:00 on the 1st), and it is always under 12 months out, so a yearless cron anchored
      at the real clock reaches it.
    """
    real = datetime.now(UTC)
    evening = (real + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
    return [evening, _month_end_evening(real)]


def _month_end_evening(real: datetime) -> datetime:
    """19:00Z on the last day of the month after next (always 31 to 92 days ahead)."""
    month = real.month + 2
    year = real.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    first_after = datetime(year + month // 12, month % 12 + 1, 1, 19, 0, tzinfo=UTC)
    return first_after - timedelta(days=1)


INSTANTS = _instants()


async def _as_education_rw(connection: asyncpg.Connection) -> None:
    await connection.execute("SET ROLE butler_education_rw")


@pytest.fixture
async def edu_pool(db):
    kw = {"schemas": {"core": "education", "education": "education"}, "schema": "education"}
    return await db("core", "education", init=_as_education_rw, **kw)


async def _schedule_create(pool: asyncpg.Pool, **kwargs: Any) -> str:
    name = kwargs.pop("name")
    cron = kwargs.pop("cron")
    prompt = kwargs.pop("prompt", None)
    return str(await core_schedule_create(pool, name, cron, prompt, **kwargs))


async def _schedule_delete(pool: asyncpg.Pool, name: str) -> None:
    row = await pool.fetchrow("SELECT id FROM scheduled_tasks WHERE name = $1", name)
    if row is not None:
        await core_schedule_delete(pool, row["id"])


async def _review(
    pool: asyncpg.Pool, monkeypatch, zone: str | None, now: datetime
) -> dict[str, Any]:
    if zone is not None:
        # The owner's saved zone lives in the shared table, which the butler role cannot write.
        async with pool.acquire() as conn:
            await conn.execute("RESET ROLE")
            try:
                await conn.execute(
                    "INSERT INTO public.state (key, value) VALUES ('settings.general', $1::jsonb)"
                    " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    {"timezone": zone},
                )
            finally:
                await conn.execute("SET ROLE butler_education_rw")
    map_id, node_id = str(uuid.uuid4()), str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO education.mind_maps (id, title, status) VALUES ($1, 'Maths', 'draft')",
        map_id,
    )
    await pool.execute(
        "INSERT INTO education.mind_map_nodes (id, mind_map_id, label) VALUES ($1, $2, 'Limits')",
        node_id,
        map_id,
    )
    monkeypatch.setattr(sr, "_now", lambda: now)
    result = await sr.spaced_repetition_record_response(
        pool,
        node_id,
        map_id,
        5,
        schedule_create=lambda **kw: _schedule_create(pool, **kw),
        schedule_delete=lambda name: _schedule_delete(pool, name),
    )
    return result


@pytest.mark.parametrize("now", INSTANTS, ids=["evening", "month-end"])
@pytest.mark.parametrize("zone", ["Asia/Singapore", "Asia/Tokyo", "Europe/London", None])
async def test_the_review_is_due_at_the_instant_it_was_scheduled_for(
    edu_pool, monkeypatch, zone, now
) -> None:
    result = await _review(edu_pool, monkeypatch, zone, now)
    intended = datetime.fromisoformat(result["next_review_at"])
    assert intended == now + timedelta(days=result["interval_days"])

    row = await edu_pool.fetchrow(
        "SELECT cron, timezone, next_run_at, until_at FROM scheduled_tasks WHERE name LIKE 'review-%'"
        " OR name LIKE '%rep%'"
    )
    assert row is not None
    assert row["next_run_at"] == intended

    # The cron, read in the zone the scheduler evaluates it in, names the same instant: the real
    # recompute an hour before it lands on it, and the recompute after the claim lands beyond
    # until_at, so the one-shot auto-disables instead of firing again.
    effective = _effective_schedule_timezone(row["timezone"], zone or "UTC")
    assert _next_run(row["cron"], timezone=effective, now=intended - timedelta(hours=1)) == intended
    assert _next_run(row["cron"], timezone=effective, now=intended) > row["until_at"]


@pytest.mark.parametrize(
    "real",
    [datetime(2026, 12, 30, 19, 1, tzinfo=UTC), datetime(2026, 1, 31, 0, 0, tzinfo=UTC)]
    + [datetime(2026, m, 15, 12, 0, tzinfo=UTC) for m in range(1, 13)],
)
def test_the_month_end_instant_is_always_under_a_year_ahead_and_crosses_the_singapore_month(real):
    instant = _month_end_evening(real)
    assert timedelta(days=28) < instant - real < timedelta(days=365)
    sgt = instant.astimezone(ZoneInfo("Asia/Singapore"))
    assert (sgt.month, sgt.day) != (instant.month, instant.day)

"""The workspace window read selects events by interval overlap, run on real Postgres.

The mocked-pool tests assert the query's text; this runs the production SQL, so a night event
that starts before the window and ends inside it is proven to come back, not just to be spelled
into the predicate (bu-5ii4gj.4.2).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime

import pytest

from butlers.api.read_models.calendar_workspace import query_calendar_workspace

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

# 2026-03-06 is a Friday.
FRI_22 = datetime(2026, 3, 6, 22, tzinfo=UTC)
SAT_02 = datetime(2026, 3, 7, 2, tzinfo=UTC)
SAT = datetime(2026, 3, 7, tzinfo=UTC)
SUN = datetime(2026, 3, 8, tzinfo=UTC)
FRI = datetime(2026, 3, 6, tzinfo=UTC)


class _Db:
    """The dashboard's fan-out, answered by the one test pool."""

    def __init__(self, pool) -> None:
        self.pool = pool

    def butlers_with_module(self, name: str) -> list[str]:
        return ["general"]

    async def fan_out_with_status(self, sql: str, args: tuple, *, butler_names=None):
        return {"general": await self.pool.fetch(sql, *args)}, []


async def _seed(pool, events: dict[str, tuple[datetime, datetime]]) -> None:
    source = await pool.fetchval(
        "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id, "
        "display_name, writable, metadata) VALUES ('provider:google:primary', 'provider_event', "
        "'user', 'google', 'primary', 'Primary', true, '{}') RETURNING id"
    )
    for ref, (start, end) in events.items():
        event_id = await pool.fetchval(
            "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, "
            "ends_at, status, metadata, source_butler) VALUES ($1, $2, $2, 'UTC', $3, $4, "
            "'confirmed', '{}', 'general') RETURNING id",
            source,
            ref,
            start,
            end,
        )
        await pool.execute(
            "INSERT INTO calendar_event_instances (event_id, source_id, origin_instance_ref, "
            "timezone, starts_at, ends_at, status) VALUES ($1, $2, $3, 'UTC', $4, $5, 'confirmed')",
            event_id,
            source,
            ref,
            start,
            end,
        )


async def _titles(pool, start: datetime, end: datetime) -> set[str]:
    rows, failed = await query_calendar_workspace(
        _Db(pool),  # type: ignore[arg-type]
        view="user",
        start=start,
        end=end,
    )
    assert failed == []
    return {r.title for r in rows}


async def test_a_friday_night_event_is_returned_for_a_saturday_window(db):
    pool = await db("core")
    await _seed(
        pool,
        {
            "overnight": (FRI_22, SAT_02),
            "friday-afternoon": (FRI.replace(hour=14), FRI.replace(hour=15)),
            "saturday-noon": (SAT.replace(hour=12), SAT.replace(hour=13)),
            "sunday-morning": (SUN.replace(hour=9), SUN.replace(hour=10)),
        },
    )

    saturday = await _titles(pool, SAT, SUN)

    # Selected by its end, not only its start; an event that ended before the window is not.
    assert saturday == {"overnight", "saturday-noon"}
    assert await _titles(pool, FRI, SAT) == {"overnight", "friday-afternoon"}


async def test_an_event_ending_exactly_at_the_window_start_is_not_returned(db):
    pool = await db("core")
    await _seed(pool, {"ends-at-midnight": (FRI.replace(hour=23), SAT)})

    assert await _titles(pool, SAT, SUN) == set()

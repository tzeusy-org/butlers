"""Real-PostgreSQL coverage for the Health briefing medication/dose queries (bu-c1623g).

The other briefing tests mock the pool and only inspect SQL shape, so nothing proved the
per-medication dose-count subquery or the taken-dose total against the real ``facts`` schema.
This suite runs the job against a database migrated with the real core and memory chains and
asserts the counts the queries return around the owner-local day boundary. It deliberately
asserts counts only, not the missed / due-later classification.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.jobs.briefing import run_health_briefing_contribution
from butlers.migrations import run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_ZONE = ZoneInfo("America/Los_Angeles")
# 2026-03-25 13:55 UTC is 06:55 PDT. The owner-local day is 07:00Z 03-25 -> 07:00Z 03-26.
_INSTANT = datetime(2026, 3, 25, 13, 55, tzinfo=UTC)
_DAY = date(2026, 3, 25)


@pytest.fixture(scope="module")
def migrated_db(postgres_container) -> str:
    """A fresh database with the real core and memory chains applied."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="memory"))
    return db_url


class _RecordingPool:
    """Delegates to a real pool and keeps every ``fetch`` result for inspection."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.fetched: list[list[asyncpg.Record]] = []

    def __getattr__(self, name: str):
        return getattr(self._pool, name)

    async def fetch(self, *args, **kwargs):
        rows = await self._pool.fetch(*args, **kwargs)
        self.fetched.append(rows)
        return rows


# The pool registers the JSONB codec, which json-encodes the Python object itself: pass dicts, not
# pre-dumped strings, or the column holds a JSON string scalar and every ``metadata->>'k'`` is null.
async def _medication(pool: asyncpg.Pool, name: str, *, active: bool = True) -> uuid.UUID:
    fact_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO facts (id, subject, predicate, content, scope, validity, metadata) "
        "VALUES ($1, $2, 'medication', $3, 'health', 'active', $4::jsonb)",
        fact_id,
        f"med-{name.lower()}",
        f"{name} 10mg",
        {"name": name, "frequency": "daily", "schedule": ["08:00"], "active": active},
    )
    return fact_id


async def _dose(
    pool: asyncpg.Pool,
    medication_id: uuid.UUID,
    taken_at: datetime,
    *,
    skipped: bool = False,
    validity: str = "active",
) -> None:
    await pool.execute(
        "INSERT INTO facts (subject, predicate, content, scope, validity, valid_at, metadata) "
        "VALUES ($1, 'took_dose', 'dose', 'health', $2, $3, $4::jsonb)",
        f"dose-{uuid.uuid4().hex[:8]}",
        validity,
        taken_at,
        {"medication_id": str(medication_id), "skipped": skipped},
    )


async def _run(pool: asyncpg.Pool) -> tuple[dict, dict[str, int]]:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return _INSTANT.astimezone(tz) if tz else _INSTANT

    recording = _RecordingPool(pool)
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DAY),
        patch("butlers.jobs.briefing.datetime", _Frozen),
        patch("butlers.jobs.briefing.owner_briefing_zone", AsyncMock(return_value=_ZONE)),
        patch("butlers.jobs.briefing._write_contribution", AsyncMock()),
    ):
        result = await run_health_briefing_contribution(recording, None)
    med_rows = recording.fetched[0]
    return result, {row["name"]: int(row["doses_taken"]) for row in med_rows}


async def _scenario(db_url: str) -> tuple[dict, dict[str, int]]:
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, init=register_jsonb_codec)
    try:
        alpha = await _medication(pool, "Alpha")
        beta = await _medication(pool, "Beta")
        retired = await _medication(pool, "Retired", active=False)

        # Alpha: only the two doses inside the owner-local day count.
        await _dose(pool, alpha, datetime(2026, 3, 25, 6, 59, tzinfo=UTC))  # before local midnight
        await _dose(pool, alpha, datetime(2026, 3, 25, 7, 0, tzinfo=UTC))  # exactly local midnight
        await _dose(pool, alpha, datetime(2026, 3, 25, 13, 0, tzinfo=UTC))  # inside
        await _dose(pool, alpha, datetime(2026, 3, 25, 12, 0, tzinfo=UTC), skipped=True)
        await _dose(pool, alpha, datetime(2026, 3, 25, 12, 30, tzinfo=UTC), validity="retracted")
        await _dose(pool, alpha, datetime(2026, 3, 26, 7, 0, tzinfo=UTC))  # exactly next midnight
        # Beta: one dose; it must not leak into Alpha's count, nor Alpha's into Beta's.
        await _dose(pool, beta, datetime(2026, 3, 25, 10, 0, tzinfo=UTC))
        # An inactive medication is not reported at all.
        await _dose(pool, retired, datetime(2026, 3, 25, 11, 0, tzinfo=UTC))
        return await _run(pool)
    finally:
        await pool.close()


@pytest.fixture(scope="module")
def scenario_result(migrated_db: str) -> tuple[dict, dict[str, int]]:
    """Seed once and run the job once; the facts are entity-less, so a reseed would collide."""
    return asyncio.run(_scenario(migrated_db))


def test_per_medication_dose_counts_respect_the_owner_local_day(scenario_result) -> None:
    _, counts = scenario_result

    assert counts == {"Alpha": 2, "Beta": 1}


def test_taken_total_counts_non_skipped_active_doses_in_the_window(scenario_result) -> None:
    result, _ = scenario_result

    # Alpha 2 + Beta 1 + the inactive medication's dose 1; skipped, retracted and out-of-window
    # doses are excluded.
    assert result["taken_doses"] == 4

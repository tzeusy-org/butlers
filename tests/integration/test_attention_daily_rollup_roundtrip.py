"""Real-Postgres regression: public.attention_daily_rollup (core_165, bu-tdd4k.5).

Exercises core_165 against a fully migrated Postgres instance (testcontainers),
not just mocked-pool unit tests:

- ``public.attention_daily_rollup`` is created with the expected columns and a
  ``day`` primary key.
- ``record_owner_ingress_rollup`` round-trips through the real table,
  incrementing ``owner_ingress_count`` on repeated calls for the same day.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.core.attention_ledger import record_owner_ingress_rollup

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


async def test_attention_daily_rollup_table_exists_with_expected_columns(
    db_pool: asyncpg.Pool,
) -> None:
    rows = await db_pool.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'attention_daily_rollup'
        """
    )
    columns = {r["column_name"] for r in rows}
    assert columns == {
        "day",
        "owner_ingress_count",
        "insights_delivered",
        "insights_engaged",
        "updated_at",
    }


async def test_day_is_primary_key(db_pool: asyncpg.Pool) -> None:
    day = datetime(2026, 3, 1, tzinfo=UTC).date()
    await db_pool.execute(
        "INSERT INTO public.attention_daily_rollup (day) VALUES ($1)",
        day,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db_pool.execute(
            "INSERT INTO public.attention_daily_rollup (day) VALUES ($1)",
            day,
        )


async def test_record_owner_ingress_rollup_inserts_then_increments(db_pool: asyncpg.Pool) -> None:
    occurred_at = datetime(2026, 4, 5, 10, 0, tzinfo=UTC)

    await record_owner_ingress_rollup(db_pool, occurred_at=occurred_at)
    row = await db_pool.fetchrow(
        "SELECT owner_ingress_count FROM public.attention_daily_rollup WHERE day = $1",
        occurred_at.date(),
    )
    assert row["owner_ingress_count"] == 1

    # A second owner-ingress event the same day increments in place rather
    # than erroring on the day primary key.
    await record_owner_ingress_rollup(db_pool, occurred_at=occurred_at)
    row = await db_pool.fetchrow(
        "SELECT owner_ingress_count FROM public.attention_daily_rollup WHERE day = $1",
        occurred_at.date(),
    )
    assert row["owner_ingress_count"] == 2

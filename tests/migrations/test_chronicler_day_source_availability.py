"""chronicler_042: retired sources and frozen per-day source availability, on real PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.flags import (
    SOURCE_CRON_MINUTES,
    correct_retired_feeder_dark,
    evaluate_and_write_daily_flags,
    freeze_day_availability,
    load_day_availability,
)
from butlers.chronicler.storage import mark_source_active, upsert_checkpoint
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.integration

_TZ = "UTC"
_DAY = date(2026, 11, 19)
_SOON_AFTER_CLOSE = datetime(2026, 11, 20, 0, 30, tzinfo=UTC)


def _database(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "chronicler"],
        schemas={"core": "chronicler_mem", "memory": "chronicler_mem", "chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_042"},
    )


def _asyncpg_url(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://")


async def _pool(url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        _asyncpg_url(url),
        init=register_jsonb_codec,
        server_settings={"search_path": "chronicler, public"},
    )


async def _healthy_fleet(pool: asyncpg.Pool, now: datetime) -> None:
    """Every source registered, active and freshly projected, except the retired one."""
    await seed_source_registry(pool, INITIAL_SOURCES)
    for name in SOURCE_CRON_MINUTES:
        if name == "health.meals":
            continue
        await mark_source_active(pool, name, active=True)
        await upsert_checkpoint(pool, name, watermark=now, success=True)
        await pool.execute(
            "UPDATE projection_checkpoints SET last_success_at = $2 "
            "WHERE source_name = $1 AND subsource = ''",
            name,
            now,
        )


def test_the_table_is_append_only_and_the_columns_exist(postgres_container) -> None:
    url = _database(postgres_container)

    async def body() -> None:
        pool = await _pool(url)
        try:
            await _healthy_fleet(pool, _SOON_AFTER_CLOSE)
            retired = await pool.fetchval(
                "SELECT retired_at FROM source_adapter_state WHERE source_name = 'health.meals'"
            )
            assert retired == date(2026, 8, 13)
            await freeze_day_availability(
                pool, local_date=_DAY, timezone=_TZ, now=_SOON_AFTER_CLOSE
            )
            for sql in (
                "UPDATE day_source_availability SET state = 'dark'",
                "DELETE FROM day_source_availability",
                "TRUNCATE day_source_availability",
            ):
                with pytest.raises(asyncpg.PostgresError, match="append-only"):
                    await pool.execute(sql)
        finally:
            await pool.close()

    asyncio.run(body())


def test_the_correction_log_is_append_only_including_truncate(postgres_container) -> None:
    url = _database(postgres_container)

    async def body() -> None:
        pool = await _pool(url)
        try:
            await pool.execute(
                "INSERT INTO retired_flag_corrections "
                "(local_date, flag_type, source_name, outcome) "
                "VALUES ('2026-09-01', 'feeder_dark', 'health.meals', 'flag_deleted')"
            )
            for sql in (
                "UPDATE retired_flag_corrections SET outcome = 'source_removed'",
                "DELETE FROM retired_flag_corrections",
                "TRUNCATE retired_flag_corrections",
            ):
                with pytest.raises(asyncpg.PostgresError, match="append-only"):
                    await pool.execute(sql)
        finally:
            await pool.close()

    asyncio.run(body())


def test_the_first_run_writes_one_row_per_source_and_a_second_writes_nothing(
    postgres_container,
) -> None:
    url = _database(postgres_container)

    async def body():
        pool = await _pool(url)
        try:
            await _healthy_fleet(pool, _SOON_AFTER_CLOSE)
            first = await freeze_day_availability(
                pool, local_date=_DAY, timezone=_TZ, now=_SOON_AFTER_CLOSE
            )
            again = await freeze_day_availability(
                pool, local_date=_DAY, timezone=_TZ, now=_SOON_AFTER_CLOSE + timedelta(hours=1)
            )
            count = await pool.fetchval("SELECT count(*) FROM day_source_availability")
            states = await load_day_availability(pool, local_date=_DAY, timezone=_TZ)
            return first, again, count, states
        finally:
            await pool.close()

    first, again, count, states = asyncio.run(body())
    assert (first, again) == ("frozen", "exists")
    assert count == len(SOURCE_CRON_MINUTES)
    assert states is not None and states["health.meals"] == "retired"


def test_a_recovered_source_does_not_erase_the_day_it_was_down(postgres_container) -> None:
    url = _database(postgres_container)

    async def body():
        pool = await _pool(url)
        try:
            await _healthy_fleet(pool, _SOON_AFTER_CLOSE)
            await mark_source_active(pool, "steam.play_history", active=False, inactive_reason="x")
            await freeze_day_availability(
                pool, local_date=_DAY, timezone=_TZ, now=_SOON_AFTER_CLOSE
            )
            frozen = await load_day_availability(pool, local_date=_DAY, timezone=_TZ)
            await evaluate_and_write_daily_flags(
                pool, local_date=_DAY, timezone=_TZ, now=_SOON_AFTER_CLOSE, availability=frozen
            )
            # The source recovers, and the job runs again the next hour.
            await mark_source_active(pool, "steam.play_history", active=True)
            await freeze_day_availability(
                pool, local_date=_DAY, timezone=_TZ, now=_SOON_AFTER_CLOSE + timedelta(hours=1)
            )
            again = await load_day_availability(pool, local_date=_DAY, timezone=_TZ)
            await evaluate_and_write_daily_flags(
                pool, local_date=_DAY, timezone=_TZ, now=_SOON_AFTER_CLOSE, availability=again
            )
            return (
                frozen,
                again,
                await pool.fetchrow(
                    "SELECT detail FROM daily_rollup_flags "
                    "WHERE local_date = $1 AND flag_type = 'feeder_dark'",
                    _DAY,
                ),
            )
        finally:
            await pool.close()

    frozen, again, flag = asyncio.run(body())
    assert frozen == again
    assert again["steam.play_history"] == "dark"
    assert flag is not None
    assert flag["detail"]["dark_sources"] == ["steam.play_history"]
    assert flag["detail"]["retired_sources"] == ["health.meals"]


def test_a_day_with_only_the_retired_source_has_no_feeder_dark(postgres_container) -> None:
    url = _database(postgres_container)

    async def body():
        pool = await _pool(url)
        try:
            await _healthy_fleet(pool, _SOON_AFTER_CLOSE)
            await freeze_day_availability(
                pool, local_date=_DAY, timezone=_TZ, now=_SOON_AFTER_CLOSE
            )
            frozen = await load_day_availability(pool, local_date=_DAY, timezone=_TZ)
            await evaluate_and_write_daily_flags(
                pool, local_date=_DAY, timezone=_TZ, now=_SOON_AFTER_CLOSE, availability=frozen
            )
            return await pool.fetchval(
                "SELECT count(*) FROM daily_rollup_flags "
                "WHERE local_date = $1 AND flag_type = 'feeder_dark'",
                _DAY,
            )
        finally:
            await pool.close()

    assert asyncio.run(body()) == 0


def test_a_day_past_the_horizon_has_no_snapshot(postgres_container) -> None:
    url = _database(postgres_container)

    async def body():
        pool = await _pool(url)
        try:
            await _healthy_fleet(pool, _SOON_AFTER_CLOSE)
            status = await freeze_day_availability(
                pool,
                local_date=date(2026, 11, 1),
                timezone=_TZ,
                now=_SOON_AFTER_CLOSE,
            )
            return status, await load_day_availability(
                pool, local_date=date(2026, 11, 1), timezone=_TZ
            )
        finally:
            await pool.close()

    assert asyncio.run(body()) == ("past_horizon", None)


def test_the_one_shot_correction_is_idempotent(postgres_container) -> None:
    url = _database(postgres_container)

    async def body():
        pool = await _pool(url)
        try:
            await seed_source_registry(pool, INITIAL_SOURCES)
            for day, dark in (
                (date(2026, 9, 1), ["health.meals"]),
                (date(2026, 9, 2), ["health.meals", "steam.play_history"]),
                (date(2026, 8, 1), ["health.meals"]),
            ):
                await pool.execute(
                    "INSERT INTO daily_rollup_flags (local_date, flag_type, severity, detail) "
                    "VALUES ($1, 'feeder_dark', 'warning', $2)",
                    day,
                    {"dark_sources": dark},
                )
            first = await correct_retired_feeder_dark(pool)
            second = await correct_retired_feeder_dark(pool)
            rows = await pool.fetch(
                "SELECT local_date, detail FROM daily_rollup_flags ORDER BY local_date"
            )
            return first, second, rows
        finally:
            await pool.close()

    first, second, rows = asyncio.run(body())
    assert first == {"corrected": 1, "deleted": 1}
    assert second == {"corrected": 0, "deleted": 0}
    by_date = {row["local_date"]: row["detail"] for row in rows}
    assert date(2026, 9, 1) not in by_date
    assert by_date[date(2026, 9, 2)]["dark_sources"] == ["steam.play_history"]
    # Before retired_at the source really was dark; that day is left alone.
    assert by_date[date(2026, 8, 1)]["dark_sources"] == ["health.meals"]


def test_the_downgrade_refuses_while_snapshots_exist(postgres_container) -> None:
    url = _database(postgres_container)

    async def seed() -> None:
        pool = await _pool(url)
        try:
            await _healthy_fleet(pool, _SOON_AFTER_CLOSE)
            await freeze_day_availability(
                pool, local_date=_DAY, timezone=_TZ, now=_SOON_AFTER_CLOSE
            )
        finally:
            await pool.close()

    asyncio.run(seed())
    config = _build_alembic_config(url, chains=["chronicler"], target_schema="chronicler")
    with pytest.raises(Exception, match="only record of past source availability"):
        command.downgrade(config, "chronicler@chronicler_041")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM chronicler.day_source_availability")
                ).scalar()
                > 0
            )
    finally:
        engine.dispose()

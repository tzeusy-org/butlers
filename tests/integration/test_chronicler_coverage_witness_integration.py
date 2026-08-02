"""Real-Postgres integration tests for the covered-local-day witness and the
no_data/unavailable/quiet precedence it drives (bu-ep4ks.1,
clarify-chronicles-narrative-truth).

Mocked-pool unit tests (``tests/chronicler/test_editorial.py``) cover the
precedence logic with monkeypatched coverage helpers. These tests prove the
real read/write path against the migrated ``covered_local_days`` table and
``tier2_cache`` admission columns added by ``chronicler_023`` — the exact
gap that shipped zero runtime code in bu-27dxl.1.1: an outage or an
unproven historical day must never render as "Quiet day."
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from types import SimpleNamespace

import asyncpg
import pytest

from butlers.chronicler.day_close_writer import DAY_CLOSE_TASK_NAME, write_day_close_cache
from butlers.chronicler.editorial import (
    _fetch_earliest_covered_date,
    _fetch_recent_days,
    _is_local_day_covered,
    compose_briefing_payload,
    record_coverage_witness,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=5, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE covered_local_days")
    await p.execute("TRUNCATE TABLE tier2_cache")
    await p.execute("TRUNCATE TABLE episodes CASCADE")
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# covered_local_days table shape + witness round-trip
# ---------------------------------------------------------------------------


async def test_migration_creates_covered_local_days_table(pool) -> None:
    row = await pool.fetchrow(
        "SELECT to_regclass('covered_local_days') AS reg",
    )
    assert row["reg"] is not None


async def test_migration_marks_coverage_witness_provenance(pool) -> None:
    columns = await pool.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'covered_local_days' AND column_name = 'origin'
        """
    )
    assert [row["column_name"] for row in columns] == ["origin"]


async def test_migration_adds_tier2_cache_admission_columns(pool) -> None:
    rows = await pool.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'tier2_cache' AND column_name IN ('date_label', 'invalid_reason')
        """
    )
    columns = {r["column_name"] for r in rows}
    assert columns == {"date_label", "invalid_reason"}


async def test_record_coverage_witness_is_idempotent(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 1), "UTC")
    await record_coverage_witness(pool, date(2026, 5, 1), "UTC")

    count = await pool.fetchval(
        "SELECT count(*) FROM covered_local_days WHERE local_date = $1 AND timezone = $2",
        date(2026, 5, 1),
        "UTC",
    )
    assert count == 1
    assert (
        await pool.fetchval(
            "SELECT origin FROM covered_local_days WHERE local_date = $1 AND timezone = $2",
            date(2026, 5, 1),
            "UTC",
        )
        == "day_close_success"
    )


async def test_record_coverage_witness_promotes_legacy_row(pool) -> None:
    await pool.execute(
        """
        INSERT INTO covered_local_days (local_date, timezone, origin)
        VALUES ($1, $2, 'legacy_unverified')
        """,
        date(2026, 5, 1),
        "UTC",
    )

    await record_coverage_witness(pool, date(2026, 5, 1), "UTC")

    assert (
        await pool.fetchval(
            "SELECT origin FROM covered_local_days WHERE local_date = $1 AND timezone = $2",
            date(2026, 5, 1),
            "UTC",
        )
        == "day_close_success"
    )


async def test_is_local_day_covered_reflects_recorded_witness(pool) -> None:
    assert await _is_local_day_covered(pool, date(2026, 5, 2), "UTC") is False
    await record_coverage_witness(pool, date(2026, 5, 2), "UTC")
    assert await _is_local_day_covered(pool, date(2026, 5, 2), "UTC") is True


async def test_legacy_witness_is_not_authoritative_coverage(pool) -> None:
    await pool.execute(
        """
        INSERT INTO covered_local_days (local_date, timezone, origin)
        VALUES ($1, $2, 'legacy_unverified')
        """,
        date(2026, 5, 2),
        "UTC",
    )

    assert await _is_local_day_covered(pool, date(2026, 5, 2), "UTC") is False
    assert await _fetch_earliest_covered_date(pool, "UTC") is None


async def test_recent_days_index_excludes_legacy_witnesses(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 9), "UTC")
    await pool.execute(
        """
        INSERT INTO covered_local_days (local_date, timezone, origin)
        VALUES ($1, $2, 'legacy_unverified')
        """,
        date(2026, 5, 8),
        "UTC",
    )

    recent_days = await _fetch_recent_days(
        pool,
        datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        days=3,
        tz_name="UTC",
    )

    assert [recent_day.date for recent_day in recent_days] == ["2026-05-09"]


async def test_earliest_covered_date_is_min_across_witnesses(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 5), "UTC")
    await record_coverage_witness(pool, date(2026, 5, 2), "UTC")
    await record_coverage_witness(pool, date(2026, 5, 9), "UTC")

    earliest = await _fetch_earliest_covered_date(pool, "UTC")
    assert earliest == date(2026, 5, 2)


async def test_earliest_covered_date_scoped_by_timezone(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 1), "UTC")
    await record_coverage_witness(pool, date(2026, 4, 1), "Asia/Singapore")

    assert await _fetch_earliest_covered_date(pool, "UTC") == date(2026, 5, 1)
    assert await _fetch_earliest_covered_date(pool, "Asia/Singapore") == date(2026, 4, 1)


# ---------------------------------------------------------------------------
# compose_briefing_payload precedence, end to end against a real pool
# ---------------------------------------------------------------------------


async def test_compose_unavailable_before_any_witness_recorded(pool) -> None:
    """No coverage floor exists yet: every settled day is unavailable, never
    a fabricated quiet day."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    payload = await compose_briefing_payload(pool, date(2026, 5, 5), "UTC", now=now)

    assert payload.state_class == "unavailable"
    assert payload.covered_and_available is False
    assert payload.earliest_date is None


async def test_compose_no_data_before_recorded_floor(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 5), "UTC")
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    payload = await compose_briefing_payload(pool, date(2026, 5, 1), "UTC", now=now)

    assert payload.state_class == "no_data"
    assert payload.covered_and_available is False
    assert payload.earliest_date == "2026-05-05"


async def test_compose_unavailable_for_coverage_gap_after_floor(pool) -> None:
    """A day on/after the floor with no witness of its own is a gap, not proof
    of absence — stays unavailable, never no_data or quiet."""
    await record_coverage_witness(pool, date(2026, 5, 1), "UTC")
    await record_coverage_witness(pool, date(2026, 5, 9), "UTC")
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    payload = await compose_briefing_payload(pool, date(2026, 5, 5), "UTC", now=now)

    assert payload.state_class == "unavailable"
    assert payload.covered_and_available is False


async def test_compose_quiet_for_covered_empty_day(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 5), "UTC")
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    payload = await compose_briefing_payload(pool, date(2026, 5, 5), "UTC", now=now)

    assert payload.state_class == "quiet"
    assert payload.covered_and_available is True
    assert payload.earliest_date == "2026-05-05"


@pytest.mark.parametrize(
    "tool_calls",
    [
        [],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {
                    "date": "2026-05-04",
                    "citations": [],
                    "episodes": [],
                    "events": [],
                },
            }
        ],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-01", "timezone": "UTC"},
                "outcome": "success",
                "result": {
                    "date": "2026-05-01",
                    "citations": [],
                    "episodes": [],
                    "events": [],
                },
            }
        ],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {"date": "2026-05-05", "citations": [], "events": []},
            }
        ],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {"date": "2026-05-05", "citations": [], "episodes": []},
            }
        ],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {
                    "date": "2026-05-05",
                    "citations": [],
                    "episodes": {"unexpected": "shape"},
                    "events": [],
                },
            }
        ],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {
                    "date": "2026-05-05",
                    "citations": [],
                    "episodes": [],
                    "events": {"unexpected": "shape"},
                },
            }
        ],
    ],
    ids=[
        "no-bundle",
        "wrong-date",
        "historical-mismatch",
        "missing-episodes",
        "missing-events",
        "nonlist-episodes",
        "nonlist-events",
    ],
)
async def test_unproven_day_close_capture_leaves_historical_briefing_unavailable(
    pool, tool_calls
) -> None:
    """A successful dispatch without a matching target capture never proves history."""
    target = date(2026, 5, 5)
    result = SimpleNamespace(success=True, output="", tool_calls=tool_calls)

    await write_day_close_cache(
        pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=datetime(2026, 5, 6, 1, 5, tzinfo=UTC),
        tz="UTC",
    )

    assert await _is_local_day_covered(pool, target, "UTC") is False
    payload = await compose_briefing_payload(
        pool,
        target,
        "UTC",
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    assert payload.state_class == "unavailable"
    assert payload.covered_and_available is False


async def test_valid_empty_canonical_day_close_capture_makes_historical_briefing_quiet(
    pool,
) -> None:
    """A matching canonical empty bundle is durable proof of a quiet closed day."""
    target = date(2026, 5, 5)
    result = SimpleNamespace(
        success=True,
        output="",
        tool_calls=[
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {
                    "date": "2026-05-05",
                    "citations": [],
                    "episodes": [],
                    "events": [],
                },
            }
        ],
    )

    await write_day_close_cache(
        pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=datetime(2026, 5, 6, 1, 5, tzinfo=UTC),
        tz="UTC",
    )

    assert await _is_local_day_covered(pool, target, "UTC") is True
    payload = await compose_briefing_payload(
        pool,
        target,
        "UTC",
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    assert payload.state_class == "quiet"
    assert payload.covered_and_available is True


async def test_compose_today_bypasses_coverage_gate_even_when_uncovered(pool) -> None:
    """Today has not closed yet, so it never renders unavailable purely for
    lacking a witness -- unlike a settled day."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    payload = await compose_briefing_payload(pool, date(2026, 5, 10), "UTC", now=now)

    assert payload.state_class == "quiet"
    assert payload.covered_and_available is True

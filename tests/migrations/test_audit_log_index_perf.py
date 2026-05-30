"""Performance/integration test for ix_audit_log_target_ts composite index.

Origin: PR #1923, bu-eh5ev — index added in core_105 migration.

This test seeds public.audit_log with >= 100 000 rows, then runs
``EXPLAIN (ANALYZE, FORMAT TEXT)`` against the by-target query
(``WHERE target = $1 ORDER BY ts DESC``) and asserts:

  1.  PostgreSQL chooses an **Index Scan** (not a Seq Scan).
  2.  The plan explicitly names ``ix_audit_log_target_ts``.
  3.  The *actual* query execution time stays under ``LATENCY_THRESHOLD_MS``.

Threshold rationale
-------------------
``LATENCY_THRESHOLD_MS = 50`` gives a comfortable ceiling for a by-target
lookup on 100 k rows in a local Docker container.  An index scan over the
subset of rows sharing a single ``target`` value returns in < 5 ms even at
1 M rows on real hardware (per migration spec §3).  50 ms absorbs container
startup jitter while still catching a regression to a full sequential scan,
which takes 80–200 ms at 100 k rows in the same environment.

Opt-in only
-----------
This test is excluded from the default ``make test`` / ``pytest`` run by the
``perf`` marker.  Run it explicitly with::

    uv run pytest tests/migrations/test_audit_log_index_perf.py -m perf -v

It requires Docker (testcontainers) and is automatically skipped when Docker
is not available.
"""

from __future__ import annotations

import shutil
import time
from typing import Any

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Number of rows to seed.  Must be >= 100 000 per acceptance criteria.
SEED_ROWS: int = 100_000

#: Fraction of rows that share the "hot" target value used in the query.
#: 1/10 → 10 000 rows share the target, making a sequential scan tempting
#: enough to show up if the index is missing.
HOT_FRACTION: int = 10

#: Documented latency ceiling in milliseconds.  See module docstring.
LATENCY_THRESHOLD_MS: float = 50.0

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.perf,
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container: Any) -> str:
    """Provision a DB with all core migrations applied once for this module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture(scope="module")
async def seeded_pool(migrated_db_url: str) -> asyncpg.Pool:  # type: ignore[return]
    """Create an asyncpg pool and seed audit_log with SEED_ROWS rows.

    Seeding runs once per module; individual tests are read-only and do not
    mutate the table, so no per-test TRUNCATE is needed.

    The seed uses a single bulk INSERT with VALUES to avoid 100 000
    round-trips.  ``generate_series`` keeps it entirely server-side.
    """
    pool = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)

    # Bulk-insert via generate_series: one SQL round-trip for all rows.
    # Rows alternate between the "hot" target value (shared by HOT_FRACTION of
    # all rows) and a unique per-row target, so the planner has realistic
    # statistics to reason about.
    await pool.execute(f"""
        INSERT INTO public.audit_log (actor, action, target, ts)
        SELECT
            'perf-test-actor',
            'perf-test-action',
            CASE
                WHEN (i % {HOT_FRACTION}) = 0 THEN 'u:perf-target'
                ELSE 'u:other-target-' || i::text
            END,
            now() - (i * interval '1 second')
        FROM generate_series(1, {SEED_ROWS}) AS i
    """)

    yield pool
    await pool.close()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _extract_plan_rows(explain_output: list[Any]) -> list[str]:
    """Flatten asyncpg EXPLAIN rows into a list of text lines."""
    lines: list[str] = []
    for row in explain_output:
        # asyncpg returns each EXPLAIN row as a Record with one text column.
        line = str(row[0]) if row else ""
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_index_scan_used_for_by_target_query(seeded_pool: asyncpg.Pool) -> None:
    """EXPLAIN (ANALYZE) confirms ix_audit_log_target_ts is used for by-target lookup.

    Acceptance criteria (bu-1uyvg):
      1.  Seed >= 100 000 rows — verified by the seeded_pool fixture.
      2.  EXPLAIN output shows an Index Scan (not a Seq Scan).
      3.  ix_audit_log_target_ts is named in the plan.
      4.  Actual execution time is below LATENCY_THRESHOLD_MS.
    """
    # EXPLAIN ANALYZE returns timing of the *inner* execution, not round-trip.
    # We also wall-clock the execute() call as a second latency signal.
    target_value = "u:perf-target"

    explain_sql = """
        EXPLAIN (ANALYZE, FORMAT TEXT)
        SELECT id, ts, actor, action, target, note, ip, request_id
        FROM public.audit_log
        WHERE target = $1
        ORDER BY ts DESC
        LIMIT 100
    """

    wall_start = time.perf_counter()
    rows = await seeded_pool.fetch(explain_sql, target_value)
    wall_elapsed_ms = (time.perf_counter() - wall_start) * 1000

    plan_lines = _extract_plan_rows(rows)
    plan_text = "\n".join(plan_lines)

    # --- Criterion 2: No sequential scan ---
    assert "Seq Scan" not in plan_text, (
        f"Expected an Index Scan but got a Seq Scan. Full plan:\n{plan_text}"
    )

    # --- Criterion 2: Index scan (Bitmap Index Scan also acceptable) ---
    assert "Index Scan" in plan_text or "Bitmap Index Scan" in plan_text, (
        f"Expected 'Index Scan' or 'Bitmap Index Scan' in EXPLAIN output. Full plan:\n{plan_text}"
    )

    # --- Criterion 3: Correct index named ---
    assert "ix_audit_log_target_ts" in plan_text, (
        f"Expected ix_audit_log_target_ts in EXPLAIN plan. Full plan:\n{plan_text}"
    )

    # --- Criterion 4: Extract actual execution time from EXPLAIN ANALYZE ---
    # Format: "  Execution Time: 0.412 ms"
    actual_time_ms: float | None = None
    for line in plan_lines:
        stripped = line.strip()
        if stripped.startswith("Execution Time:"):
            try:
                actual_time_ms = float(stripped.split(":")[1].strip().split()[0])
            except (IndexError, ValueError):
                pass
            break

    # Prefer the PostgreSQL-reported execution time; fall back to wall clock.
    measured_ms = actual_time_ms if actual_time_ms is not None else wall_elapsed_ms

    assert measured_ms < LATENCY_THRESHOLD_MS, (
        f"Query latency {measured_ms:.2f} ms exceeds threshold {LATENCY_THRESHOLD_MS} ms. "
        f"Full plan:\n{plan_text}"
    )

    # Emit a summary line visible in verbose mode (-v).
    print(
        f"\n[perf] ix_audit_log_target_ts on {SEED_ROWS:,} rows — "
        f"PG execution time: {actual_time_ms:.2f} ms, "
        f"wall-clock: {wall_elapsed_ms:.2f} ms, "
        f"threshold: {LATENCY_THRESHOLD_MS} ms\n"
        f"Plan excerpt:\n{plan_text[:800]}"
    )

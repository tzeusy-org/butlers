"""Tests for health butler scheduled job SQL — schema-isolation assertions.

Verifies that health_jobs.run_insight_scan queries the facts table via unqualified
name (resolved through search_path to public.facts) rather than hard-coding the
public schema.  No real database required; asyncpg is mocked.

Follow-up to merged PR #1610 which fixed briefing.py. See:
  roster/health/jobs/health_jobs.py — three query sites fixed here.

Call order within run_insight_scan:
  fetch[0]  — section 1: SELECT DISTINCT predicate FROM facts  (gap detection)
  fetch[1]  — section 1 inner loop: SELECT valid_at FROM facts (history per type)
  fetch[2]  — section 2: SELECT id, name, frequency FROM health.medications
  fetch[3]  — section 3: SELECT name, severity FROM health.symptoms
  fetch[4]  — section 4 inner loop: SELECT DISTINCT DATE(...) FROM facts  (streak)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.jobs._roster_loader import load_roster_jobs

pytestmark = pytest.mark.unit

# Shared sentinel: one predicate row to make the inner loops run once.
_PREDICATE_ROWS = [{"predicate": "measurement_weight"}]
_NO_ROWS: list[Any] = []


def _make_pool(fetch_side_effect: list[list[Any]]) -> MagicMock:
    """Build a mock pool.

    ``fetch`` returns the next queued row-list, or ``[]`` once the queue is
    exhausted. The fallback keeps these SQL-isolation tests robust to the extra
    queries issued by later scan sections (e.g. cross-signal correlation), which
    no-op on empty results.
    """
    pool = MagicMock()
    queue = list(fetch_side_effect)

    def _next_fetch(*args: Any, **kwargs: Any) -> list[Any]:
        return queue.pop(0) if queue else []

    pool.fetch = AsyncMock(side_effect=_next_fetch)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock()
    return pool


async def _fake_propose(*args, **kwargs):
    return {"status": "accepted"}


class _RoutingPool:
    """Async pool stub that routes ``fetch``/``fetchval`` by SQL + args.

    Used by the cross-signal correlation unit tests: each test supplies small
    routers that return canned rows for the queries that matter and ``[]``/``0``
    for everything else, so unrelated scan sections no-op cleanly.
    """

    def __init__(self, fetch_router=None, fetchval_router=None) -> None:
        self._fetch_router = fetch_router or (lambda sql, args: [])
        self._fetchval_router = fetchval_router or (lambda sql, args: 0)
        self.execute = AsyncMock()
        self.fetchrow = AsyncMock(return_value=None)
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self.fetch_calls.append((sql, args))
        return self._fetch_router(sql, args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.fetchval_calls.append((sql, args))
        return self._fetchval_router(sql, args)


# ---------------------------------------------------------------------------
# search_path schema-isolation: the three insight-scan facts query sites
# (predicate gap-detection, per-type history, consecutive-day streak) must all
# use unqualified 'facts' (resolved via search_path) and never 'public.facts'.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql_marker",
    ["SELECT DISTINCT predicate", "SELECT valid_at", "SELECT DISTINCT DATE"],
    ids=["predicate-gap", "per-type-history", "streak"],
)
async def test_insight_scan_facts_queries_use_search_path_isolation(sql_marker):
    # One predicate row makes every inner loop fire once so all three query sites run.
    pool = _make_pool([_PREDICATE_ROWS, _NO_ROWS, _NO_ROWS, _NO_ROWS, _NO_ROWS])

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=_fake_propose,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool)

    query_sql = next(c.args[0] for c in pool.fetch.call_args_list if sql_marker in c.args[0])
    assert "FROM facts" in query_sql, f"{sql_marker!r} query must use unqualified 'facts'"
    assert "public.facts" not in query_sql, (
        f"{sql_marker!r} query must not hard-code 'public.facts'"
    )


# ---------------------------------------------------------------------------
# Cross-signal correlation — submitted via the propose_insight_candidate MCP tool
# ---------------------------------------------------------------------------


def _capture_proposals():
    """Return (captured_list, fake_propose) for asserting MCP-tool submissions."""
    captured: list[dict[str, Any]] = []

    async def _fake(pool, **kwargs):
        captured.append(kwargs)
        return {"status": "accepted"}

    return captured, _fake


async def test_adherence_symptom_correlation_submits_via_mcp_tool():
    """An adherence dip followed by a symptom flare submits a correlation candidate."""
    from datetime import UTC, datetime, timedelta

    from butlers.api.briefing.lint import voice_lint_passes

    now = datetime.now(UTC)
    med_id = "11111111-1111-1111-1111-111111111111"

    def fetch_router(sql, args):
        if "predicate = 'medication'" in sql:
            return [{"id": med_id, "name": "Metformin", "frequency": "daily"}]
        return []

    def fetchval_router(sql, args):
        if "'symptom'" in sql:
            return 3  # flare: >= _FLARE_MIN_SYMPTOMS higher-severity entries
        if "'took_dose'" in sql:
            # args = (med_id, window_start, window_end); the prior window starts earliest.
            window_start = args[1]
            if window_start <= now - timedelta(days=18):
                return 7  # prior week: the user normally logs this medication
            return 0  # dip week: adherence collapsed
        return 0

    pool = _RoutingPool(fetch_router, fetchval_router)
    captured, fake = _capture_proposals()

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=fake,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool)

    adherence = [c for c in captured if c["category"] == "correlation-adherence"]
    assert len(adherence) == 1
    cand = adherence[0]
    assert cand["origin_butler"] == "health"
    assert cand["priority"] == 60
    assert cand["dedup_key"].startswith(f"health:correlation-adherence:{med_id}:")
    assert "Metformin" in cand["message"]
    assert voice_lint_passes(cand["message"])


async def test_measurement_drift_correlation_submits_via_mcp_tool():
    """A gradual median shift across recent readings submits a drift candidate."""
    from butlers.api.briefing.lint import voice_lint_passes

    # Newest-first: newest third median 80, oldest third median 70 → ~14% drift.
    drift_values = ["80", "80", "80", "75", "75", "75", "70", "70", "70"]

    def fetch_router(sql, args):
        if "SELECT DISTINCT predicate" in sql:
            return [{"predicate": "measurement_weight"}]
        if "metadata->>'value' AS value" in sql:
            return [{"value": v} for v in drift_values]
        return []

    pool = _RoutingPool(fetch_router)
    captured, fake = _capture_proposals()

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=fake,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool)

    drift = [c for c in captured if c["category"] == "correlation-drift"]
    assert len(drift) == 1
    cand = drift[0]
    assert cand["priority"] == 50
    assert cand["dedup_key"].startswith("health:correlation-drift:weight:")
    assert "upward" in cand["message"]
    assert voice_lint_passes(cand["message"])


async def test_environment_correlation_submits_via_mcp_tool():
    """Adverse HA environment readings co-occurring with short sleep submit a candidate."""
    from datetime import UTC, datetime, timedelta

    from butlers.api.briefing.lint import voice_lint_passes

    now = datetime.now(UTC)
    day1 = now - timedelta(days=2)
    day2 = now - timedelta(days=3)

    def fetch_router(sql, args):
        if "'sleep_session'" in sql:
            # 5h sleep (< _ENV_SLEEP_SHORT_HOURS) on two distinct days.
            return [
                {"valid_at": day1, "duration_ms": "18000000"},
                {"valid_at": day2, "duration_ms": "18000000"},
            ]
        return []

    async def reader():
        return [
            {"captured_at": day1, "metric": "temperature", "adverse": True},
            {"captured_at": day2, "metric": "temperature", "adverse": True},
        ]

    pool = _RoutingPool(fetch_router)
    captured, fake = _capture_proposals()

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=fake,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool, reader)

    env = [c for c in captured if c["category"] == "correlation-environment"]
    assert len(env) == 1
    cand = env[0]
    assert cand["priority"] == 50
    assert cand["dedup_key"].startswith("health:correlation-env:temperature:")
    assert "bedroom temperature" in cand["message"]
    assert voice_lint_passes(cand["message"])


async def test_environment_correlation_skipped_without_reader():
    """With no HA environment reader wired, no environment candidate is submitted."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    def fetch_router(sql, args):
        if "'sleep_session'" in sql:
            return [{"valid_at": now - timedelta(days=2), "duration_ms": "18000000"}]
        return []

    pool = _RoutingPool(fetch_router)
    captured, fake = _capture_proposals()

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=fake,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool)  # no reader

    assert not [c for c in captured if c["category"] == "correlation-environment"]


async def test_environment_correlation_below_min_days_no_candidate():
    """A single overlapping day is below the threshold and produces no candidate."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    day1 = now - timedelta(days=2)

    def fetch_router(sql, args):
        if "'sleep_session'" in sql:
            return [{"valid_at": day1, "duration_ms": "18000000"}]
        return []

    async def reader():
        return [{"captured_at": day1, "metric": "temperature", "adverse": True}]

    pool = _RoutingPool(fetch_router)
    captured, fake = _capture_proposals()

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=fake,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool, reader)

    assert not [c for c in captured if c["category"] == "correlation-environment"]

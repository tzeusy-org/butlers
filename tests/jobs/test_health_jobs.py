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
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock()
    return pool


async def _fake_propose(*args, **kwargs):
    return {"status": "accepted"}


# ---------------------------------------------------------------------------
# Section 1a — predicate query (fetch[0])
# ---------------------------------------------------------------------------


async def test_insight_scan_predicate_query_does_not_hardcode_public_facts():
    """The 'get all distinct predicates' query uses unqualified 'facts', not 'public.facts'.

    This is the first SQL query in run_insight_scan.
    """
    # Empty predicate list: inner loops skip, remaining fetch calls:
    #   fetch[0] predicate, fetch[1] meds, fetch[2] symptoms  (no per-type loops run)
    pool = _make_pool([_NO_ROWS, _NO_ROWS, _NO_ROWS])

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=_fake_propose,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool)

    assert pool.fetch.call_count >= 1
    first_call_sql = next(
        c.args[0] for c in pool.fetch.call_args_list if "SELECT DISTINCT predicate" in c.args[0]
    )
    assert "FROM facts" in first_call_sql, (
        "measurement-gap predicate query must use unqualified 'facts'"
    )
    assert "public.facts" not in first_call_sql, (
        "measurement-gap predicate query must not hard-code 'public.facts'"
    )
    assert "measurement" in first_call_sql


# ---------------------------------------------------------------------------
# Section 1b — per-type history query (fetch[1] when one predicate row)
# ---------------------------------------------------------------------------


async def test_insight_scan_history_query_does_not_hardcode_public_facts():
    """The per-type cadence history query uses unqualified 'facts', not 'public.facts'.

    With one predicate row the loop fires once and fetch[1] is the history query.
    fetch order: [0] predicate, [1] history, [2] meds, [3] symptoms, [4] streak
    """
    pool = _make_pool(
        [
            _PREDICATE_ROWS,  # [0] predicate query
            _NO_ROWS,  # [1] history for measurement_weight (< min, no gap insight)
            _NO_ROWS,  # [2] meds
            _NO_ROWS,  # [3] symptoms
            _NO_ROWS,  # [4] streak for measurement_weight
        ]
    )

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=_fake_propose,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool)

    assert pool.fetch.call_count >= 2
    history_call_sql = next(
        c.args[0] for c in pool.fetch.call_args_list if "SELECT valid_at" in c.args[0]
    )
    assert "FROM facts" in history_call_sql, (
        "per-type cadence history query must use unqualified 'facts'"
    )
    assert "public.facts" not in history_call_sql, (
        "per-type cadence history query must not hard-code 'public.facts'"
    )
    assert "valid_at" in history_call_sql


# ---------------------------------------------------------------------------
# Section 4 — streak query (fetch[4] when one predicate row)
# ---------------------------------------------------------------------------


async def test_insight_scan_streak_query_does_not_hardcode_public_facts():
    """The consecutive-day streak query uses unqualified 'facts', not 'public.facts'.

    With one predicate row the streak loop fires once and fetch[4] is the streak query.
    fetch order: [0] predicate, [1] history, [2] meds, [3] symptoms, [4] streak
    """
    pool = _make_pool(
        [
            _PREDICATE_ROWS,  # [0] predicate query
            _NO_ROWS,  # [1] history (< min → no gap insight)
            _NO_ROWS,  # [2] meds (no medication refill loop)
            _NO_ROWS,  # [3] symptoms
            _NO_ROWS,  # [4] streak for measurement_weight
        ]
    )

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=_fake_propose,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool)

    assert pool.fetch.call_count >= 5
    streak_call_sql = next(
        c.args[0] for c in pool.fetch.call_args_list if "SELECT DISTINCT DATE" in c.args[0]
    )
    assert "FROM facts" in streak_call_sql, "streak query must use unqualified 'facts'"
    assert "public.facts" not in streak_call_sql, "streak query must not hard-code 'public.facts'"
    assert "valid_at" in streak_call_sql

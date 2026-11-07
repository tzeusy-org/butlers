"""Unit tests for the health butler's ``run_insight_scan`` job.

Two kinds of check, both DB-free:

* Schema isolation: the insight-scan facts queries use the unqualified ``facts`` name
  (resolved through search_path to public.facts) rather than hard-coding the public
  schema. That is deliberately a SQL-text check: production resolves either spelling
  to public.facts, so a real database cannot tell them apart. Follow-up to PR #1610.
* Candidate assembly: given section rows, the scan submits the right insight
  candidates. Rows are queued on the strict kit pool in the scan's fixed read order;
  SQL is never inspected. The queries themselves run on real Postgres in
  roster/health/tests/test_jobs.py.

Read order within run_insight_scan (one measurement type, no refillable medication):
  fetch     — measurement types (SELECT DISTINCT predicate FROM facts)
  fetch     — per type: history (SELECT valid_at ... ORDER BY valid_at DESC)
  fetch     — medications
  fetch     — symptoms (last 7 days)
  fetch     — per type: streak days (SELECT DISTINCT DATE(...))
  fetchval  — symptom-flare count; then per medication, prior and dip dose counts
  fetch     — per type: drift readings
  fetch     — with an environment reader: sleep sessions, then symptoms
"""

from __future__ import annotations

from itertools import chain, repeat
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.jobs._roster_loader import load_roster_jobs
from butlers.testing.fake_pool import FakePool, fake_pool
from butlers.testing.records import records

pytestmark = pytest.mark.unit

# Shared sentinel: one predicate row to make the inner loops run once.
_PREDICATE_ROWS = [{"predicate": "measurement_weight"}]


def _one_predicate_scan_pool() -> FakePool:
    """A health pool whose first ``fetch`` (gap detection) answers one predicate row.

    Every later read answers ``[]`` and every scalar ``0``, so each inner loop fires
    once and later scan sections (e.g. cross-signal correlation) no-op.
    """
    return fake_pool(
        fetch=AsyncMock(side_effect=chain([records(_PREDICATE_ROWS)], repeat([]))),
        fetchrow=None,
        fetchval=0,
        execute=None,
    )


async def _fake_propose(*args, **kwargs):
    return {"status": "accepted"}


def _scan_pool(
    *,
    measurement: str | None = None,
    history: list[dict[str, Any]] | None = None,
    drift: list[dict[str, Any]] | None = None,
    medications: list[dict[str, Any]] | None = None,
    flare_count: int = 0,
    dose_counts: tuple[int, ...] = (),
    sleep: list[dict[str, Any]] | None = None,
) -> FakePool:
    """A strict health pool answering ``run_insight_scan``'s reads in call order.

    The scan's read order is fixed (see the module docstring), so each section's rows
    are queued rather than matched on SQL text:

      fetch: measurement types; per type, its history; medications; symptoms ([]);
             per type, its streak days ([]); per type, its drift readings; then, when
             ``sleep`` is given (an environment reader is wired), sleep sessions and
             environment symptoms ([]).
      fetchval: the symptom-flare count, then (prior, dip) dose counts per medication.

    Only one measurement type is supported. ``execute`` is configured only when a
    measurement history is queued, because only the gap section's expected-signal
    upsert writes. A read past the queue raises, and ``_assert_drained`` proves every
    queued read was consumed.
    """
    types = [{"predicate": f"measurement_{measurement}"}] if measurement else []
    fetch = [types]
    if measurement:
        fetch.append(history or [])
    fetch += [medications or [], []]
    if measurement:
        fetch += [[], drift or []]
    if sleep is not None:
        fetch += [sleep, []]
    fetchval = [flare_count, *dose_counts]
    configured: dict[str, Any] = {
        "fetch": AsyncMock(side_effect=[records(rows) for rows in fetch]),
        "fetchval": AsyncMock(side_effect=fetchval),
    }
    if history:
        configured["execute"] = None
    pool = fake_pool(**configured)
    pool.queued_reads = (len(fetch), len(fetchval))  # type: ignore[attr-defined]
    return pool


def _assert_drained(pool: FakePool) -> None:
    """Every queued read was consumed: the scan reached each section it was fed."""
    assert (pool.fetch.await_count, pool.fetchval.await_count) == pool.queued_reads  # type: ignore[attr-defined]


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
    pool = _one_predicate_scan_pool()

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


async def test_measurement_gap_submits_typed_door_metadata():
    """A measurement gap carries its own bounded chart-door metadata."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    history = [now - timedelta(days=25 + i * 7) for i in range(5)]

    pool = _scan_pool(
        measurement="weight",
        history=[
            {"measured_at": measured_at, "source": "owner_log", "source_endpoint_identity": None}
            for measured_at in history
        ],
    )
    captured, fake = _capture_proposals()

    before = datetime.now(UTC)
    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=fake,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool)
    _assert_drained(pool)
    after = datetime.now(UTC)

    gap = [candidate for candidate in captured if candidate["category"] == "measurement-gap"]
    assert len(gap) == 1
    metadata = gap[0]["metadata"]
    assert metadata["measurement_door"] == {
        "type": "weight",
        "since": history[0].date().isoformat(),
        "until": now.date().isoformat(),
    }
    # bu-ep4ks.9 adoption: event_window carries the same since/until at full
    # datetime precision (the shape broker._candidate_time_window expects).
    # `since` (most_recent) is deterministic from the test's own `history`;
    # `until` is the job's own internal now_utc, only bounded here.
    assert metadata["event_window"]["start"] == history[0].isoformat()
    event_window_end = datetime.fromisoformat(metadata["event_window"]["end"])
    assert before <= event_window_end <= after


async def test_adherence_symptom_correlation_submits_via_mcp_tool():
    """An adherence dip followed by a symptom flare submits a correlation candidate."""
    from butlers.api.briefing.lint import voice_lint_passes

    med_id = "11111111-1111-1111-1111-111111111111"

    pool = _scan_pool(
        medications=[{"id": med_id, "name": "Metformin", "frequency": "daily"}],
        flare_count=3,  # >= _FLARE_MIN_SYMPTOMS higher-severity entries
        dose_counts=(7, 0),  # prior week logged normally; the dip week collapsed
    )
    captured, fake = _capture_proposals()

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=fake,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool)
    _assert_drained(pool)

    adherence = [c for c in captured if c["category"] == "correlation-adherence"]
    assert len(adherence) == 1
    cand = adherence[0]
    assert cand["origin_butler"] == "health"
    assert cand["priority"] == 60
    assert cand["dedup_key"].startswith(f"health:correlation-adherence:{med_id}:")
    assert "Metformin" in cand["message"]
    assert "metadata" not in cand
    assert voice_lint_passes(cand["message"])


async def test_measurement_drift_correlation_submits_via_mcp_tool():
    """A gradual median shift across recent readings submits a drift candidate."""
    from datetime import UTC, datetime, timedelta

    from butlers.api.briefing.lint import voice_lint_passes

    # Newest-first: newest third median 80, oldest third median 70 → ~14% drift.
    drift_values = ["80", "80", "80", "75", "75", "75", "70", "70", "70"]
    now = datetime.now(UTC)

    pool = _scan_pool(
        measurement="weight",
        drift=[
            {"measured_at": now - timedelta(days=index), "value": value}
            for index, value in enumerate(drift_values)
        ],
    )
    captured, fake = _capture_proposals()

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=fake,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool)
    _assert_drained(pool)

    drift = [c for c in captured if c["category"] == "correlation-drift"]
    assert len(drift) == 1
    cand = drift[0]
    assert cand["priority"] == 50
    assert cand["dedup_key"].startswith("health:correlation-drift:weight:")
    assert "upward" in cand["message"]
    assert cand["metadata"] == {
        "measurement_door": {
            "type": "weight",
            "since": (now - timedelta(days=8)).date().isoformat(),
            "until": now.date().isoformat(),
        },
        # bu-ep4ks.9 adoption: event_window mirrors since/until (min/max of
        # `readings`) at full datetime precision, deterministic from `now`.
        "event_window": {
            "start": (now - timedelta(days=8)).isoformat(),
            "end": now.isoformat(),
        },
    }
    assert voice_lint_passes(cand["message"])


async def test_environment_correlation_submits_via_mcp_tool():
    """Adverse HA environment readings co-occurring with short sleep submit a candidate."""
    from datetime import UTC, datetime, timedelta

    from butlers.api.briefing.lint import voice_lint_passes

    now = datetime.now(UTC)
    day1 = now - timedelta(days=2)
    day2 = now - timedelta(days=3)

    # 5h sleep (< _ENV_SLEEP_SHORT_HOURS) on two distinct days.
    sleep = [
        {"valid_at": day1, "duration_ms": "18000000"},
        {"valid_at": day2, "duration_ms": "18000000"},
    ]

    async def reader():
        return [
            {"captured_at": day1, "metric": "temperature", "adverse": True},
            {"captured_at": day2, "metric": "temperature", "adverse": True},
        ]

    pool = _scan_pool(sleep=sleep)
    captured, fake = _capture_proposals()

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=fake,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool, reader)
    _assert_drained(pool)

    env = [c for c in captured if c["category"] == "correlation-environment"]
    assert len(env) == 1
    cand = env[0]
    assert cand["priority"] == 50
    assert cand["dedup_key"].startswith("health:correlation-env:temperature:")
    assert "bedroom temperature" in cand["message"]
    assert "metadata" not in cand
    assert voice_lint_passes(cand["message"])


async def test_environment_correlation_skipped_without_reader():
    """With no HA environment reader wired, the environment section never runs.

    No sleep or environment-symptom reads are queued, so a scan that entered the
    section would overrun the strict queue and raise.
    """
    pool = _scan_pool()
    captured, fake = _capture_proposals()

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=fake,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool)  # no reader
    _assert_drained(pool)

    assert not [c for c in captured if c["category"] == "correlation-environment"]


async def test_environment_correlation_below_min_days_no_candidate():
    """A single overlapping day is below the threshold and produces no candidate."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    day1 = now - timedelta(days=2)

    sleep = [{"valid_at": day1, "duration_ms": "18000000"}]

    async def reader():
        return [{"captured_at": day1, "metric": "temperature", "adverse": True}]

    pool = _scan_pool(sleep=sleep)
    captured, fake = _capture_proposals()

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        side_effect=fake,
    ):
        health_jobs = load_roster_jobs("health")
        await health_jobs.run_insight_scan(pool, reader)
    _assert_drained(pool)

    assert not [c for c in captured if c["category"] == "correlation-environment"]

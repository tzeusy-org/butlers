"""Tests for the deferred retention pruning sweeps (bu-2nlt4).

Covers all four pruners from src/butlers/jobs/retention.py:
  [A] prune_session_process_logs
  [B] prune_filtered_events_partitions
  [C] prune_insight_candidates
  [D] prune_secret_probe_log

Each pruner is tested for:
  (a) disabled-by-default: no DB calls when enabled=False
  (b) dry-run: counts but does not delete
  (c) enabled+confirm: deletes only beyond-TTL rows (or drops correct partitions)
  (d) edge: zero candidates, batch_limit honoured, validation errors

Also verifies:
  (e) all four pruner jobs are registered in the deterministic schedule job registry
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.jobs.retention import (
    prune_filtered_events_partitions,
    prune_insight_candidates,
    prune_secret_probe_log,
    prune_session_process_logs,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# All four pruners are disabled-by-default: enabled=False and zero DB calls.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pruner,kwargs,zero_result_keys",
    [
        (prune_session_process_logs, {"schema": "general"}, ["candidates", "deleted"]),
        (prune_filtered_events_partitions, {}, ["partitions_eligible", "partitions_dropped"]),
        (prune_insight_candidates, {}, ["candidates", "deleted"]),
        (prune_secret_probe_log, {}, ["candidates", "deleted"]),
    ],
    ids=["session-logs", "filtered-partitions", "insight-candidates", "secret-probe-log"],
)
async def test_pruner_disabled_by_default_no_db_calls(pruner, kwargs, zero_result_keys):
    """A pruner must not touch the DB when enabled=False (the default)."""
    pool = _make_pool()
    result = await pruner(pool, **kwargs)
    assert result["enabled"] is False
    for key in zero_result_keys:
        assert result[key] in (0, []), f"{key}={result[key]!r} should be empty/zero when disabled"
    pool.fetchrow.assert_not_called()
    pool.fetch.assert_not_called()
    pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Pool fakes
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    fetchrow_result: dict[str, Any] | None = None,
    fetch_result: list[dict[str, Any]] | None = None,
    execute_result: str = "DELETE 0",
) -> MagicMock:
    """Build a minimal mock asyncpg pool."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    pool.fetch = AsyncMock(return_value=fetch_result or [])
    pool.execute = AsyncMock(return_value=execute_result)
    return pool


# ===========================================================================
# [A] prune_session_process_logs
# ===========================================================================


class TestPruneSessionProcessLogs:
    async def test_dry_run_counts_but_does_not_delete(self):
        """With enabled=True, dry_run=True: count is returned but no DELETE issued."""
        pool = _make_pool(fetchrow_result={"n": 7}, execute_result="DELETE 0")
        result = await prune_session_process_logs(
            pool, schema="general", enabled=True, dry_run=True
        )
        assert result["enabled"] is True
        assert result["dry_run"] is True
        assert result["candidates"] == 7
        assert result["deleted"] == 0
        # COUNT query fired, but no DELETE
        pool.fetchrow.assert_called_once()
        pool.execute.assert_not_called()

    async def test_enabled_and_confirmed_deletes_rows(self):
        """With enabled=True, dry_run=False: DELETE is executed and count returned."""
        pool = _make_pool(fetchrow_result={"n": 3}, execute_result="DELETE 3")
        result = await prune_session_process_logs(
            pool, schema="general", enabled=True, dry_run=False, batch_limit=500
        )
        assert result["enabled"] is True
        assert result["dry_run"] is False
        assert result["candidates"] == 3
        assert result["deleted"] == 3
        pool.execute.assert_called_once()
        # Verify the SQL targets the correct schema
        call_sql: str = pool.execute.call_args[0][0]
        assert "general.session_process_logs" in call_sql
        assert "LIMIT" in call_sql

    async def test_zero_candidates_no_delete_issued(self):
        """When no rows are eligible, no DELETE is issued even when enabled."""
        pool = _make_pool(fetchrow_result={"n": 0})
        result = await prune_session_process_logs(
            pool, schema="health", enabled=True, dry_run=False
        )
        assert result["candidates"] == 0
        assert result["deleted"] == 0
        pool.execute.assert_not_called()


# ===========================================================================
# [B] prune_filtered_events_partitions
# ===========================================================================


class TestPruneFilteredEventsPartitions:
    async def test_dry_run_lists_eligible_without_dropping(self):
        """With enabled=True, dry_run=True: eligible list returned, no DROP issued."""
        # Simulate partitions: current month 2026-06, keep_months=12 → cutoff = 2025-06
        # Partitions from 2024-12 and 2025-01 are old enough to drop.
        pool = _make_pool(
            fetch_result=[
                {"table_name": "filtered_events_202412"},
                {"table_name": "filtered_events_202501"},
                {"table_name": "filtered_events_202601"},  # within window
                {"table_name": "filtered_events_202606"},  # current month
            ]
        )
        result = await prune_filtered_events_partitions(
            pool, enabled=True, dry_run=True, keep_months=12
        )
        assert result["enabled"] is True
        assert result["dry_run"] is True
        assert "filtered_events_202412" in result["partitions_eligible"]
        assert "filtered_events_202501" in result["partitions_eligible"]
        assert "filtered_events_202601" not in result["partitions_eligible"]
        assert result["partitions_dropped"] == []
        pool.execute.assert_not_called()

    async def test_enabled_and_confirmed_drops_eligible_partitions(self):
        """With enabled=True, dry_run=False: eligible partitions are DROPped."""
        # Today is 2026-06; keep_months=12 → retain 2025-07 through 2026-06.
        # 202412 (Dec 2024) is 18 months old → eligible to drop.
        # 202601 (Jan 2026) is 5 months old → within keep window, NOT dropped.
        pool = _make_pool(
            fetch_result=[
                {"table_name": "filtered_events_202412"},
                {"table_name": "filtered_events_202601"},  # within window (Jan 2026)
            ]
        )
        pool.execute = AsyncMock(return_value="")
        result = await prune_filtered_events_partitions(
            pool, enabled=True, dry_run=False, keep_months=12
        )
        assert result["enabled"] is True
        assert result["dry_run"] is False
        assert "filtered_events_202412" in result["partitions_dropped"]
        assert "filtered_events_202601" not in result["partitions_dropped"]
        pool.execute.assert_called_once()

    async def test_no_eligible_partitions_returns_empty(self):
        """No eligible partitions → nothing dropped, no DROP executed."""
        pool = _make_pool(
            fetch_result=[
                {"table_name": "filtered_events_202606"},
                {"table_name": "filtered_events_202605"},
            ]
        )
        result = await prune_filtered_events_partitions(
            pool, enabled=True, dry_run=False, keep_months=12
        )
        assert result["partitions_eligible"] == []
        assert result["partitions_dropped"] == []
        pool.execute.assert_not_called()

    async def test_invalid_keep_months_raises(self):
        """keep_months < 1 should raise ValueError."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="keep_months"):
            await prune_filtered_events_partitions(pool, enabled=True, keep_months=0)

    async def test_non_yyyymm_table_names_are_skipped(self):
        """Tables with non-YYYYMM suffixes are not treated as eligible partitions."""
        pool = _make_pool(
            fetch_result=[
                {"table_name": "filtered_events_old"},
                {"table_name": "filtered_events_backup_202401"},
                {"table_name": "filtered_events_202412"},
            ]
        )
        result = await prune_filtered_events_partitions(
            pool, enabled=True, dry_run=True, keep_months=12
        )
        names = result["partitions_eligible"]
        assert "filtered_events_old" not in names
        assert "filtered_events_backup_202401" not in names
        assert "filtered_events_202412" in names


# ===========================================================================
# [C] prune_insight_candidates
# ===========================================================================


class TestPruneInsightCandidates:
    async def test_dry_run_counts_but_does_not_delete(self):
        """With enabled=True, dry_run=True: count returned but no DELETE issued."""
        pool = _make_pool(fetchrow_result={"n": 12})
        result = await prune_insight_candidates(pool, enabled=True, dry_run=True, ttl_days=90)
        assert result["enabled"] is True
        assert result["dry_run"] is True
        assert result["candidates"] == 12
        assert result["deleted"] == 0
        pool.fetchrow.assert_called_once()
        pool.execute.assert_not_called()

    async def test_enabled_and_confirmed_deletes_terminal_rows(self):
        """With enabled=True, dry_run=False: DELETE issued for non-pending old rows."""
        pool = _make_pool(fetchrow_result={"n": 5}, execute_result="DELETE 5")
        result = await prune_insight_candidates(
            pool, enabled=True, dry_run=False, ttl_days=90, batch_limit=500
        )
        assert result["deleted"] == 5
        pool.execute.assert_called_once()
        call_sql: str = pool.execute.call_args[0][0]
        assert "public.insight_candidates" in call_sql
        assert "status <> 'pending'" in call_sql
        assert "LIMIT" in call_sql

    async def test_zero_candidates_no_delete(self):
        """No eligible rows → no DELETE executed."""
        pool = _make_pool(fetchrow_result={"n": 0})
        result = await prune_insight_candidates(pool, enabled=True, dry_run=False, ttl_days=90)
        assert result["candidates"] == 0
        assert result["deleted"] == 0
        pool.execute.assert_not_called()

    async def test_cutoff_timestamp_uses_ttl_days(self):
        """The cutoff timestamp passed to the DB is roughly ttl_days before now."""
        pool = _make_pool(fetchrow_result={"n": 0})
        before = datetime.now(UTC)
        await prune_insight_candidates(pool, enabled=True, dry_run=True, ttl_days=90)
        after = datetime.now(UTC)

        call_args = pool.fetchrow.call_args[0]
        # Second positional arg is the cutoff datetime
        cutoff: datetime = call_args[1]
        expected_low = before - timedelta(days=90)
        expected_high = after - timedelta(days=90)
        assert expected_low <= cutoff <= expected_high


# ===========================================================================
# [D] prune_secret_probe_log
# ===========================================================================


class TestPruneSecretProbeLog:
    async def test_dry_run_counts_but_does_not_delete(self):
        """With enabled=True, dry_run=True: count returned but no DELETE issued."""
        pool = _make_pool(fetchrow_result={"n": 30})
        result = await prune_secret_probe_log(pool, enabled=True, dry_run=True, ttl_days=90)
        assert result["enabled"] is True
        assert result["dry_run"] is True
        assert result["candidates"] == 30
        assert result["deleted"] == 0
        pool.fetchrow.assert_called_once()
        pool.execute.assert_not_called()

    async def test_enabled_and_confirmed_deletes_old_rows(self):
        """With enabled=True, dry_run=False: DELETE issued for rows older than ttl_days."""
        pool = _make_pool(fetchrow_result={"n": 10}, execute_result="DELETE 10")
        result = await prune_secret_probe_log(
            pool, enabled=True, dry_run=False, ttl_days=90, batch_limit=500
        )
        assert result["deleted"] == 10
        pool.execute.assert_called_once()
        call_sql: str = pool.execute.call_args[0][0]
        assert "public.secret_probe_log" in call_sql
        assert "LIMIT" in call_sql

    async def test_zero_candidates_no_delete(self):
        """No eligible rows → no DELETE executed."""
        pool = _make_pool(fetchrow_result={"n": 0})
        result = await prune_secret_probe_log(pool, enabled=True, dry_run=False, ttl_days=90)
        assert result["candidates"] == 0
        assert result["deleted"] == 0
        pool.execute.assert_not_called()

    async def test_ttl_days_below_90_raises(self):
        """ttl_days < 90 must raise ValueError to honour the spec minimum."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="≥ 90"):
            await prune_secret_probe_log(pool, enabled=True, ttl_days=89)

    async def test_ttl_days_exactly_90_is_allowed(self):
        """ttl_days == 90 is the spec minimum and must not raise."""
        pool = _make_pool(fetchrow_result={"n": 0})
        result = await prune_secret_probe_log(pool, enabled=True, dry_run=True, ttl_days=90)
        assert result["enabled"] is True


# ===========================================================================
# Registry wiring
# ===========================================================================


class TestRetentionJobRegistry:
    """Verify that all pruner jobs are registered in the deterministic job registry."""

    def test_all_pruner_jobs_registered_in_general_butler(self):
        """All four pruner job names must appear in the 'general' butler's registry."""
        from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

        registry = get_deterministic_schedule_job_registry()
        general_jobs = registry.get("general", {})
        expected = {
            "session_process_logs_prune",
            "filtered_events_partition_prune",
            "insight_candidates_prune",
            "secret_probe_log_prune",
        }
        missing = expected - set(general_jobs.keys())
        assert not missing, f"Missing pruner jobs in 'general' registry: {missing}"

    def test_session_process_logs_prune_available_in_each_butler(self):
        """Per-butler session log pruner must be registered for each butler schema."""
        from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

        registry = get_deterministic_schedule_job_registry()
        butlers_with_session_tables = {
            "general",
            "health",
            "finance",
            "relationship",
            "travel",
            "education",
            "home",
            "lifestyle",
            "switchboard",
            "qa",
        }
        for butler in butlers_with_session_tables:
            jobs = registry.get(butler, {})
            assert "session_process_logs_prune" in jobs, (
                f"session_process_logs_prune not registered for butler={butler!r}"
            )

    @pytest.mark.parametrize(
        "job_name",
        [
            "session_process_logs_prune",
            "insight_candidates_prune",
            "secret_probe_log_prune",
            "filtered_events_partition_prune",
        ],
    )
    async def test_prune_job_defaults_to_disabled(self, job_name):
        """Each registered pruner wrapper, invoked with no job_args, returns enabled=False
        and issues no DB calls."""
        from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

        registry = get_deterministic_schedule_job_registry()
        handler = registry["general"][job_name]
        pool = _make_pool()
        result = await handler(pool, None)
        assert result["enabled"] is False
        pool.fetchrow.assert_not_called()
        pool.fetch.assert_not_called()
        pool.execute.assert_not_called()

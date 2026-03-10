"""Tests for consolidation runner and episode cleanup in Memory Butler."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load consolidation module from disk (roster/ is not a Python package).
# Mock sentence_transformers before loading to avoid heavy dependency.
# ---------------------------------------------------------------------------

_CONSOLIDATION_PATH = MEMORY_MODULE_PATH / "consolidation.py"


def _load_consolidation_module():
    spec = importlib.util.spec_from_file_location("consolidation", _CONSOLIDATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_consolidation_module()
run_consolidation = _mod.run_consolidation
run_episode_cleanup = _mod.run_episode_cleanup

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _episode_row(
    *,
    butler: str = "test-butler",
    content: str = "something happened",
    days_ago: float = 1.0,
    consolidation_attempts: int = 0,
    tenant_id: str = "owner",
) -> dict:
    """Build a dict mimicking an asyncpg Record for a pending episode."""
    return {
        "id": uuid.uuid4(),
        "butler": butler,
        "content": content,
        "importance": 5.0,
        "metadata": "{}",
        "created_at": datetime.now(UTC) - timedelta(days=days_ago),
        "tenant_id": tenant_id,
        "consolidation_attempts": consolidation_attempts,
    }


def _mock_pool_with_acquire(rows: list[dict] | None = None) -> tuple[MagicMock, MagicMock]:
    """Create a pool mock that supports the pool.acquire() async context manager.

    Returns:
        (pool, mock_conn) — pool with .acquire() wired up, and the connection mock.
    """
    rows = rows or []

    mock_transaction = MagicMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=None)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(return_value=rows)
    mock_conn.execute = AsyncMock(return_value="UPDATE 0")
    mock_conn.transaction = MagicMock(return_value=mock_transaction)

    mock_acquire_cm = MagicMock()
    mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire_cm.__aexit__ = AsyncMock(return_value=None)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=mock_acquire_cm)
    pool.execute = AsyncMock(return_value="UPDATE 0")
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)

    return pool, mock_conn


# ---------------------------------------------------------------------------
# Tests — run_consolidation
# ---------------------------------------------------------------------------


class TestRunConsolidation:
    """Tests for run_consolidation()."""

    async def test_fetches_pending_episodes_with_skip_locked(self) -> None:
        """run_consolidation uses FOR UPDATE SKIP LOCKED on pending episodes."""
        pool, mock_conn = _mock_pool_with_acquire(rows=[])
        engine = MagicMock()

        await run_consolidation(pool, engine)

        # The claim query runs inside pool.acquire() context
        mock_conn.fetch.assert_awaited_once()
        sql = mock_conn.fetch.call_args[0][0]
        assert "consolidation_status = 'pending'" in sql
        assert "FOR UPDATE SKIP LOCKED" in sql
        assert "ORDER BY tenant_id, butler, created_at, id" in sql

    async def test_lease_is_set_on_claimed_episodes(self) -> None:
        """Claimed episodes have leased_until and leased_by set."""
        rows = [_episode_row(butler="test-butler")]
        pool, mock_conn = _mock_pool_with_acquire(rows=rows)
        engine = MagicMock()

        await run_consolidation(pool, engine)

        # The lease UPDATE runs inside the transaction
        mock_conn.execute.assert_awaited_once()
        sql = mock_conn.execute.call_args[0][0]
        assert "leased_until" in sql
        assert "leased_by" in sql

    async def test_groups_episodes_by_source_butler(self) -> None:
        """Episodes from different butlers are grouped separately (same tenant)."""
        rows = [
            _episode_row(butler="alpha", content="a1"),
            _episode_row(butler="alpha", content="a2"),
            _episode_row(butler="beta", content="b1"),
        ]
        pool, mock_conn = _mock_pool_with_acquire(rows=rows)
        engine = MagicMock()

        result = await run_consolidation(pool, engine)

        assert result["groups"]["owner/alpha"] == 2
        assert result["groups"]["owner/beta"] == 1

    async def test_groups_episodes_by_tenant_and_butler(self) -> None:
        """Episodes from different tenants with the same butler name are NOT mixed."""
        rows = [
            _episode_row(butler="alpha", tenant_id="tenant-a", content="a1"),
            _episode_row(butler="alpha", tenant_id="tenant-a", content="a2"),
            _episode_row(butler="alpha", tenant_id="tenant-b", content="b1"),
        ]
        pool, mock_conn = _mock_pool_with_acquire(rows=rows)
        engine = MagicMock()

        result = await run_consolidation(pool, engine)

        # Same butler name but different tenants → two distinct groups
        assert result["butlers_processed"] == 2
        assert result["groups"]["tenant-a/alpha"] == 2
        assert result["groups"]["tenant-b/alpha"] == 1

    async def test_returns_correct_stats(self) -> None:
        """Stats dict has the right shape and values."""
        rows = [
            _episode_row(butler="alpha"),
            _episode_row(butler="alpha"),
            _episode_row(butler="beta"),
            _episode_row(butler="gamma"),
        ]
        pool, mock_conn = _mock_pool_with_acquire(rows=rows)
        engine = MagicMock()

        result = await run_consolidation(pool, engine)

        assert result["episodes_processed"] == 4
        assert result["butlers_processed"] == 3
        assert result["groups"] == {"owner/alpha": 2, "owner/beta": 1, "owner/gamma": 1}

    async def test_handles_no_pending_episodes(self) -> None:
        """When there are no pending episodes, return zeros."""
        pool, mock_conn = _mock_pool_with_acquire(rows=[])
        engine = MagicMock()

        result = await run_consolidation(pool, engine)

        assert result["episodes_processed"] == 0
        assert result["butlers_processed"] == 0
        assert result["groups"] == {}
        assert result["groups_consolidated"] == 0
        assert result["facts_created"] == 0
        assert result["episodes_consolidated"] == 0
        # No lease UPDATE when no episodes claimed
        mock_conn.execute.assert_not_awaited()

    async def test_without_spawner_returns_grouping_stats_only(self) -> None:
        """When cc_spawner is None, only grouping stats are returned."""
        rows = [
            _episode_row(butler="alpha"),
            _episode_row(butler="beta"),
        ]
        pool, mock_conn = _mock_pool_with_acquire(rows=rows)
        engine = MagicMock()

        result = await run_consolidation(pool, engine, cc_spawner=None)

        assert result["episodes_processed"] == 2
        assert result["butlers_processed"] == 2
        assert result["groups_consolidated"] == 0
        assert result["facts_created"] == 0
        # Leases ARE set even without spawner (episodes are claimed)
        mock_conn.execute.assert_awaited_once()

    async def test_orchestrates_full_pipeline_with_spawner(self) -> None:
        """With cc_spawner, run_consolidation orchestrates the full pipeline."""
        episodes = [_episode_row(butler="test-butler", content="test content")]
        pool, mock_conn = _mock_pool_with_acquire(rows=episodes)
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # Existing facts query
                [],  # Existing rules query
            ]
        )
        pool.execute = AsyncMock(return_value="UPDATE 1")

        engine = MagicMock()

        spawner = AsyncMock()
        spawner.trigger = AsyncMock(
            return_value=MagicMock(
                success=True,
                output='{"new_facts": [{"subject": "test", "predicate": "is", '
                '"content": "example", "permanence": "standard"}], '
                '"updated_facts": [], "new_rules": [], "confirmations": []}',
            )
        )

        with patch.dict(
            __import__("sys").modules,
            {"sentence_transformers": MagicMock()},
        ):
            result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["episodes_processed"] == 1
        assert result["butlers_processed"] == 1
        spawner.trigger.assert_awaited_once()
        call_kwargs = spawner.trigger.call_args[1]
        assert "prompt" in call_kwargs
        assert "test content" in call_kwargs["prompt"]

    async def test_partial_failure_does_not_block_other_groups(self) -> None:
        """When one butler group fails, others continue processing."""
        rows = [
            _episode_row(butler="alpha"),
            _episode_row(butler="beta"),
        ]
        pool, mock_conn = _mock_pool_with_acquire(rows=rows)
        # facts for alpha raises; beta proceeds
        pool.fetch = AsyncMock(
            side_effect=[
                Exception("Database error for alpha"),
                [],  # facts for beta
                [],  # rules for beta
            ]
        )
        pool.execute = AsyncMock(return_value="UPDATE 1")

        spawner = AsyncMock()
        spawner.trigger = AsyncMock(
            return_value=MagicMock(
                success=True,
                output='{"new_facts": [], "updated_facts": [], "new_rules": [], '
                '"confirmations": []}',
            )
        )
        engine = MagicMock()

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["butlers_processed"] == 2
        assert result["groups_consolidated"] == 1  # Only beta succeeded
        assert len(result["errors"]) > 0
        assert "alpha" in result["errors"][0]

    async def test_cc_failure_is_reported_in_errors(self) -> None:
        """When runtime session fails, error is captured in stats (sanitized)."""
        episodes = [_episode_row(butler="test-butler")]
        pool, mock_conn = _mock_pool_with_acquire(rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules for dedup
        pool.execute = AsyncMock(return_value="UPDATE 1")
        engine = MagicMock()

        spawner = AsyncMock()
        spawner.trigger = AsyncMock(
            return_value=MagicMock(
                success=False,
                output=None,
                error="CC timeout",
            )
        )

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["groups_consolidated"] == 0
        assert len(result["errors"]) > 0
        # Error message should mention failure reason
        assert "runtime session failed" in result["errors"][0]
        assert "test-butler" in result["errors"][0]

    async def test_episodes_marked_consolidated_only_after_success(self) -> None:
        """Episodes are marked consolidated=true only after execute_consolidation."""
        episodes = [_episode_row(butler="test-butler")]
        pool, mock_conn = _mock_pool_with_acquire(rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules for dedup
        pool.execute = AsyncMock(return_value="UPDATE 1")
        engine = MagicMock()

        spawner = AsyncMock()
        spawner.trigger = AsyncMock(
            return_value=MagicMock(
                success=True,
                output='{"new_facts": [], "updated_facts": [], "new_rules": [], '
                '"confirmations": []}',
            )
        )

        await run_consolidation(pool, engine, cc_spawner=spawner)

        # pool.execute (not conn.execute) should be called for marking consolidated
        update_calls = [
            call for call in pool.execute.call_args_list if "UPDATE episodes" in str(call)
        ]
        assert len(update_calls) > 0

    async def test_fetches_rules_using_maturity_column(self) -> None:
        """Consolidation queries rules using 'maturity' not 'status' column."""
        episodes = [_episode_row(butler="test-butler")]
        pool, mock_conn = _mock_pool_with_acquire(rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, then rules
        pool.execute = AsyncMock(return_value="UPDATE 1")
        engine = MagicMock()

        spawner = AsyncMock()
        spawner.trigger = AsyncMock(
            return_value=MagicMock(
                success=True,
                output='{"new_facts": [], "updated_facts": [], "new_rules": [], '
                '"confirmations": []}',
            )
        )

        await run_consolidation(pool, engine, cc_spawner=spawner)

        # The second fetch call (index 1) is the rules query
        all_fetches = pool.fetch.await_args_list
        assert len(all_fetches) >= 2
        rules_sql = all_fetches[1][0][0]  # Second pool.fetch = rules query
        # Must use 'maturity' column, NOT 'status'
        assert "maturity" in rules_sql
        assert "maturity = 'active'" not in rules_sql  # old bad query
        assert "anti_pattern" in rules_sql  # correct exclusion pattern

    async def test_batch_size_passed_to_claim_query(self) -> None:
        """The LIMIT in the claim query reflects the batch_size parameter."""
        pool, mock_conn = _mock_pool_with_acquire(rows=[])
        engine = MagicMock()

        await run_consolidation(pool, engine, batch_size=42)

        sql_args = mock_conn.fetch.call_args[0]
        # Second positional arg is the $1 parameter (batch_size)
        assert sql_args[1] == 42


# ---------------------------------------------------------------------------
# Tests — run_episode_cleanup
# ---------------------------------------------------------------------------


class TestRunEpisodeCleanup:
    """Tests for run_episode_cleanup()."""

    async def test_deletes_expired_episodes(self) -> None:
        """Expired episodes are deleted via DELETE WHERE expires_at < now()."""
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="DELETE 5")
        pool.fetchval = AsyncMock(return_value=100)

        result = await run_episode_cleanup(pool)

        expire_sql = pool.execute.call_args_list[0][0][0]
        assert "expires_at < now()" in expire_sql
        assert result["expired_deleted"] == 5

    async def test_enforces_capacity_limit(self) -> None:
        """When remaining > max_entries, oldest consolidated episodes are deleted."""
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 50"])
        pool.fetchval = AsyncMock(return_value=150)

        result = await run_episode_cleanup(pool, max_entries=100)

        assert result["capacity_deleted"] == 50
        cap_sql = pool.execute.call_args_list[1][0][0]
        assert "consolidated = true" in cap_sql
        assert "ORDER BY created_at ASC" in cap_sql
        cap_param = pool.execute.call_args_list[1][0][1]
        assert cap_param == 50

    async def test_protects_unconsolidated_episodes(self) -> None:
        """Capacity cleanup only deletes consolidated episodes, never unconsolidated."""
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 10"])
        pool.fetchval = AsyncMock(return_value=200)

        await run_episode_cleanup(pool, max_entries=100)

        cap_sql = pool.execute.call_args_list[1][0][0]
        assert "consolidated = true" in cap_sql
        assert "consolidated = false" not in cap_sql

    async def test_handles_no_episodes_to_delete(self) -> None:
        """When nothing is expired and count is within limits, return zeros."""
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="DELETE 0")
        pool.fetchval = AsyncMock(return_value=50)

        result = await run_episode_cleanup(pool, max_entries=10000)

        assert result["expired_deleted"] == 0
        assert result["capacity_deleted"] == 0
        assert result["remaining"] == 50

    async def test_capacity_check_only_when_over_limit(self) -> None:
        """When remaining <= max_entries after expiry, no capacity delete runs."""
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="DELETE 3")
        pool.fetchval = AsyncMock(return_value=50)

        result = await run_episode_cleanup(pool, max_entries=100)

        assert result["expired_deleted"] == 3
        assert result["capacity_deleted"] == 0
        assert result["remaining"] == 50
        assert pool.execute.await_count == 1

    async def test_remaining_reflects_capacity_deletion(self) -> None:
        """The remaining count is adjusted after capacity deletion."""
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 20"])
        pool.fetchval = AsyncMock(return_value=120)

        result = await run_episode_cleanup(pool, max_entries=100)

        assert result["remaining"] == 100  # 120 - 20


# ---------------------------------------------------------------------------
# Tests — lease-based concurrency
# ---------------------------------------------------------------------------


class TestLeaseBasedClaiming:
    """Tests for the FOR UPDATE SKIP LOCKED lease mechanism."""

    async def test_lease_columns_set_after_claiming(self) -> None:
        """After claiming, episodes have leased_until and leased_by set."""
        episodes = [_episode_row(butler="test-butler")]
        pool, mock_conn = _mock_pool_with_acquire(rows=episodes)
        engine = MagicMock()

        await run_consolidation(pool, engine)

        # The UPDATE inside the transaction sets the lease
        mock_conn.execute.assert_awaited_once()
        sql = mock_conn.execute.call_args[0][0]
        assert "leased_until" in sql
        assert "leased_by" in sql
        assert "ANY(" in sql  # batch update by ID list

    async def test_expired_leases_are_claimable(self) -> None:
        """The claim query filters for expired (or absent) leases."""
        pool, mock_conn = _mock_pool_with_acquire(rows=[])
        engine = MagicMock()

        await run_consolidation(pool, engine)

        sql = mock_conn.fetch.call_args[0][0]
        # The WHERE clause must allow episodes whose lease has expired
        assert "leased_until IS NULL OR leased_until < now()" in sql

    async def test_retry_delay_is_respected(self) -> None:
        """The claim query skips episodes scheduled for future retry."""
        pool, mock_conn = _mock_pool_with_acquire(rows=[])
        engine = MagicMock()

        await run_consolidation(pool, engine)

        sql = mock_conn.fetch.call_args[0][0]
        # Episodes with future next_consolidation_retry_at must be skipped
        assert "next_consolidation_retry_at IS NULL" in sql
        assert "next_consolidation_retry_at <= now()" in sql


# ---------------------------------------------------------------------------
# Tests — failure and dead-letter state machine
# ---------------------------------------------------------------------------


class TestFailureStateMachine:
    """Tests for failed and dead_letter state transitions."""

    async def test_cc_failure_calls_mark_group_failed(self) -> None:
        """When CC spawner fails, _mark_group_failed updates episode status."""
        episodes = [_episode_row(butler="test-butler")]
        pool, mock_conn = _mock_pool_with_acquire(rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules
        pool.execute = AsyncMock(return_value="UPDATE 1")
        engine = MagicMock()

        spawner = AsyncMock()
        spawner.trigger = AsyncMock(
            return_value=MagicMock(success=False, output=None, error="timeout")
        )

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        # pool.execute should be called for _mark_group_failed (UPDATE + INSERT events)
        assert pool.execute.await_count >= 1
        # Verify UPDATE sets consolidation_attempts and clears lease
        update_sql = pool.execute.call_args_list[0][0][0]
        assert "consolidation_attempts" in update_sql
        assert "leased_until" in update_sql
        assert "failed" in update_sql or "dead_letter" in update_sql

        assert result["groups_consolidated"] == 0
        assert len(result["errors"]) > 0


# ---------------------------------------------------------------------------
# Tests — tenant isolation correctness
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    """Tests that consolidation groups correctly by (tenant_id, butler)."""

    async def test_same_butler_different_tenants_are_separate_groups(self) -> None:
        """Two tenants with the same butler name produce two distinct groups."""
        rows = [
            _episode_row(butler="news", tenant_id="alice", content="alice content"),
            _episode_row(butler="news", tenant_id="bob", content="bob content"),
        ]
        pool, mock_conn = _mock_pool_with_acquire(rows=rows)
        engine = MagicMock()

        result = await run_consolidation(pool, engine)

        assert result["butlers_processed"] == 2
        assert "alice/news" in result["groups"]
        assert "bob/news" in result["groups"]

    async def test_execute_consolidation_receives_tenant_id(self) -> None:
        """execute_consolidation is called with the episode's tenant_id."""
        import unittest.mock

        episodes = [_episode_row(butler="test-butler", tenant_id="acme")]
        pool, mock_conn = _mock_pool_with_acquire(rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules
        pool.execute = AsyncMock(return_value="UPDATE 1")
        engine = MagicMock()

        spawner = AsyncMock()
        spawner.trigger = AsyncMock(
            return_value=MagicMock(
                success=True,
                output='{"new_facts": [], "updated_facts": [], "new_rules": [], '
                '"confirmations": []}',
            )
        )

        captured_kwargs: dict = {}

        async def _capture_execute(**kwargs):
            captured_kwargs.update(kwargs)
            return {
                "facts_created": 0,
                "facts_updated": 0,
                "rules_created": 0,
                "confirmations_made": 0,
                "episodes_consolidated": 0,
                "episode_ttl_days": 7,
                "errors": [],
            }

        with unittest.mock.patch.object(
            _mod, "execute_consolidation", side_effect=_capture_execute
        ):
            await run_consolidation(pool, engine, cc_spawner=spawner)

        assert captured_kwargs.get("tenant_id") == "acme"
        assert "request_id" in captured_kwargs  # auto-generated UUID string

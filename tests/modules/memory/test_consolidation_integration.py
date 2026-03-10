"""Integration tests for the end-to-end consolidation pipeline.

Tests verify the complete flow: run_consolidation() → prompt → CC → parse → execute,
including retry/failure scenarios, consolidation_status transitions, and episode
cleanup with the updated schema.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load consolidation module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_CONSOLIDATION_PATH = MEMORY_MODULE_PATH / "consolidation.py"
_EXECUTOR_PATH = MEMORY_MODULE_PATH / "consolidation_executor.py"


def _load_consolidation_module():
    spec = importlib.util.spec_from_file_location("consolidation", _CONSOLIDATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_executor_module():
    spec = importlib.util.spec_from_file_location("consolidation_executor", _EXECUTOR_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_cons_mod = _load_consolidation_module()
_exec_mod = _load_executor_module()

run_consolidation = _cons_mod.run_consolidation
run_episode_cleanup = _cons_mod.run_episode_cleanup
execute_consolidation = _exec_mod.execute_consolidation

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _episode_row(
    *,
    butler: str = "test-butler",
    content: str = "something happened",
    days_ago: float = 1.0,
    consolidation_status: str = "pending",
    consolidation_attempts: int = 0,
    last_consolidation_error: str | None = None,
    tenant_id: str = "owner",
) -> dict:
    """Build a dict mimicking an asyncpg Record for an episode."""
    return {
        "id": uuid.uuid4(),
        "butler": butler,
        "content": content,
        "importance": 5.0,
        "metadata": "{}",
        "created_at": datetime.now(UTC) - timedelta(days=days_ago),
        "consolidated": consolidation_status == "consolidated",
        "consolidation_status": consolidation_status,
        "consolidation_attempts": consolidation_attempts,
        "last_consolidation_error": last_consolidation_error,
        "tenant_id": tenant_id,
    }


def _mock_spawner_success(output: str | None = None) -> AsyncMock:
    """Create a mock spawner that returns success with optional JSON output."""
    if output is None:
        output = '{"new_facts": [], "updated_facts": [], "new_rules": [], "confirmations": []}'

    spawner = AsyncMock()
    spawner.trigger = AsyncMock(
        return_value=MagicMock(
            success=True,
            output=output,
            error=None,
        )
    )
    return spawner


def _mock_spawner_failure(error: str = "CC timeout") -> AsyncMock:
    """Create a mock spawner that returns failure."""
    spawner = AsyncMock()
    spawner.trigger = AsyncMock(
        return_value=MagicMock(
            success=False,
            output=None,
            error=error,
        )
    )
    return spawner


def _mock_pool_with_connection(
    claim_rows: list[dict] | None = None,
) -> tuple[AsyncMock, MagicMock]:
    """Create a mock pool with properly configured acquire() context manager.

    The pool.acquire() context manager returns a connection that handles
    the lease claim transaction (conn.fetch + conn.execute inside
    conn.transaction()).

    Returns:
        (pool, mock_conn)
    """
    from unittest.mock import MagicMock as SyncMock

    claim_rows = claim_rows or []

    # Mock transaction context manager
    mock_transaction = SyncMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=None)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)

    # Mock connection
    mock_conn = SyncMock()
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock(return_value="UPDATE 1")
    mock_conn.fetch = AsyncMock(return_value=claim_rows)
    mock_conn.transaction = SyncMock(return_value=mock_transaction)

    # Mock pool.acquire()
    mock_acquire_cm = SyncMock()
    mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire_cm.__aexit__ = AsyncMock(return_value=None)

    pool = AsyncMock()
    pool.acquire = SyncMock(return_value=mock_acquire_cm)
    pool.execute = AsyncMock(return_value="UPDATE 1")
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)

    return pool, mock_conn


# ---------------------------------------------------------------------------
# Integration Tests — End-to-End Consolidation Pipeline
# ---------------------------------------------------------------------------


class TestEndToEndConsolidation:
    """Integration tests for the complete consolidation pipeline."""

    async def test_successful_end_to_end_consolidation_with_valid_json(self) -> None:
        """Test successful consolidation with mocked LLM CLI spawner producing valid JSON."""
        from unittest.mock import patch

        episodes = [
            _episode_row(butler="test-butler", content="User prefers dark mode"),
            _episode_row(butler="test-butler", content="User is in Berlin"),
        ]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=episodes)
        # pool.fetch is used for facts/rules dedup queries (after claiming)
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # Existing facts query
                [],  # Existing rules query
            ]
        )
        pool.execute = AsyncMock(return_value="UPDATE 2")

        engine = MagicMock()

        spawner = _mock_spawner_success(
            output='{"new_facts": [{"subject": "user", "predicate": "prefers", '
            '"content": "dark mode", "permanence": "stable", "importance": 7.0}], '
            '"updated_facts": [], "new_rules": [], "confirmations": []}'
        )

        import sys

        with patch.dict(sys.modules, {"sentence_transformers": MagicMock()}):
            result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["episodes_processed"] == 2
        assert result["butlers_processed"] == 1
        assert result["groups_consolidated"] == 1
        assert result["facts_created"] == 1

        spawner.trigger.assert_awaited_once()
        call_kwargs = spawner.trigger.call_args[1]
        assert "prompt" in call_kwargs
        assert "test-butler" in call_kwargs["prompt"]

        # Episodes must be marked consolidated (terminal state)
        assert pool.execute.await_count > 0
        # Find the consolidated UPDATE call (normalise multiline SQL for matching)
        consolidated_calls = [
            c
            for c in pool.execute.call_args_list
            if "consolidation_status" in " ".join(c[0][0].split())
            and "consolidated" in " ".join(c[0][0].split())
        ]
        assert len(consolidated_calls) >= 1

    async def test_partial_group_failure_one_butler_fails_others_succeed(self) -> None:
        """Test partial group failure — one butler group fails, others succeed."""
        episodes = [
            _episode_row(butler="alpha", content="alpha content"),
            _episode_row(butler="beta", content="beta content"),
        ]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=episodes)
        # alpha's facts fetch fails; beta proceeds
        pool.fetch = AsyncMock(
            side_effect=[
                Exception("Database error for alpha"),
                [],  # facts for beta
                [],  # rules for beta
            ]
        )
        pool.execute = AsyncMock(return_value="UPDATE 1")

        engine = MagicMock()
        spawner = _mock_spawner_success()

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["butlers_processed"] == 2
        assert result["groups_consolidated"] == 1  # Only beta
        assert len(result["errors"]) > 0
        assert any("alpha" in err for err in result["errors"])

    async def test_cc_spawner_failure_captured_in_errors(self) -> None:
        """Test that LLM CLI spawner failures are captured and don't crash the pipeline."""
        episodes = [_episode_row(butler="test-butler")]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules
        pool.execute = AsyncMock(return_value="UPDATE 1")

        engine = MagicMock()
        spawner = _mock_spawner_failure(error="Timeout waiting for CC response")

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["groups_consolidated"] == 0
        assert len(result["errors"]) > 0
        assert any("runtime session failed" in err for err in result["errors"])

        # _mark_group_failed should have been called (pool.execute for UPDATE)
        assert pool.execute.await_count >= 1
        failed_update_calls = [
            c for c in pool.execute.call_args_list if "consolidation_attempts" in str(c)
        ]
        assert len(failed_update_calls) >= 1

    async def test_multiple_butler_groups_all_succeed(self) -> None:
        """Test that multiple butler groups can all be processed successfully."""
        episodes = [
            _episode_row(butler="alpha", content="alpha episode 1"),
            _episode_row(butler="alpha", content="alpha episode 2"),
            _episode_row(butler="beta", content="beta episode 1"),
            _episode_row(butler="gamma", content="gamma episode 1"),
        ]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=episodes)
        # facts and rules for each butler group
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # alpha facts
                [],  # alpha rules
                [],  # beta facts
                [],  # beta rules
                [],  # gamma facts
                [],  # gamma rules
            ]
        )
        pool.execute = AsyncMock(return_value="UPDATE 1")

        engine = MagicMock()
        spawner = _mock_spawner_success()

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["episodes_processed"] == 4
        assert result["butlers_processed"] == 3
        assert result["groups_consolidated"] == 3
        assert result["groups"]["owner/alpha"] == 2
        assert result["groups"]["owner/beta"] == 1
        assert result["groups"]["owner/gamma"] == 1


# ---------------------------------------------------------------------------
# Integration Tests — Consolidation Status Transitions
# ---------------------------------------------------------------------------


class TestConsolidationStatusTransitions:
    """Tests for consolidation_status state transitions."""

    async def test_pending_to_consolidated_on_success(self) -> None:
        """Test status transitions from pending to consolidated after successful execution."""
        episodes = [_episode_row(butler="test-butler", consolidation_status="pending")]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules
        pool.execute = AsyncMock(return_value="UPDATE 1")

        engine = MagicMock()
        spawner = _mock_spawner_success()

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["episodes_consolidated"] == 1
        # Find the terminal state UPDATE
        consolidated_calls = [
            c
            for c in pool.execute.call_args_list
            if "consolidation_status" in " ".join(c[0][0].split())
        ]
        assert len(consolidated_calls) >= 1
        sql_norm = " ".join(consolidated_calls[0][0][0].split())
        assert "consolidated = true" in sql_norm
        assert "consolidation_status = 'consolidated'" in sql_norm
        assert "leased_until" in sql_norm  # lease cleared

    async def test_pending_to_failed_with_retry_count_on_error(self) -> None:
        """Test episodes marked as failed with attempts incremented on group error.

        When the executor gets called and a fact fails, the episode is still
        consolidated (LLM output was produced). Group-level failures via
        _mark_group_failed set the 'failed' status.
        """
        episode_ids = [uuid.uuid4()]

        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")
        engine = MagicMock()

        from butlers.modules.memory.consolidation_parser import ConsolidationResult, NewFact

        parsed = ConsolidationResult(
            new_facts=[
                NewFact(subject="user", predicate="likes", content="broken"),
            ],
        )

        from unittest.mock import patch

        with patch.object(
            _exec_mod,
            "store_fact",
            new_callable=AsyncMock,
            side_effect=RuntimeError("storage error"),
        ):
            with patch.object(_exec_mod, "create_link", new_callable=AsyncMock):
                result = await execute_consolidation(
                    pool, engine, parsed, episode_ids, "test-butler"
                )

        # Individual fact failures → episodes still consolidated
        assert result["episodes_consolidated"] == 1
        assert len(result["errors"]) > 0
        # Check the terminal state UPDATE has the correct columns (normalise SQL)
        first_sql = " ".join(pool.execute.call_args_list[0][0][0].split())
        assert "consolidation_status = 'consolidated'" in first_sql
        assert "leased_until" in first_sql

    async def test_group_level_failure_sets_failed_or_dead_letter_status(self) -> None:
        """Test _mark_group_failed sets failed/dead_letter on spawner error."""
        episodes = [_episode_row(butler="test-butler")]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules
        pool.execute = AsyncMock(return_value="UPDATE 1")

        engine = MagicMock()
        spawner = _mock_spawner_failure(error="CC crashed")

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["groups_consolidated"] == 0
        # _mark_group_failed emits an UPDATE with consolidation_attempts + CASE
        failed_updates = [
            c for c in pool.execute.call_args_list if "consolidation_attempts" in str(c)
        ]
        assert len(failed_updates) >= 1
        sql = failed_updates[0][0][0]
        assert "failed" in sql or "dead_letter" in sql
        assert "leased_until" in sql  # lease cleared on failure

    async def test_idempotent_rerun_only_pending_episodes_claimed(self) -> None:
        """Test idempotent re-run — only pending episodes are claimed by SKIP LOCKED."""
        # The claim query filters for consolidation_status = 'pending', so already
        # consolidated episodes never appear in the result set
        pending_episodes = [
            _episode_row(
                butler="test-butler", content="new episode", consolidation_status="pending"
            )
        ]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=pending_episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules
        pool.execute = AsyncMock(return_value="UPDATE 1")

        engine = MagicMock()
        spawner = _mock_spawner_success()

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        # Only 1 episode should be processed (the pending one)
        assert result["episodes_processed"] == 1
        assert result["episodes_consolidated"] == 1

        # Verify claim query filters for 'pending' status
        claim_sql = mock_conn.fetch.call_args[0][0]
        assert "consolidation_status = 'pending'" in claim_sql
        assert "FOR UPDATE SKIP LOCKED" in claim_sql


# ---------------------------------------------------------------------------
# Integration Tests — Episode Cleanup
# ---------------------------------------------------------------------------


class TestEpisodeCleanupIntegration:
    """Integration tests for episode cleanup with consolidation_status."""

    async def test_cleanup_respects_pending_status_protection(self) -> None:
        """Test cleanup respects status='pending' (never deletes unconsolidated)."""
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 10"])
        pool.fetchval = AsyncMock(return_value=200)

        await run_episode_cleanup(pool, max_entries=100)

        cap_sql = pool.execute.call_args_list[1][0][0]
        assert "consolidated = true" in cap_sql
        assert "consolidated = false" not in cap_sql

    async def test_cleanup_deletes_only_consolidated_when_over_capacity(self) -> None:
        """Test that capacity cleanup only deletes consolidated episodes."""
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 50"])
        pool.fetchval = AsyncMock(return_value=150)

        result = await run_episode_cleanup(pool, max_entries=100)

        assert result["capacity_deleted"] == 50
        cap_sql = pool.execute.call_args_list[1][0][0]
        assert "consolidated = true" in cap_sql
        assert "ORDER BY created_at ASC" in cap_sql

    async def test_cleanup_expired_episodes_regardless_of_status(self) -> None:
        """Test that expired episodes are deleted regardless of consolidation status."""
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="DELETE 5")
        pool.fetchval = AsyncMock(return_value=100)

        result = await run_episode_cleanup(pool)

        expire_sql = pool.execute.call_args_list[0][0][0]
        assert "expires_at < now()" in expire_sql
        assert result["expired_deleted"] == 5

    async def test_cleanup_no_capacity_delete_when_under_limit(self) -> None:
        """Test that no capacity delete occurs when episode count is under limit."""
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="DELETE 3")
        pool.fetchval = AsyncMock(return_value=50)

        result = await run_episode_cleanup(pool, max_entries=100)

        assert result["expired_deleted"] == 3
        assert result["capacity_deleted"] == 0
        assert result["remaining"] == 50
        assert pool.execute.await_count == 1


# ---------------------------------------------------------------------------
# Integration Tests — Error Handling and Resilience
# ---------------------------------------------------------------------------


class TestConsolidationErrorHandling:
    """Tests for error handling and resilience in the consolidation pipeline."""

    async def test_parse_errors_captured_but_dont_block_execution(self) -> None:
        """Test that parse errors are captured but don't prevent partial execution."""
        from unittest.mock import patch

        episodes = [_episode_row(butler="test-butler")]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules
        pool.execute = AsyncMock(return_value="UPDATE 1")

        engine = MagicMock()

        # Output with one valid fact and one invalid (incomplete) fact
        spawner = _mock_spawner_success(
            output='{"new_facts": [{"subject": "s", "predicate": "p", "content": "c"}, '
            '{"subject": "incomplete"}], "confirmations": []}'
        )

        import sys

        with patch.dict(sys.modules, {"sentence_transformers": MagicMock()}):
            result = await run_consolidation(pool, engine, cc_spawner=spawner)

        # 1 valid fact stored despite parse error on second
        assert result["facts_created"] == 1
        assert len(result["errors"]) > 0

    async def test_storage_failure_in_one_fact_doesnt_block_others(self) -> None:
        """Test storage failure in one fact doesn't prevent others."""
        from butlers.modules.memory.consolidation_parser import ConsolidationResult, NewFact

        episode_ids = [uuid.uuid4()]
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")
        engine = MagicMock()

        parsed = ConsolidationResult(
            new_facts=[
                NewFact(subject="good", predicate="p", content="c"),
                NewFact(subject="bad", predicate="p", content="c"),
                NewFact(subject="good2", predicate="p", content="c"),
            ],
        )

        from unittest.mock import patch

        call_count = 0

        async def store_fact_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("storage error")
            return uuid.uuid4()

        with patch.object(_exec_mod, "store_fact", side_effect=store_fact_side_effect):
            with patch.object(_exec_mod, "create_link", new_callable=AsyncMock):
                result = await execute_consolidation(
                    pool, engine, parsed, episode_ids, "test-butler"
                )

        assert result["facts_created"] == 2
        assert len(result["errors"]) == 1
        assert result["episodes_consolidated"] == 1

    async def test_empty_consolidation_result_marks_episodes_consolidated(self) -> None:
        """Test that empty consolidation results still mark episodes as consolidated."""
        episodes = [_episode_row(butler="test-butler")]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules
        pool.execute = AsyncMock(return_value="UPDATE 1")

        engine = MagicMock()

        spawner = _mock_spawner_success(
            output='{"new_facts": [], "updated_facts": [], "new_rules": [], "confirmations": []}'
        )

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["episodes_consolidated"] == 1
        assert result["facts_created"] == 0
        assert result["rules_created"] == 0
        # Terminal state UPDATE must be called
        consolidated_calls = [
            c
            for c in pool.execute.call_args_list
            if "consolidation_status = 'consolidated'" in str(c)
        ]
        assert len(consolidated_calls) >= 1


# ---------------------------------------------------------------------------
# Integration Tests — Exponential Backoff and Dead-Letter
# ---------------------------------------------------------------------------


class TestExponentialBackoffAndDeadLetter:
    """Tests for exponential backoff retry and dead-letter transitions."""

    async def test_failed_episode_gets_next_retry_at(self) -> None:
        """Failed episodes have next_consolidation_retry_at set with exponential backoff."""
        episodes = [_episode_row(butler="test-butler", consolidation_attempts=1)]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules
        pool.execute = AsyncMock(return_value="UPDATE 1")
        engine = MagicMock()

        spawner = _mock_spawner_failure(error="transient error")

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["groups_consolidated"] == 0
        # The _mark_group_failed UPDATE must set next_consolidation_retry_at
        failed_calls = [
            c for c in pool.execute.call_args_list if "next_consolidation_retry_at" in str(c)
        ]
        assert len(failed_calls) >= 1
        sql = failed_calls[0][0][0]
        assert "power(2" in sql  # exponential backoff formula

    async def test_dead_letter_after_max_attempts(self) -> None:
        """Episode transitions to dead_letter after MAX_CONSOLIDATION_ATTEMPTS."""
        max_attempts = _cons_mod.MAX_CONSOLIDATION_ATTEMPTS
        # Episode already at max_attempts - 1 so this failure pushes it over
        episodes = [
            _episode_row(
                butler="test-butler",
                consolidation_attempts=max_attempts - 1,
            )
        ]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])  # facts, rules
        pool.execute = AsyncMock(return_value="UPDATE 1")
        engine = MagicMock()

        spawner = _mock_spawner_failure(error="persistent failure")

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["groups_consolidated"] == 0
        # The _mark_group_failed CASE expression transitions to dead_letter
        failed_calls = [c for c in pool.execute.call_args_list if "dead_letter" in str(c)]
        assert len(failed_calls) >= 1
        sql = failed_calls[0][0][0]
        assert "dead_letter_reason" in sql
        assert "CASE" in sql  # CASE expression for conditional dead-letter

    async def test_lease_cleared_on_group_failure(self) -> None:
        """Lease columns (leased_until, leased_by) are cleared after group failure."""
        episodes = [_episode_row(butler="test-butler")]

        pool, mock_conn = _mock_pool_with_connection(claim_rows=episodes)
        pool.fetch = AsyncMock(side_effect=[[], []])
        pool.execute = AsyncMock(return_value="UPDATE 1")
        engine = MagicMock()

        spawner = _mock_spawner_failure(error="crash")

        await run_consolidation(pool, engine, cc_spawner=spawner)

        # The failure UPDATE must clear lease columns
        failed_updates = [
            c for c in pool.execute.call_args_list if "leased_until" in str(c) and "NULL" in str(c)
        ]
        assert len(failed_updates) >= 1

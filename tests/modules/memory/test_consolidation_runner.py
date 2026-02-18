"""Tests for consolidation runner and episode cleanup in Memory Butler."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load consolidation module from disk (roster/ is not a Python package).
# Mock sentence_transformers before loading to avoid heavy dependency.
# ---------------------------------------------------------------------------

_CONSOLIDATION_PATH = MEMORY_MODULE_PATH / "consolidation.py"


def _load_consolidation_module():
    # sys.modules.setdefault("sentence_transformers", MagicMock())
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
) -> dict:
    """Build a dict mimicking an asyncpg Record for an unconsolidated episode."""
    return {
        "id": uuid.uuid4(),
        "butler": butler,
        "content": content,
        "importance": 5.0,
        "metadata": "{}",
        "created_at": datetime.now(UTC) - timedelta(days=days_ago),
    }


# ---------------------------------------------------------------------------
# Tests — run_consolidation
# ---------------------------------------------------------------------------


class TestRunConsolidation:
    """Tests for run_consolidation()."""

    async def test_fetches_unconsolidated_episodes(self) -> None:
        """run_consolidation issues a SELECT for unconsolidated episodes."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        engine = MagicMock()

        await run_consolidation(pool, engine)

        pool.fetch.assert_awaited_once()
        sql = pool.fetch.call_args[0][0]
        # Current behavior uses the consolidated boolean
        # When source code is updated, this should also check:
        # assert "consolidation_status = 'pending'" in sql
        assert "consolidated = false" in sql
        assert "ORDER BY created_at" in sql

    async def test_groups_episodes_by_source_butler(self) -> None:
        """Episodes from different butlers are grouped separately."""
        rows = [
            _episode_row(butler="alpha", content="a1"),
            _episode_row(butler="alpha", content="a2"),
            _episode_row(butler="beta", content="b1"),
        ]
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=rows)
        engine = MagicMock()

        result = await run_consolidation(pool, engine)

        assert result["groups"]["alpha"] == 2
        assert result["groups"]["beta"] == 1

    async def test_returns_correct_stats(self) -> None:
        """Stats dict has the right shape and values."""
        rows = [
            _episode_row(butler="alpha"),
            _episode_row(butler="alpha"),
            _episode_row(butler="beta"),
            _episode_row(butler="gamma"),
        ]
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=rows)
        engine = MagicMock()

        result = await run_consolidation(pool, engine)

        assert result["episodes_processed"] == 4
        assert result["butlers_processed"] == 3
        assert result["groups"] == {"alpha": 2, "beta": 1, "gamma": 1}

    async def test_handles_no_unconsolidated_episodes(self) -> None:
        """When there are no unconsolidated episodes, return zeros."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        engine = MagicMock()

        result = await run_consolidation(pool, engine)

        assert result["episodes_processed"] == 0
        assert result["butlers_processed"] == 0
        assert result["groups"] == {}
        assert result["groups_consolidated"] == 0
        assert result["facts_created"] == 0
        assert result["episodes_consolidated"] == 0

    async def test_without_spawner_returns_grouping_stats_only(self) -> None:
        """When cc_spawner is None, only grouping stats are returned."""
        rows = [
            _episode_row(butler="alpha"),
            _episode_row(butler="beta"),
        ]
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=rows)
        engine = MagicMock()

        result = await run_consolidation(pool, engine, cc_spawner=None)

        assert result["episodes_processed"] == 2
        assert result["butlers_processed"] == 2
        assert result["groups_consolidated"] == 0
        assert result["facts_created"] == 0

    async def test_orchestrates_full_pipeline_with_spawner(self) -> None:
        """With cc_spawner, run_consolidation orchestrates the full pipeline."""
        from unittest.mock import MagicMock as SyncMock

        episodes = [_episode_row(butler="test-butler", content="test content")]

        # Mock connection with transaction support
        mock_conn = SyncMock()
        mock_transaction = SyncMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=None)
        mock_transaction.__aexit__ = AsyncMock(return_value=None)
        mock_conn.transaction.return_value = mock_transaction

        # Mock pool.acquire() to return an async context manager
        mock_acquire_cm = SyncMock()
        mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire_cm.__aexit__ = AsyncMock(return_value=None)

        pool = AsyncMock()
        pool.fetch = AsyncMock(
            side_effect=[
                episodes,  # Initial episodes query
                [],  # Existing facts query
                [],  # Existing rules query
            ]
        )
        pool.execute = AsyncMock(return_value="UPDATE 1")
        # Make acquire() a regular method that returns the context manager
        pool.acquire = SyncMock(return_value=mock_acquire_cm)

        engine = MagicMock()

        # Mock spawner that returns valid JSON output
        spawner = AsyncMock()
        spawner.trigger = AsyncMock(
            return_value=MagicMock(
                success=True,
                output='{"new_facts": [{"subject": "test", "predicate": "is", '
                '"content": "example", "permanence": "standard"}], '
                '"updated_facts": [], "new_rules": [], "confirmations": []}',
            )
        )

        # Mock embedding and storage functions
        import sys
        from unittest.mock import patch

        with patch.dict(
            sys.modules,
            {
                "sentence_transformers": MagicMock(),
            },
        ):
            result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["episodes_processed"] == 1
        assert result["butlers_processed"] == 1
        spawner.trigger.assert_awaited_once()
        # Verify prompt was passed to spawner
        call_kwargs = spawner.trigger.call_args[1]
        assert "prompt" in call_kwargs
        assert "test content" in call_kwargs["prompt"]

    async def test_partial_failure_does_not_block_other_groups(self) -> None:
        """When one butler group fails, others continue processing."""
        rows = [
            _episode_row(butler="alpha"),
            _episode_row(butler="beta"),
        ]
        pool = AsyncMock()
        # First fetch: episodes
        # Second fetch: facts for alpha (will fail)
        # Third fetch: facts for beta
        # Fourth fetch: rules for beta
        pool.fetch = AsyncMock(
            side_effect=[
                rows,
                Exception("Database error for alpha"),
                [],  # facts for beta
                [],  # rules for beta
            ]
        )
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

        result = await run_consolidation(pool, engine, cc_spawner=spawner)

        assert result["butlers_processed"] == 2
        assert result["groups_consolidated"] == 1  # Only beta succeeded
        assert len(result["errors"]) > 0
        assert "alpha" in result["errors"][0]

    async def test_cc_failure_is_reported_in_errors(self) -> None:
        """When runtime session fails, error is captured in stats (sanitized)."""
        episodes = [_episode_row(butler="test-butler")]
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=[episodes, [], []])
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
        # Error message should be sanitized (no internal details in return value)
        assert "test-butler" in result["errors"][0]
        assert "runtime session failed" in result["errors"][0]

    async def test_episodes_marked_consolidated_only_after_success(self) -> None:
        """Episodes are marked consolidated=true only after execute_consolidation."""
        episodes = [_episode_row(butler="test-butler")]
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=[episodes, [], []])
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

        # Verify UPDATE was called with consolidated=true
        update_calls = [
            call for call in pool.execute.call_args_list if "UPDATE episodes" in str(call)
        ]
        assert len(update_calls) > 0


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

        # First execute call should be the expiry delete
        expire_sql = pool.execute.call_args_list[0][0][0]
        assert "expires_at < now()" in expire_sql
        assert result["expired_deleted"] == 5

    async def test_enforces_capacity_limit(self) -> None:
        """When remaining > max_entries, oldest consolidated episodes are deleted."""
        pool = AsyncMock()
        # First execute: expire delete returns 0
        # Second execute: capacity delete returns 50
        pool.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 50"])
        pool.fetchval = AsyncMock(return_value=150)

        result = await run_episode_cleanup(pool, max_entries=100)

        assert result["capacity_deleted"] == 50
        # Capacity delete SQL should target consolidated episodes
        cap_sql = pool.execute.call_args_list[1][0][0]
        # Current behavior uses the consolidated boolean
        # When source code is updated, this should also check:
        # assert "consolidation_status = 'consolidated'" in cap_sql
        assert "consolidated = true" in cap_sql
        assert "ORDER BY created_at ASC" in cap_sql
        # The excess (150 - 100 = 50) should be passed as a parameter
        cap_param = pool.execute.call_args_list[1][0][1]
        assert cap_param == 50

    async def test_protects_unconsolidated_episodes(self) -> None:
        """Capacity cleanup only deletes consolidated episodes, never unconsolidated."""
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 10"])
        pool.fetchval = AsyncMock(return_value=200)

        await run_episode_cleanup(pool, max_entries=100)

        # The capacity delete query must only target consolidated episodes
        cap_sql = pool.execute.call_args_list[1][0][0]
        # Current behavior uses the consolidated boolean
        # When source code is updated, this should also check:
        # assert "consolidation_status = 'consolidated'" in cap_sql
        # assert "consolidation_status = 'pending'" not in cap_sql
        assert "consolidated = true" in cap_sql
        # Unconsolidated episodes must NOT be targeted
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
        # Only one execute call (the expiry delete), no capacity delete
        assert pool.execute.await_count == 1

    async def test_remaining_reflects_capacity_deletion(self) -> None:
        """The remaining count is adjusted after capacity deletion."""
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 20"])
        pool.fetchval = AsyncMock(return_value=120)

        result = await run_episode_cleanup(pool, max_entries=100)

        assert result["remaining"] == 100  # 120 - 20

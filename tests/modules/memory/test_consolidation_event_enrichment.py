"""Tests for enriched memory_events INSERTs in consolidation code.

After mem_021, the consolidation_executor and consolidation runner emit
memory_events with the new actor_butler, memory_type, and memory_id columns
populated.  These tests verify the SQL emitted contains those columns.
"""

from __future__ import annotations

import importlib.util
import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

pytestmark = pytest.mark.unit

_EXECUTOR_PATH = MEMORY_MODULE_PATH / "consolidation_executor.py"
_CONSOLIDATION_PATH = MEMORY_MODULE_PATH / "consolidation.py"


def _load_executor_module():
    spec = importlib.util.spec_from_file_location("consolidation_executor", _EXECUTOR_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_consolidation_module():
    spec = importlib.util.spec_from_file_location("consolidation", _CONSOLIDATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_exec_mod = _load_executor_module()
_consol_mod = _load_consolidation_module()

execute_consolidation = _exec_mod.execute_consolidation
_mark_group_failed = _consol_mod._mark_group_failed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="UPDATE 2")
    return pool


class TestExecutorEventEnrichment:
    """episode_consolidated events emitted with actor_butler, memory_type, memory_id."""

    async def test_consolidated_event_includes_actor_butler_column(self) -> None:
        """The INSERT for episode_consolidated populates actor_butler from butler column."""
        from unittest.mock import patch

        from butlers.modules.memory.consolidation_parser import ConsolidationResult

        pool = _mock_pool()
        engine = MagicMock()
        episode_ids = [uuid.uuid4()]
        parsed = ConsolidationResult()

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["episodes_consolidated"] == 1
        # Verify the memory_events INSERT SQL (second pool.execute call after terminal UPDATE)
        assert pool.execute.await_count >= 2
        event_sql = " ".join(pool.execute.call_args_list[1][0][0].split())
        assert "actor_butler" in event_sql
        assert "memory_type" in event_sql
        assert "memory_id" in event_sql

    async def test_consolidated_event_selects_butler_as_actor_butler(self) -> None:
        """The INSERT SELECT assigns butler column to actor_butler."""
        pool = _mock_pool()
        engine = MagicMock()
        episode_ids = [uuid.uuid4()]

        from unittest.mock import patch

        from butlers.modules.memory.consolidation_parser import ConsolidationResult

        parsed = ConsolidationResult()

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            await execute_consolidation(pool, engine, parsed, episode_ids, "my-butler")

        assert pool.execute.await_count >= 2
        event_sql = " ".join(pool.execute.call_args_list[1][0][0].split())
        # The SELECT should use the butler column from the episodes table
        assert "butler" in event_sql
        # And the event_type must be episode_consolidated
        assert "episode_consolidated" in event_sql

    async def test_consolidated_event_emits_episode_memory_type(self) -> None:
        """The episode_consolidated event has memory_type = 'episode'."""
        pool = _mock_pool()
        engine = MagicMock()
        episode_ids = [uuid.uuid4()]

        from unittest.mock import patch

        from butlers.modules.memory.consolidation_parser import ConsolidationResult

        parsed = ConsolidationResult()

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            await execute_consolidation(pool, engine, parsed, episode_ids, "my-butler")

        assert pool.execute.await_count >= 2
        event_sql = " ".join(pool.execute.call_args_list[1][0][0].split())
        assert "'episode'" in event_sql


class TestExecutorEventEnrichmentSourceInspection:
    """Source-code inspection of consolidation_executor.py event INSERT."""

    def test_executor_event_insert_includes_actor_butler(self) -> None:
        source = inspect.getsource(_exec_mod)
        # The INSERT column list must include actor_butler
        assert "actor_butler" in source

    def test_executor_event_insert_includes_memory_type(self) -> None:
        source = inspect.getsource(_exec_mod)
        assert "memory_type" in source

    def test_executor_event_insert_includes_memory_id(self) -> None:
        source = inspect.getsource(_exec_mod)
        assert "memory_id" in source

    def test_executor_event_selects_butler_for_actor_butler(self) -> None:
        source = inspect.getsource(_exec_mod)
        # The SELECT that feeds the INSERT should pull butler column from episodes
        assert "butler" in source


class TestMarkGroupFailedEventEnrichment:
    """Source-code inspection and runtime checks for _mark_group_failed event INSERT."""

    def test_consolidation_emits_actor_butler_in_failure_event(self) -> None:
        source = inspect.getsource(_consol_mod)
        assert "actor_butler" in source

    def test_consolidation_emits_memory_type_in_failure_event(self) -> None:
        source = inspect.getsource(_consol_mod)
        assert "memory_type" in source

    def test_consolidation_emits_memory_id_in_failure_event(self) -> None:
        source = inspect.getsource(_consol_mod)
        assert "memory_id" in source

    async def test_mark_group_failed_event_insert_includes_enrichment_columns(self) -> None:
        """_mark_group_failed emits events with actor_butler, memory_type, memory_id."""
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")
        episode_ids = [uuid.uuid4()]

        await _mark_group_failed(pool, episode_ids, "some error")

        # Two pool.execute calls: UPDATE episodes, INSERT memory_events
        assert pool.execute.await_count >= 2
        event_sql = " ".join(pool.execute.call_args_list[1][0][0].split())
        assert "actor_butler" in event_sql
        assert "memory_type" in event_sql
        assert "memory_id" in event_sql

    async def test_mark_group_failed_selects_butler_for_actor_butler(self) -> None:
        """The event INSERT SELECT picks up butler from the episodes table."""
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")
        episode_ids = [uuid.uuid4()]

        await _mark_group_failed(pool, episode_ids, "some error")

        assert pool.execute.await_count >= 2
        event_sql = " ".join(pool.execute.call_args_list[1][0][0].split())
        assert "butler" in event_sql

    async def test_mark_group_failed_emits_episode_memory_type(self) -> None:
        """The failure event specifies memory_type = 'episode'."""
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")
        episode_ids = [uuid.uuid4()]

        await _mark_group_failed(pool, episode_ids, "some error")

        assert pool.execute.await_count >= 2
        event_sql = " ".join(pool.execute.call_args_list[1][0][0].split())
        assert "'episode'" in event_sql

    async def test_mark_group_failed_no_op_when_empty_ids(self) -> None:
        """_mark_group_failed is a no-op when episode_ids is empty."""
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 0")

        await _mark_group_failed(pool, [], "some error")

        pool.execute.assert_not_awaited()

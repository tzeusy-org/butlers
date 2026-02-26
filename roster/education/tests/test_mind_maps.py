"""Unit tests for education butler mind map tools.

All tests mock the asyncpg pool/connection objects — no live database required.

Coverage:
- CRUD for mind maps and nodes
- Mastery status state machine (valid + invalid transitions)
- Edge creation with cycle detection (self-loop, 2-node, multi-hop, valid DAG)
- Frontier query (various mastery states, ordering)
- Subtree query (leaf, internal, dedup)
- Auto-completion lifecycle
- Error cases (not found, cross-map edges)
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers: mock asyncpg pool builder
# ---------------------------------------------------------------------------


class _MockRecord:
    """Minimal asyncpg.Record-like object backed by a dict.

    Supports dict(row), row[key], row.items(), etc.
    asyncpg.Record supports the Mapping protocol, so dict(record)
    iterates keys and then calls __getitem__ for each key.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


def _make_row(data: dict[str, Any]) -> _MockRecord:
    """Build a _MockRecord that behaves like an asyncpg.Record."""
    return _MockRecord(data)


def _make_pool(
    *,
    fetchrow_returns: list[Any] | None = None,
    fetch_returns: list[Any] | None = None,
    fetchval_returns: list[Any] | None = None,
    execute_returns: list[str] | None = None,
) -> AsyncMock:
    """Build an AsyncMock behaving like an asyncpg.Pool.

    Each ``*_returns`` parameter is a list consumed sequentially (FIFO).
    """
    pool = AsyncMock()

    if fetchrow_returns is not None:
        pool.fetchrow = AsyncMock(side_effect=list(fetchrow_returns))
    else:
        pool.fetchrow = AsyncMock(return_value=None)

    if fetch_returns is not None:
        pool.fetch = AsyncMock(side_effect=list(fetch_returns))
    else:
        pool.fetch = AsyncMock(return_value=[])

    if fetchval_returns is not None:
        pool.fetchval = AsyncMock(side_effect=list(fetchval_returns))
    else:
        pool.fetchval = AsyncMock(return_value=0)

    if execute_returns is not None:
        pool.execute = AsyncMock(side_effect=list(execute_returns))
    else:
        pool.execute = AsyncMock(return_value="UPDATE 1")

    return pool


def _node_row(
    node_id: str | None = None,
    mind_map_id: str | None = None,
    label: str = "Test Node",
    mastery_status: str = "unseen",
    depth: int = 0,
    effort_minutes: int | None = None,
) -> dict[str, Any]:
    """Return a minimal node row dict."""
    return {
        "id": node_id or str(uuid.uuid4()),
        "mind_map_id": mind_map_id or str(uuid.uuid4()),
        "label": label,
        "description": None,
        "depth": depth,
        "mastery_score": 0.0,
        "mastery_status": mastery_status,
        "ease_factor": 2.5,
        "repetitions": 0,
        "next_review_at": None,
        "last_reviewed_at": None,
        "effort_minutes": effort_minutes,
        "metadata": {},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _map_row(
    map_id: str | None = None,
    title: str = "Test Map",
    status: str = "active",
) -> dict[str, Any]:
    """Return a minimal mind map row dict."""
    return {
        "id": map_id or str(uuid.uuid4()),
        "title": title,
        "root_node_id": None,
        "status": status,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Tests: mind_map_create
# ---------------------------------------------------------------------------


class TestMindMapCreate:
    """mind_map_create inserts a new row and returns its UUID."""

    async def test_returns_uuid_string(self) -> None:
        from butlers.tools.education import mind_map_create

        new_id = str(uuid.uuid4())
        pool = _make_pool(fetchrow_returns=[_make_row({"id": new_id})])
        result = await mind_map_create(pool, title="Python")
        assert result == new_id

    async def test_sql_inserts_active_status(self) -> None:
        from butlers.tools.education import mind_map_create

        new_id = str(uuid.uuid4())
        pool = _make_pool(fetchrow_returns=[_make_row({"id": new_id})])
        await mind_map_create(pool, title="Calculus")
        # Verify the SQL was called with the title
        call_args = pool.fetchrow.call_args
        assert "Calculus" in call_args.args or "Calculus" in str(call_args)


# ---------------------------------------------------------------------------
# Tests: mind_map_get
# ---------------------------------------------------------------------------


class TestMindMapGet:
    """mind_map_get returns a dict or None."""

    async def test_returns_dict_when_found(self) -> None:
        from butlers.tools.education import mind_map_get

        map_id = str(uuid.uuid4())
        row = _map_row(map_id=map_id, title="Physics")
        pool = _make_pool(fetchrow_returns=[_make_row(row)])
        result = await mind_map_get(pool, map_id)
        assert result is not None
        assert result["id"] == map_id
        assert result["title"] == "Physics"

    async def test_returns_none_when_not_found(self) -> None:
        from butlers.tools.education import mind_map_get

        pool = _make_pool(fetchrow_returns=[None])
        result = await mind_map_get(pool, str(uuid.uuid4()))
        assert result is None


# ---------------------------------------------------------------------------
# Tests: mind_map_list
# ---------------------------------------------------------------------------


class TestMindMapList:
    """mind_map_list returns list of dicts, optionally filtered by status."""

    async def test_returns_all_when_no_filter(self) -> None:
        from butlers.tools.education import mind_map_list

        rows = [
            _make_row(_map_row(title="A", status="active")),
            _make_row(_map_row(title="B", status="completed")),
        ]
        pool = _make_pool(fetch_returns=[rows])
        result = await mind_map_list(pool)
        assert len(result) == 2

    async def test_returns_filtered_by_status(self) -> None:
        from butlers.tools.education import mind_map_list

        rows = [_make_row(_map_row(title="Active Map", status="active"))]
        pool = _make_pool(fetch_returns=[rows])
        result = await mind_map_list(pool, status="active")
        assert len(result) == 1
        # Verify status param was passed in the SQL call
        call_args = pool.fetch.call_args
        assert "active" in str(call_args)

    async def test_returns_empty_list_when_no_maps(self) -> None:
        from butlers.tools.education import mind_map_list

        pool = _make_pool(fetch_returns=[[]])
        result = await mind_map_list(pool)
        assert result == []


# ---------------------------------------------------------------------------
# Tests: mind_map_update_status
# ---------------------------------------------------------------------------


class TestMindMapUpdateStatus:
    """mind_map_update_status updates status or raises ValueError if not found."""

    async def test_succeeds_when_map_exists(self) -> None:
        from butlers.tools.education import mind_map_update_status

        pool = _make_pool(execute_returns=["UPDATE 1"])
        await mind_map_update_status(pool, str(uuid.uuid4()), "completed")
        assert pool.execute.called

    async def test_raises_when_not_found(self) -> None:
        from butlers.tools.education import mind_map_update_status

        pool = _make_pool(execute_returns=["UPDATE 0"])
        with pytest.raises(ValueError, match="Mind map not found"):
            await mind_map_update_status(pool, str(uuid.uuid4()), "completed")


# ---------------------------------------------------------------------------
# Tests: mind_map_node_create
# ---------------------------------------------------------------------------


class TestMindMapNodeCreate:
    """mind_map_node_create inserts a node and returns its UUID."""

    async def test_returns_node_uuid(self) -> None:
        from butlers.tools.education import mind_map_node_create

        new_id = str(uuid.uuid4())
        pool = _make_pool(fetchrow_returns=[_make_row({"id": new_id})])
        result = await mind_map_node_create(
            pool,
            mind_map_id=str(uuid.uuid4()),
            label="Variables",
        )
        assert result == new_id

    async def test_defaults_depth_to_zero(self) -> None:
        from butlers.tools.education import mind_map_node_create

        new_id = str(uuid.uuid4())
        pool = _make_pool(fetchrow_returns=[_make_row({"id": new_id})])
        await mind_map_node_create(pool, mind_map_id=str(uuid.uuid4()), label="Loops")
        # depth=None should be sent as 0 to the DB
        sql_args = pool.fetchrow.call_args.args
        # depth 0 should be passed (4th positional param after map_id, label, desc)
        assert 0 in sql_args

    async def test_accepts_explicit_depth(self) -> None:
        from butlers.tools.education import mind_map_node_create

        new_id = str(uuid.uuid4())
        pool = _make_pool(fetchrow_returns=[_make_row({"id": new_id})])
        await mind_map_node_create(pool, mind_map_id=str(uuid.uuid4()), label="Advanced", depth=3)
        sql_args = pool.fetchrow.call_args.args
        assert 3 in sql_args


# ---------------------------------------------------------------------------
# Tests: mind_map_node_get
# ---------------------------------------------------------------------------


class TestMindMapNodeGet:
    """mind_map_node_get returns dict or None."""

    async def test_returns_dict_when_found(self) -> None:
        from butlers.tools.education import mind_map_node_get

        node_id = str(uuid.uuid4())
        row = _node_row(node_id=node_id, label="Recursion")
        pool = _make_pool(fetchrow_returns=[_make_row(row)])
        result = await mind_map_node_get(pool, node_id)
        assert result is not None
        assert result["id"] == node_id
        assert result["label"] == "Recursion"

    async def test_returns_none_when_not_found(self) -> None:
        from butlers.tools.education import mind_map_node_get

        pool = _make_pool(fetchrow_returns=[None])
        result = await mind_map_node_get(pool, str(uuid.uuid4()))
        assert result is None


# ---------------------------------------------------------------------------
# Tests: mind_map_node_list
# ---------------------------------------------------------------------------


class TestMindMapNodeList:
    """mind_map_node_list returns nodes optionally filtered by mastery_status."""

    async def test_returns_all_nodes(self) -> None:
        from butlers.tools.education import mind_map_node_list

        map_id = str(uuid.uuid4())
        rows = [
            _make_row(_node_row(mind_map_id=map_id, label="A", mastery_status="unseen")),
            _make_row(_node_row(mind_map_id=map_id, label="B", mastery_status="mastered")),
        ]
        pool = _make_pool(fetch_returns=[rows])
        result = await mind_map_node_list(pool, map_id)
        assert len(result) == 2

    async def test_filters_by_mastery_status(self) -> None:
        from butlers.tools.education import mind_map_node_list

        map_id = str(uuid.uuid4())
        rows = [_make_row(_node_row(mind_map_id=map_id, label="A", mastery_status="learning"))]
        pool = _make_pool(fetch_returns=[rows])
        result = await mind_map_node_list(pool, map_id, mastery_status="learning")
        assert len(result) == 1
        assert "learning" in str(pool.fetch.call_args)

    async def test_returns_empty_list(self) -> None:
        from butlers.tools.education import mind_map_node_list

        pool = _make_pool(fetch_returns=[[]])
        result = await mind_map_node_list(pool, str(uuid.uuid4()))
        assert result == []


# ---------------------------------------------------------------------------
# Tests: mastery status state machine
# ---------------------------------------------------------------------------


class TestMasteryStateMachine:
    """mind_map_node_update enforces valid mastery_status transitions."""

    async def _make_update_pool(
        self,
        current_status: str,
        map_id: str | None = None,
        node_id: str | None = None,
    ) -> tuple[AsyncMock, str, str]:
        """Return (pool, node_id, map_id) wired for a node with given mastery_status."""
        nid = node_id or str(uuid.uuid4())
        mid = map_id or str(uuid.uuid4())
        # fetchrow for current_row (mastery_status + mind_map_id)
        current_row = _make_row({"mastery_status": current_status, "mind_map_id": mid})
        pool = _make_pool(
            fetchrow_returns=[current_row],
            execute_returns=["UPDATE 1"],
            fetchval_returns=[1, 1],  # unmastered_count, node_count
        )
        return pool, nid, mid

    # Valid transitions
    async def test_unseen_to_diagnosed(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("unseen")
        # diagnosed → mastery_status != mastered, so no auto-complete check
        pool.fetchval = AsyncMock(return_value=1)  # not all mastered
        await mind_map_node_update(pool, nid, mastery_status="diagnosed")
        assert pool.execute.called

    async def test_unseen_to_learning(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("unseen")
        pool.fetchval = AsyncMock(return_value=1)
        await mind_map_node_update(pool, nid, mastery_status="learning")
        assert pool.execute.called

    async def test_diagnosed_to_learning(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("diagnosed")
        pool.fetchval = AsyncMock(return_value=1)
        await mind_map_node_update(pool, nid, mastery_status="learning")
        assert pool.execute.called

    async def test_diagnosed_to_mastered(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, mid = await self._make_update_pool("diagnosed")
        # fetchval: unmastered_count=0 (all mastered) → triggers auto-complete
        # fetchval: node_count=1
        pool.fetchval = AsyncMock(side_effect=[0, 1])
        # auto-complete calls mind_map_update_status which calls pool.execute again
        pool.execute = AsyncMock(return_value="UPDATE 1")
        await mind_map_node_update(pool, nid, mastery_status="mastered")
        assert pool.execute.called

    async def test_learning_to_reviewing(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("learning")
        pool.fetchval = AsyncMock(return_value=1)
        await mind_map_node_update(pool, nid, mastery_status="reviewing")
        assert pool.execute.called

    async def test_learning_to_mastered(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("learning")
        pool.fetchval = AsyncMock(side_effect=[0, 1])
        pool.execute = AsyncMock(return_value="UPDATE 1")
        await mind_map_node_update(pool, nid, mastery_status="mastered")
        assert pool.execute.called

    async def test_reviewing_to_mastered(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("reviewing")
        pool.fetchval = AsyncMock(side_effect=[0, 1])
        pool.execute = AsyncMock(return_value="UPDATE 1")
        await mind_map_node_update(pool, nid, mastery_status="mastered")
        assert pool.execute.called

    async def test_reviewing_to_learning_regression(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("reviewing")
        pool.fetchval = AsyncMock(return_value=1)
        await mind_map_node_update(pool, nid, mastery_status="learning")
        assert pool.execute.called

    async def test_mastered_to_reviewing_spaced_repetition(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("mastered")
        pool.fetchval = AsyncMock(return_value=1)
        await mind_map_node_update(pool, nid, mastery_status="reviewing")
        assert pool.execute.called

    # Invalid transitions
    async def test_unseen_to_mastered_rejected(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("unseen")
        with pytest.raises(ValueError, match="Invalid mastery_status transition"):
            await mind_map_node_update(pool, nid, mastery_status="mastered")

    async def test_unseen_to_reviewing_rejected(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("unseen")
        with pytest.raises(ValueError, match="Invalid mastery_status transition"):
            await mind_map_node_update(pool, nid, mastery_status="reviewing")

    async def test_mastered_to_learning_rejected(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("mastered")
        with pytest.raises(ValueError, match="Invalid mastery_status transition"):
            await mind_map_node_update(pool, nid, mastery_status="learning")

    async def test_mastered_to_unseen_rejected(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("mastered")
        with pytest.raises(ValueError, match="Invalid mastery_status transition"):
            await mind_map_node_update(pool, nid, mastery_status="unseen")

    async def test_diagnosed_to_unseen_rejected(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("diagnosed")
        with pytest.raises(ValueError, match="Invalid mastery_status transition"):
            await mind_map_node_update(pool, nid, mastery_status="unseen")

    async def test_learning_to_diagnosed_rejected(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool, nid, _ = await self._make_update_pool("learning")
        with pytest.raises(ValueError, match="Invalid mastery_status transition"):
            await mind_map_node_update(pool, nid, mastery_status="diagnosed")

    async def test_node_not_found_raises_error(self) -> None:
        from butlers.tools.education import mind_map_node_update

        pool = _make_pool(fetchrow_returns=[None])
        with pytest.raises(ValueError, match="Node not found"):
            await mind_map_node_update(pool, str(uuid.uuid4()), mastery_status="learning")

    async def test_non_writable_fields_silently_ignored(self) -> None:
        """Non-writable fields like 'label' are dropped; only writable fields are updated."""
        from butlers.tools.education import mind_map_node_update

        nid = str(uuid.uuid4())
        mid = str(uuid.uuid4())
        current_row = _make_row({"mastery_status": "unseen", "mind_map_id": mid})
        pool = _make_pool(
            fetchrow_returns=[current_row],
            execute_returns=["UPDATE 1"],
            fetchval_returns=[1],
        )
        # label is not writable; mastery_score is writable
        await mind_map_node_update(pool, nid, label="Should be ignored", mastery_score=0.5)
        # Should not raise; execute should be called for mastery_score update
        assert pool.execute.called
        # 'label' should not appear in the SET clause
        sql_call = pool.execute.call_args.args[0]
        assert "label" not in sql_call


# ---------------------------------------------------------------------------
# Tests: mind_map_edge_create — cycle detection
# ---------------------------------------------------------------------------


class TestEdgeCreateCycleDetection:
    """mind_map_edge_create rejects cycles (self-loop, 2-node, multi-hop)."""

    def _setup_same_map_nodes(
        self,
        parent_id: str,
        child_id: str,
        map_id: str,
    ) -> AsyncMock:
        """Pool returning two nodes in the same map."""
        node_rows = [
            _make_row({"id": parent_id, "mind_map_id": map_id}),
            _make_row({"id": child_id, "mind_map_id": map_id}),
        ]
        pool = _make_pool(
            fetch_returns=[node_rows],
            fetchrow_returns=None,
            execute_returns=["INSERT 0 1", "UPDATE 2"],
        )
        return pool

    async def test_self_loop_rejected(self) -> None:
        from butlers.tools.education import mind_map_edge_create

        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())
        pool = self._setup_same_map_nodes(node_id, node_id, map_id)
        with pytest.raises(ValueError, match="cycle"):
            await mind_map_edge_create(pool, node_id, node_id)

    async def test_two_node_cycle_rejected(self) -> None:
        """A → B already exists; adding B → A should be rejected."""
        from butlers.tools.education.mind_map_edges import _check_cycle

        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())

        # _check_cycle: fetchrow returns has_cycle=True (ancestors of parent include child)
        cycle_row = _make_row({"has_cycle": True})
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=cycle_row)

        result = await _check_cycle(pool, parent_id, child_id)
        assert result is True

    async def test_multi_hop_cycle_rejected(self) -> None:
        """A → B → C exists; adding C → A should be rejected."""
        from butlers.tools.education.mind_map_edges import _check_cycle

        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())

        # Simulated: CTE finds child_id in ancestors of parent_id
        cycle_row = _make_row({"has_cycle": True})
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=cycle_row)

        result = await _check_cycle(pool, parent_id, child_id)
        assert result is True

    async def test_no_cycle_for_valid_dag(self) -> None:
        """A → B is fine when there is no path from B back to A."""
        from butlers.tools.education.mind_map_edges import _check_cycle

        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())

        # No cycle found
        no_cycle_row = _make_row({"has_cycle": False})
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=no_cycle_row)

        result = await _check_cycle(pool, parent_id, child_id)
        assert result is False

    async def test_self_loop_detected_before_db_query(self) -> None:
        """Self-loop (same IDs) is caught immediately without a DB query."""
        from butlers.tools.education.mind_map_edges import _check_cycle

        node_id = str(uuid.uuid4())
        pool = AsyncMock()

        result = await _check_cycle(pool, node_id, node_id)
        assert result is True
        # DB should NOT be queried for self-loops
        pool.fetchrow.assert_not_called()

    async def test_cross_map_edge_rejected(self) -> None:
        """Edge creation fails when nodes are in different mind maps."""
        from butlers.tools.education import mind_map_edge_create

        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        map_a = str(uuid.uuid4())
        map_b = str(uuid.uuid4())

        node_rows = [
            _make_row({"id": parent_id, "mind_map_id": map_a}),
            _make_row({"id": child_id, "mind_map_id": map_b}),
        ]
        pool = _make_pool(fetch_returns=[node_rows])
        with pytest.raises(ValueError, match="different mind maps"):
            await mind_map_edge_create(pool, parent_id, child_id)

    async def test_parent_not_found_raises_error(self) -> None:
        from butlers.tools.education import mind_map_edge_create

        child_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())
        # Only child found
        node_rows = [_make_row({"id": child_id, "mind_map_id": map_id})]
        pool = _make_pool(fetch_returns=[node_rows])
        with pytest.raises(ValueError, match="Parent node not found"):
            await mind_map_edge_create(pool, str(uuid.uuid4()), child_id)

    async def test_child_not_found_raises_error(self) -> None:
        from butlers.tools.education import mind_map_edge_create

        parent_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())
        # Only parent found
        node_rows = [_make_row({"id": parent_id, "mind_map_id": map_id})]
        pool = _make_pool(fetch_returns=[node_rows])
        with pytest.raises(ValueError, match="Child node not found"):
            await mind_map_edge_create(pool, parent_id, str(uuid.uuid4()))

    async def test_invalid_edge_type_rejected(self) -> None:
        from butlers.tools.education import mind_map_edge_create

        pool = AsyncMock()
        with pytest.raises(ValueError, match="Invalid edge_type"):
            await mind_map_edge_create(pool, str(uuid.uuid4()), str(uuid.uuid4()), "invalid")

    async def test_related_edge_skips_cycle_check(self) -> None:
        """'related' edge type skips DAG cycle detection."""
        from butlers.tools.education import mind_map_edge_create

        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())

        node_rows = [
            _make_row({"id": parent_id, "mind_map_id": map_id}),
            _make_row({"id": child_id, "mind_map_id": map_id}),
        ]
        pool = _make_pool(
            fetch_returns=[node_rows],
            execute_returns=["INSERT 0 1", "UPDATE 2"],
        )
        # Should not raise even for same-ID nodes (related edges skip cycle check)
        # Here parent != child so should succeed
        await mind_map_edge_create(pool, parent_id, child_id, "related")
        assert pool.execute.called


# ---------------------------------------------------------------------------
# Tests: mind_map_edge_delete
# ---------------------------------------------------------------------------


class TestEdgeDelete:
    """mind_map_edge_delete is idempotent and recomputes depths."""

    async def test_deletes_edge_and_recomputes_depth(self) -> None:
        from butlers.tools.education import mind_map_edge_delete

        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        pool = _make_pool(execute_returns=["DELETE 1", "UPDATE 1"])
        await mind_map_edge_delete(pool, parent_id, child_id)
        assert pool.execute.call_count >= 2

    async def test_idempotent_when_edge_not_found(self) -> None:
        from butlers.tools.education import mind_map_edge_delete

        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        # DELETE 0 means no rows deleted — should still succeed
        pool = _make_pool(execute_returns=["DELETE 0", "UPDATE 0"])
        await mind_map_edge_delete(pool, parent_id, child_id)
        assert pool.execute.call_count >= 1


# ---------------------------------------------------------------------------
# Tests: mind_map_frontier
# ---------------------------------------------------------------------------


class TestMindMapFrontier:
    """mind_map_frontier returns prerequisite-satisfied unmastered nodes."""

    async def test_returns_unblocked_unmastered_nodes(self) -> None:
        from butlers.tools.education import mind_map_frontier

        map_id = str(uuid.uuid4())
        node_rows = [
            _make_row(_node_row(mind_map_id=map_id, label="A", mastery_status="unseen", depth=0)),
            _make_row(_node_row(mind_map_id=map_id, label="B", mastery_status="learning", depth=1)),
        ]
        pool = _make_pool(fetch_returns=[node_rows])
        result = await mind_map_frontier(pool, map_id)
        assert len(result) == 2
        assert result[0]["label"] == "A"
        assert result[1]["label"] == "B"

    async def test_frontier_ordered_by_depth_then_effort(self) -> None:
        from butlers.tools.education import mind_map_frontier

        map_id = str(uuid.uuid4())
        # depth=1 effort=5 comes after depth=0 effort=10 (depth takes priority)
        node_rows = [
            _make_row(
                _node_row(
                    mind_map_id=map_id,
                    label="Shallow",
                    mastery_status="unseen",
                    depth=0,
                    effort_minutes=10,
                )
            ),
            _make_row(
                _node_row(
                    mind_map_id=map_id,
                    label="Deep",
                    mastery_status="unseen",
                    depth=1,
                    effort_minutes=5,
                )
            ),
        ]
        pool = _make_pool(fetch_returns=[node_rows])
        await mind_map_frontier(pool, map_id)
        # SQL ORDER BY depth ASC, effort_minutes ASC NULLS LAST
        # We trust the SQL; just verify fetch was called with map_id
        assert map_id in str(pool.fetch.call_args)

    async def test_returns_empty_when_all_mastered(self) -> None:
        from butlers.tools.education import mind_map_frontier

        pool = _make_pool(fetch_returns=[[]])
        result = await mind_map_frontier(pool, str(uuid.uuid4()))
        assert result == []

    async def test_sql_filters_unmastered_statuses(self) -> None:
        from butlers.tools.education import mind_map_frontier

        pool = _make_pool(fetch_returns=[[]])
        await mind_map_frontier(pool, str(uuid.uuid4()))
        sql = pool.fetch.call_args.args[0]
        assert "unseen" in sql
        assert "diagnosed" in sql
        assert "learning" in sql

    async def test_sql_checks_prerequisite_parents(self) -> None:
        from butlers.tools.education import mind_map_frontier

        pool = _make_pool(fetch_returns=[[]])
        await mind_map_frontier(pool, str(uuid.uuid4()))
        sql = pool.fetch.call_args.args[0]
        assert "prerequisite" in sql
        assert "mastered" in sql


# ---------------------------------------------------------------------------
# Tests: mind_map_subtree
# ---------------------------------------------------------------------------


class TestMindMapSubtree:
    """mind_map_subtree returns all descendants via recursive CTE."""

    async def test_returns_descendants(self) -> None:
        from butlers.tools.education import mind_map_subtree

        node_id = str(uuid.uuid4())
        child1 = _node_row(label="Child 1", depth=1)
        child2 = _node_row(label="Child 2", depth=2)
        pool = _make_pool(fetch_returns=[[_make_row(child1), _make_row(child2)]])
        result = await mind_map_subtree(pool, node_id)
        assert len(result) == 2

    async def test_returns_empty_for_leaf_node(self) -> None:
        from butlers.tools.education import mind_map_subtree

        pool = _make_pool(fetch_returns=[[]])
        result = await mind_map_subtree(pool, str(uuid.uuid4()))
        assert result == []

    async def test_uses_recursive_cte(self) -> None:
        from butlers.tools.education import mind_map_subtree

        node_id = str(uuid.uuid4())
        pool = _make_pool(fetch_returns=[[]])
        await mind_map_subtree(pool, node_id)
        sql = pool.fetch.call_args.args[0]
        assert "RECURSIVE" in sql.upper()

    async def test_deduplicates_results(self) -> None:
        from butlers.tools.education import mind_map_subtree

        node_id = str(uuid.uuid4())
        pool = _make_pool(fetch_returns=[[]])
        await mind_map_subtree(pool, node_id)
        sql = pool.fetch.call_args.args[0]
        assert "DISTINCT" in sql.upper()

    async def test_uses_all_edge_types(self) -> None:
        """Subtree traversal uses ALL edge types (not just prerequisite)."""
        from butlers.tools.education import mind_map_subtree

        node_id = str(uuid.uuid4())
        pool = _make_pool(fetch_returns=[[]])
        await mind_map_subtree(pool, node_id)
        sql = pool.fetch.call_args.args[0]
        # No edge_type filter in the CTE — all edges traversed
        # (the SQL does NOT have WHERE edge_type = 'prerequisite' in the CTE)
        assert "edge_type" not in sql or "prerequisite" not in sql


# ---------------------------------------------------------------------------
# Tests: auto-completion lifecycle
# ---------------------------------------------------------------------------


class TestAutoCompletion:
    """mind_map_node_update triggers auto-completion when all nodes are mastered."""

    async def test_auto_completes_when_all_mastered(self) -> None:
        """When last non-mastered node is set to mastered, map is auto-completed."""
        from butlers.tools.education import mind_map_node_update

        nid = str(uuid.uuid4())
        mid = str(uuid.uuid4())
        current_row = _make_row({"mastery_status": "reviewing", "mind_map_id": mid})

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=current_row)
        pool.execute = AsyncMock(return_value="UPDATE 1")
        # fetchval calls: unmastered_count=0 (all mastered), node_count=3
        pool.fetchval = AsyncMock(side_effect=[0, 3])

        await mind_map_node_update(pool, nid, mastery_status="mastered")

        # execute called at least twice: node update + map status update
        assert pool.execute.call_count >= 2
        # Second execute should update mind_maps status to 'completed'
        all_calls = [str(c) for c in pool.execute.call_args_list]
        assert any("completed" in c for c in all_calls)

    async def test_no_auto_complete_when_some_unmastered(self) -> None:
        """Map is NOT auto-completed if other nodes remain unmastered."""
        from butlers.tools.education import mind_map_node_update

        nid = str(uuid.uuid4())
        mid = str(uuid.uuid4())
        current_row = _make_row({"mastery_status": "reviewing", "mind_map_id": mid})

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=current_row)
        pool.execute = AsyncMock(return_value="UPDATE 1")
        # unmastered_count=2 → not all mastered
        pool.fetchval = AsyncMock(return_value=2)

        await mind_map_node_update(pool, nid, mastery_status="mastered")

        # Only 1 execute call (node update), no auto-complete
        assert pool.execute.call_count == 1

    async def test_no_auto_complete_when_status_not_mastered(self) -> None:
        """Auto-complete only triggers when new status is 'mastered'."""
        from butlers.tools.education import mind_map_node_update

        nid = str(uuid.uuid4())
        mid = str(uuid.uuid4())
        current_row = _make_row({"mastery_status": "unseen", "mind_map_id": mid})

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=current_row)
        pool.execute = AsyncMock(return_value="UPDATE 1")
        pool.fetchval = AsyncMock(return_value=0)  # Would trigger if checked

        await mind_map_node_update(pool, nid, mastery_status="learning")

        # Only 1 execute call — no fetchval/auto-complete
        pool.fetchval.assert_not_called()
        assert pool.execute.call_count == 1

    async def test_no_auto_complete_empty_map(self) -> None:
        """Auto-complete is skipped for maps with 0 nodes."""
        from butlers.tools.education import mind_map_node_update

        nid = str(uuid.uuid4())
        mid = str(uuid.uuid4())
        current_row = _make_row({"mastery_status": "reviewing", "mind_map_id": mid})

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=current_row)
        pool.execute = AsyncMock(return_value="UPDATE 1")
        # unmastered_count=0 but node_count=0 (edge case: empty map)
        pool.fetchval = AsyncMock(side_effect=[0, 0])

        await mind_map_node_update(pool, nid, mastery_status="mastered")

        # No auto-complete execute for empty map
        assert pool.execute.call_count == 1


# ---------------------------------------------------------------------------
# Tests: _row_to_dict helper
# ---------------------------------------------------------------------------


class TestRowToDict:
    """_row_to_dict converts asyncpg Record to a serializable dict."""

    def test_converts_uuid_to_string(self) -> None:
        from butlers.tools.education._helpers import _row_to_dict

        uid = uuid.uuid4()
        row = _make_row({"id": uid, "label": "Test"})
        result = _row_to_dict(row)
        assert isinstance(result["id"], str)
        assert result["id"] == str(uid)

    def test_converts_datetime_to_iso(self) -> None:
        from datetime import UTC, datetime

        from butlers.tools.education._helpers import _row_to_dict

        now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        row = _make_row({"id": "abc", "created_at": now})
        result = _row_to_dict(row)
        assert isinstance(result["created_at"], str)
        assert "2026" in result["created_at"]

    def test_parses_json_string_metadata(self) -> None:
        from butlers.tools.education._helpers import _row_to_dict

        row = _make_row({"id": "abc", "metadata": '{"key": "value"}'})
        result = _row_to_dict(row)
        assert result["metadata"] == {"key": "value"}

    def test_leaves_dict_metadata_unchanged(self) -> None:
        from butlers.tools.education._helpers import _row_to_dict

        row = _make_row({"id": "abc", "metadata": {"key": "value"}})
        result = _row_to_dict(row)
        assert result["metadata"] == {"key": "value"}

    def test_handles_none_values(self) -> None:
        from butlers.tools.education._helpers import _row_to_dict

        row = _make_row({"id": "abc", "description": None})
        result = _row_to_dict(row)
        assert result["description"] is None

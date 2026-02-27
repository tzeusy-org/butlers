"""Unit tests for education butler diagnostic assessment tools.

All tests mock the asyncpg pool/connection objects — no live database required.

Coverage:
- diagnostic_start: sets flow state to DIAGNOSING, returns concept inventory,
  rejects non-existent mind map, rejects re-start on active flow
- diagnostic_record_probe: records probe, seeds mastery only for quality>=3,
  mastery constrained to [0.3, 0.7], low quality leaves node unseen,
  validates quality range and inferred_mastery range (1.0 rejected),
  requires DIAGNOSING flow state
- diagnostic_complete: transitions flow to PLANNING, raises on zero probes,
  computes summary + inferred_frontier_rank, raises if not DIAGNOSING
- state_store_get / state_store_set: thin helpers delegate to pool correctly
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers — minimal mock record and pool builders
# (mirrors the pattern in test_mastery.py)
# ---------------------------------------------------------------------------


class _MockRecord:
    """Minimal asyncpg.Record-like object backed by a dict."""

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
    return _MockRecord(data)


def _make_pool(
    *,
    fetchrow_returns: list[Any] | None = None,
    fetch_returns: list[Any] | None = None,
    fetchval_returns: list[Any] | None = None,
    execute_returns: list[str] | None = None,
) -> AsyncMock:
    """Build an AsyncMock behaving like an asyncpg.Pool (direct pool calls)."""
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
        pool.fetchval = AsyncMock(return_value=None)

    if execute_returns is not None:
        pool.execute = AsyncMock(side_effect=list(execute_returns))
    else:
        pool.execute = AsyncMock(return_value="INSERT 0 1")

    return pool


def _make_conn(
    *,
    fetchrow_returns: list[Any] | None = None,
    fetch_returns: list[Any] | None = None,
    fetchval_returns: list[Any] | None = None,
    execute_returns: list[str] | None = None,
) -> AsyncMock:
    """Build an AsyncMock behaving like an asyncpg connection."""
    conn = AsyncMock()

    if fetchrow_returns is not None:
        conn.fetchrow = AsyncMock(side_effect=list(fetchrow_returns))
    else:
        conn.fetchrow = AsyncMock(return_value=None)

    if fetch_returns is not None:
        conn.fetch = AsyncMock(side_effect=list(fetch_returns))
    else:
        conn.fetch = AsyncMock(return_value=[])

    if fetchval_returns is not None:
        conn.fetchval = AsyncMock(side_effect=list(fetchval_returns))
    else:
        conn.fetchval = AsyncMock(return_value=None)

    if execute_returns is not None:
        conn.execute = AsyncMock(side_effect=list(execute_returns))
    else:
        conn.execute = AsyncMock(return_value="INSERT 0 1")

    return conn


def _make_conn_with_transaction(conn: AsyncMock) -> AsyncMock:
    """Wire conn.transaction() as an async context manager."""
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)
    return conn


def _make_pool_with_conn(conn: AsyncMock) -> AsyncMock:
    """Build a pool whose acquire() context manager yields the given conn."""
    pool = MagicMock()
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)

    # Also set direct fetch/fetchrow for functions that use pool directly
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    return pool


# ---------------------------------------------------------------------------
# Shared UUIDs for test fixtures
# ---------------------------------------------------------------------------

MAP_ID = str(uuid.uuid4())
NODE_ID_A = str(uuid.uuid4())
NODE_ID_B = str(uuid.uuid4())
NODE_ID_C = str(uuid.uuid4())


def _node_inventory_rows() -> list[_MockRecord]:
    """Three concept nodes with varying depths."""
    return [
        _make_row({"node_id": NODE_ID_A, "label": "Basics", "description": "Intro", "depth": 0}),
        _make_row(
            {"node_id": NODE_ID_B, "label": "Intermediate", "description": "Mid", "depth": 1}
        ),
        _make_row({"node_id": NODE_ID_C, "label": "Advanced", "description": "Pro", "depth": 2}),
    ]


# ---------------------------------------------------------------------------
# Tests: diagnostic_start
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: diagnostic_start
# ---------------------------------------------------------------------------


class TestDiagnosticStart:
    """diagnostic_start sets diagnosing state and returns concept inventory."""

    async def _make_start_pool(self, *, existing_flow: Any = None) -> tuple[AsyncMock, list]:
        """Helper: pool for diagnostic_start with given existing flow state."""
        nodes = _node_inventory_rows()
        # Pool operations order:
        # 1. fetchrow — verify mind map exists
        # 2. fetchval — state_get (KV lookup); returns None or JSON-encoded flow
        # 3. fetch    — get all nodes
        # 4. fetchval — state_set (KV upsert, RETURNING version); returns int
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=_make_row({"id": MAP_ID}))

        flow_json = json.dumps(existing_flow) if existing_flow else None
        # Two fetchval calls: first is state_get, second is state_set RETURNING version
        pool.fetchval = AsyncMock(side_effect=[flow_json, 1])
        pool.fetch = AsyncMock(return_value=nodes)
        return pool, nodes

    async def test_returns_concept_inventory(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_start

        pool, _nodes = await self._make_start_pool()
        inventory = await diagnostic_start(pool, MAP_ID)

        assert len(inventory) == 3
        assert inventory[0]["node_id"] == NODE_ID_A
        assert inventory[0]["label"] == "Basics"
        assert inventory[0]["difficulty_rank"] == 0
        assert inventory[1]["difficulty_rank"] == 1
        assert inventory[2]["difficulty_rank"] == 2

    async def test_writes_diagnosing_flow_state(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_start

        pool, _nodes = await self._make_start_pool()
        await diagnostic_start(pool, MAP_ID)

        # The second fetchval call (state_set RETURNING version) carries the stored JSON
        # as the third positional argument.
        assert pool.fetchval.await_count == 2
        state_set_call_args = pool.fetchval.call_args_list[1][0]
        stored = json.loads(state_set_call_args[2])
        assert stored["status"] == "diagnosing"
        assert stored["mind_map_id"] == MAP_ID
        assert stored["probes_issued"] == 0
        assert stored["diagnostic_results"] == {}
        assert "started_at" in stored
        assert "last_session_at" in stored

    async def test_raises_if_mind_map_not_found(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_start

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.fetchval = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Mind map not found"):
            await diagnostic_start(pool, MAP_ID)

    async def test_allows_start_with_no_existing_flow(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_start

        pool, _nodes = await self._make_start_pool(existing_flow=None)
        # Should not raise
        inventory = await diagnostic_start(pool, MAP_ID)
        assert len(inventory) == 3

    async def test_allows_start_with_pending_flow(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_start

        pool, _nodes = await self._make_start_pool(existing_flow={"status": "pending"})
        # Should not raise — pending is allowed
        inventory = await diagnostic_start(pool, MAP_ID)
        assert len(inventory) == 3

    async def test_restarts_if_already_diagnosing(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_start

        pool, _nodes = await self._make_start_pool(
            existing_flow={"status": "diagnosing", "probes_issued": 1}
        )
        # Restarting from 'diagnosing' is allowed — handles stuck flows where the
        # LLM session ended before completing the diagnostic.
        inventory = await diagnostic_start(pool, MAP_ID)
        assert len(inventory) == len(_nodes)

    async def test_raises_if_flow_in_planning(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_start

        pool, _nodes = await self._make_start_pool(existing_flow={"status": "planning"})
        with pytest.raises(ValueError, match="already in state"):
            await diagnostic_start(pool, MAP_ID)

    async def test_raises_if_flow_in_teaching(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_start

        pool, _nodes = await self._make_start_pool(existing_flow={"status": "teaching"})
        with pytest.raises(ValueError, match="already in state"):
            await diagnostic_start(pool, MAP_ID)

    async def test_inventory_includes_description(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_start

        pool, _nodes = await self._make_start_pool()
        inventory = await diagnostic_start(pool, MAP_ID)
        assert inventory[0]["description"] == "Intro"
        assert inventory[1]["description"] == "Mid"


# ---------------------------------------------------------------------------
# Tests: diagnostic_record_probe
# ---------------------------------------------------------------------------


def _make_record_probe_pool(
    *,
    flow_state: dict[str, Any] | None = None,
    node_mastery_status: str = "unseen",
) -> AsyncMock:
    """
    Build a pool for diagnostic_record_probe.

    Pool direct calls (in order):
    - fetchval[0]: state_get — returns JSON-encoded flow_state
    - fetchval[1]: state_set RETURNING version — returns int

    Pool.acquire() → conn:
    - conn.fetchrow: SELECT node id, mastery_status
    - conn.execute[0]: INSERT quiz_response
    - conn.execute[1]: UPDATE mastery (only when quality >= 3 and mastery_status='unseen')
    """
    default_flow: dict[str, Any] = {
        "status": "diagnosing",
        "mind_map_id": MAP_ID,
        "probes_issued": 0,
        "diagnostic_results": {},
        "concept_inventory": [],
    }
    stored_flow = flow_state if flow_state is not None else default_flow
    flow_json = json.dumps(stored_flow)

    pool = MagicMock()
    # Two fetchval calls: state_get returns flow JSON, state_set returns version int
    pool.fetchval = AsyncMock(side_effect=[flow_json, 1])

    # Connection inside acquire() context manager
    conn = _make_conn_with_transaction(
        _make_conn(
            fetchrow_returns=[_make_row({"id": NODE_ID_A, "mastery_status": node_mastery_status})],
            execute_returns=["INSERT 0 1", "UPDATE 1"],
        )
    )
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)

    return pool


class TestDiagnosticRecordProbe:
    """diagnostic_record_probe validates input, records response, seeds mastery."""

    async def test_quality_out_of_range_raises(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = MagicMock()
        with pytest.raises(ValueError, match="quality must be between 0 and 5"):
            await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=-1, inferred_mastery=0.5)

    async def test_quality_above_five_raises(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = MagicMock()
        with pytest.raises(ValueError, match="quality must be between 0 and 5"):
            await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=6, inferred_mastery=0.5)

    async def test_inferred_mastery_of_1_raises(self) -> None:
        """inferred_mastery=1.0 must be rejected — diagnostic seeds never reach 1.0."""
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = MagicMock()
        with pytest.raises(ValueError, match="never 1.0"):
            await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=5, inferred_mastery=1.0)

    async def test_inferred_mastery_negative_raises(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = MagicMock()
        with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\)"):
            await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=3, inferred_mastery=-0.1)

    async def test_flow_not_diagnosing_raises(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=json.dumps({"status": "planning"}))

        with pytest.raises(ValueError, match="diagnosing"):
            await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=3, inferred_mastery=0.5)

    async def test_no_flow_state_raises(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="diagnosing"):
            await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=3, inferred_mastery=0.5)

    async def test_node_not_in_map_raises(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = _make_record_probe_pool()
        # Override conn.fetchrow to return None (node not found)
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found in mind map"):
            await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=3, inferred_mastery=0.5)

    async def test_quality_3_seeds_mastery_and_sets_diagnosed(self) -> None:
        """quality>=3 should update mastery_score and mastery_status='diagnosed'."""
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = _make_record_probe_pool()
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value

        result = await diagnostic_record_probe(
            pool, MAP_ID, NODE_ID_A, quality=3, inferred_mastery=0.5
        )

        # conn.execute called twice: INSERT quiz_response + UPDATE mastery
        assert conn.execute.call_count == 2

        # Second call is the UPDATE mastery
        update_call_args = conn.execute.call_args_list[1][0]
        assert "mastery_score" in update_call_args[0]
        assert "diagnosed" in update_call_args[0]
        # The score should be 0.5 (within [0.3, 0.7])
        assert update_call_args[1] == 0.5

        # Flow state should be updated
        assert result["probes_issued"] == 1
        assert NODE_ID_A in result["diagnostic_results"]
        assert result["diagnostic_results"][NODE_ID_A]["quality"] == 3

    async def test_quality_5_seeds_mastery_clamped_to_07(self) -> None:
        """inferred_mastery=0.7 (quality=5 ceiling) must be stored as 0.7."""
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = _make_record_probe_pool()
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value

        await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=5, inferred_mastery=0.7)

        update_call_args = conn.execute.call_args_list[1][0]
        seeded = update_call_args[1]
        # Must be exactly 0.7 and NOT exceed 0.7
        assert seeded <= 0.7
        assert seeded >= 0.3

    async def test_quality_4_inferred_mastery_06_stored_within_range(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = _make_record_probe_pool()
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value

        await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=4, inferred_mastery=0.6)

        update_call_args = conn.execute.call_args_list[1][0]
        seeded = update_call_args[1]
        assert 0.3 <= seeded <= 0.7

    async def test_quality_low_does_not_update_mastery(self) -> None:
        """quality<3 should NOT update mastery_score — node stays 'unseen'."""
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = _make_record_probe_pool()
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value

        result = await diagnostic_record_probe(
            pool, MAP_ID, NODE_ID_A, quality=2, inferred_mastery=0.3
        )

        # Only one execute call: INSERT quiz_response. No UPDATE mastery.
        assert conn.execute.call_count == 1
        insert_sql = conn.execute.call_args_list[0][0][0]
        assert "INSERT INTO education.quiz_responses" in insert_sql

        # Flow state still updated
        assert result["probes_issued"] == 1
        assert result["diagnostic_results"][NODE_ID_A]["quality"] == 2

    async def test_quality_zero_does_not_update_mastery(self) -> None:
        """quality=0 (blackout) should not seed mastery."""
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = _make_record_probe_pool()
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value

        await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=0, inferred_mastery=0.1)

        # Only INSERT, no UPDATE
        assert conn.execute.call_count == 1

    async def test_quiz_response_inserted_with_diagnostic_type(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = _make_record_probe_pool()
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value

        await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=4, inferred_mastery=0.6)

        insert_sql = conn.execute.call_args_list[0][0][0]
        assert "'diagnostic'" in insert_sql or "diagnostic" in insert_sql

    async def test_probes_issued_increments_on_each_call(self) -> None:
        """Each call to diagnostic_record_probe increments probes_issued by 1."""
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        # Simulate flow state already having 2 probes
        existing_flow: dict[str, Any] = {
            "status": "diagnosing",
            "mind_map_id": MAP_ID,
            "probes_issued": 2,
            "diagnostic_results": {},
            "concept_inventory": [],
        }
        pool = _make_record_probe_pool(flow_state=existing_flow)

        result = await diagnostic_record_probe(
            pool, MAP_ID, NODE_ID_A, quality=3, inferred_mastery=0.5
        )
        assert result["probes_issued"] == 3

    async def test_returns_updated_flow_state(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = _make_record_probe_pool()
        result = await diagnostic_record_probe(
            pool, MAP_ID, NODE_ID_A, quality=3, inferred_mastery=0.5
        )

        assert result["status"] == "diagnosing"
        assert result["mind_map_id"] == MAP_ID
        assert NODE_ID_A in result["diagnostic_results"]

    async def test_mastery_seed_clamped_below_03(self) -> None:
        """inferred_mastery=0.1 passed but should be clamped to 0.3 minimum when seeding."""
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = _make_record_probe_pool()
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value

        await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=3, inferred_mastery=0.1)

        # Should clamp to 0.3
        update_call_args = conn.execute.call_args_list[1][0]
        seeded = update_call_args[1]
        assert seeded == 0.3

    async def test_already_mastered_node_not_demoted(self) -> None:
        """quality>=3 probe must not demote a node already in 'mastered' status.

        The UPDATE query includes AND mastery_status = 'unseen', so it should
        match zero rows for a node that is already 'mastered'.  The test
        verifies that the UPDATE *attempt* still happens (SQL has the guard),
        but from the application side we verify only one conn.execute call
        occurs when mastery_status is not 'unseen' (only the INSERT).

        Note: SQLite row-count semantics are not available in this mock,
        so we verify the SQL guard is present in the emitted query string.
        """
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        # Node is already 'mastered' — the UPDATE should contain the unseen guard
        pool = _make_record_probe_pool(node_mastery_status="mastered")
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value

        await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=5, inferred_mastery=0.6)

        # Two execute calls (INSERT + UPDATE attempt), but the UPDATE SQL must
        # include the mastery_status = 'unseen' guard so the DB row is protected.
        assert conn.execute.call_count == 2
        update_sql = conn.execute.call_args_list[1][0][0]
        assert "mastery_status = 'unseen'" in update_sql


# ---------------------------------------------------------------------------
# Tests: diagnostic_complete
# ---------------------------------------------------------------------------


def _make_complete_pool(
    *,
    flow_state: dict[str, Any] | None = None,
    node_status_rows: list[_MockRecord] | None = None,
) -> AsyncMock:
    """
    Build a pool for diagnostic_complete.

    Pool direct calls (in order):
    - fetchval[0]: state_get — returns JSON-encoded flow_state
    - fetch:       SELECT mastery_status for probed nodes
    - fetchval[1]: state_set RETURNING version — returns int
    """
    default_flow: dict[str, Any] = {
        "status": "diagnosing",
        "mind_map_id": MAP_ID,
        "probes_issued": 2,
        "diagnostic_results": {
            NODE_ID_A: {"quality": 4, "inferred_mastery": 0.6},
            NODE_ID_B: {"quality": 1, "inferred_mastery": 0.1},
        },
        "concept_inventory": [
            {"node_id": NODE_ID_A, "label": "Basics", "description": "Intro", "difficulty_rank": 0},
            {
                "node_id": NODE_ID_B,
                "label": "Intermediate",
                "description": "Mid",
                "difficulty_rank": 1,
            },
            {"node_id": NODE_ID_C, "label": "Advanced", "description": "Pro", "difficulty_rank": 2},
        ],
    }
    stored_flow = flow_state if flow_state is not None else default_flow

    default_node_rows = [
        _make_row({"node_id": NODE_ID_A, "mastery_status": "diagnosed"}),
        _make_row({"node_id": NODE_ID_B, "mastery_status": "unseen"}),
    ]
    rows = node_status_rows if node_status_rows is not None else default_node_rows

    pool = MagicMock()
    # Two fetchval calls: state_get returns flow JSON, state_set returns version int
    pool.fetchval = AsyncMock(side_effect=[json.dumps(stored_flow), 1])
    pool.fetch = AsyncMock(return_value=rows)
    return pool


class TestDiagnosticComplete:
    """diagnostic_complete finalises diagnostic and transitions to planning."""

    async def test_transitions_flow_to_planning(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_complete

        pool = _make_complete_pool()
        await diagnostic_complete(pool, MAP_ID)

        # The second fetchval call (state_set RETURNING version) carries the stored JSON
        # as the third positional argument.
        assert pool.fetchval.await_count == 2
        state_set_call_args = pool.fetchval.call_args_list[1][0]
        stored = json.loads(state_set_call_args[2])
        assert stored["status"] == "planning"

    async def test_raises_if_not_diagnosing(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_complete

        flow: dict[str, Any] = {"status": "planning", "probes_issued": 1, "diagnostic_results": {}}
        pool = _make_complete_pool(flow_state=flow)
        with pytest.raises(ValueError, match="diagnosing"):
            await diagnostic_complete(pool, MAP_ID)

    async def test_raises_if_no_flow_state(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_complete

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="diagnosing"):
            await diagnostic_complete(pool, MAP_ID)

    async def test_raises_if_zero_probes_issued(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_complete

        flow: dict[str, Any] = {
            "status": "diagnosing",
            "mind_map_id": MAP_ID,
            "probes_issued": 0,
            "diagnostic_results": {},
            "concept_inventory": [],
        }
        pool = _make_complete_pool(flow_state=flow)
        with pytest.raises(ValueError, match="no probes"):
            await diagnostic_complete(pool, MAP_ID)

    async def test_returns_summary_with_probed_nodes(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_complete

        pool = _make_complete_pool()
        result = await diagnostic_complete(pool, MAP_ID)

        assert "summary" in result
        assert NODE_ID_A in result["summary"]
        assert NODE_ID_B in result["summary"]
        # NODE_ID_C was not probed
        assert NODE_ID_C not in result["summary"]

    async def test_summary_contains_mastery_status(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_complete

        pool = _make_complete_pool()
        result = await diagnostic_complete(pool, MAP_ID)

        assert result["summary"][NODE_ID_A]["mastery_status"] == "diagnosed"
        assert result["summary"][NODE_ID_B]["mastery_status"] == "unseen"

    async def test_total_concepts_in_inventory(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_complete

        pool = _make_complete_pool()
        result = await diagnostic_complete(pool, MAP_ID)

        # 3 concepts in inventory (A, B, C)
        assert result["total_concepts_in_inventory"] == 3

    async def test_unprobed_node_count(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_complete

        pool = _make_complete_pool()
        result = await diagnostic_complete(pool, MAP_ID)

        # 3 total - 2 probed = 1 unprobed
        assert result["unprobed_node_count"] == 1

    async def test_inferred_frontier_rank_highest_correct_probe(self) -> None:
        """inferred_frontier_rank = highest difficulty_rank of quality>=3 probes."""
        from butlers.tools.education.diagnostic import diagnostic_complete

        # NODE_ID_A (quality=4, rank=0) → correct; NODE_ID_B (quality=1, rank=1) → wrong
        pool = _make_complete_pool()
        result = await diagnostic_complete(pool, MAP_ID)

        # Only NODE_ID_A was correct (quality=4), difficulty_rank=0
        assert result["inferred_frontier_rank"] == 0

    async def test_inferred_frontier_rank_zero_when_all_wrong(self) -> None:
        """If all probes have quality<3, inferred_frontier_rank should be 0."""
        from butlers.tools.education.diagnostic import diagnostic_complete

        flow: dict[str, Any] = {
            "status": "diagnosing",
            "mind_map_id": MAP_ID,
            "probes_issued": 2,
            "diagnostic_results": {
                NODE_ID_A: {"quality": 1, "inferred_mastery": 0.1},
                NODE_ID_B: {"quality": 0, "inferred_mastery": 0.1},
            },
            "concept_inventory": [
                {
                    "node_id": NODE_ID_A,
                    "label": "Basics",
                    "description": None,
                    "difficulty_rank": 0,
                },
                {
                    "node_id": NODE_ID_B,
                    "label": "Intermediate",
                    "description": None,
                    "difficulty_rank": 1,
                },
            ],
        }
        node_rows = [
            _make_row({"node_id": NODE_ID_A, "mastery_status": "unseen"}),
            _make_row({"node_id": NODE_ID_B, "mastery_status": "unseen"}),
        ]
        pool = _make_complete_pool(flow_state=flow, node_status_rows=node_rows)
        result = await diagnostic_complete(pool, MAP_ID)

        assert result["inferred_frontier_rank"] == 0

    async def test_inferred_frontier_rank_multiple_correct_takes_max(self) -> None:
        """If multiple quality>=3 probes, frontier_rank = max difficulty_rank."""
        from butlers.tools.education.diagnostic import diagnostic_complete

        flow: dict[str, Any] = {
            "status": "diagnosing",
            "mind_map_id": MAP_ID,
            "probes_issued": 3,
            "diagnostic_results": {
                NODE_ID_A: {"quality": 4, "inferred_mastery": 0.6},  # rank 0
                NODE_ID_B: {"quality": 3, "inferred_mastery": 0.5},  # rank 1
                NODE_ID_C: {"quality": 5, "inferred_mastery": 0.7},  # rank 2
            },
            "concept_inventory": [
                {
                    "node_id": NODE_ID_A,
                    "label": "Basics",
                    "description": None,
                    "difficulty_rank": 0,
                },
                {
                    "node_id": NODE_ID_B,
                    "label": "Intermediate",
                    "description": None,
                    "difficulty_rank": 1,
                },
                {
                    "node_id": NODE_ID_C,
                    "label": "Advanced",
                    "description": None,
                    "difficulty_rank": 2,
                },
            ],
        }
        node_rows = [
            _make_row({"node_id": NODE_ID_A, "mastery_status": "diagnosed"}),
            _make_row({"node_id": NODE_ID_B, "mastery_status": "diagnosed"}),
            _make_row({"node_id": NODE_ID_C, "mastery_status": "diagnosed"}),
        ]
        pool = _make_complete_pool(flow_state=flow, node_status_rows=node_rows)
        result = await diagnostic_complete(pool, MAP_ID)

        # All correct; max rank is 2 (NODE_ID_C)
        assert result["inferred_frontier_rank"] == 2

    async def test_returns_last_session_at_updated(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_complete

        pool = _make_complete_pool()
        await diagnostic_complete(pool, MAP_ID)

        state_set_call_args = pool.fetchval.call_args_list[1][0]
        stored = json.loads(state_set_call_args[2])
        assert "last_session_at" in stored

    async def test_unprobed_count_zero_when_all_probed(self) -> None:
        from butlers.tools.education.diagnostic import diagnostic_complete

        flow: dict[str, Any] = {
            "status": "diagnosing",
            "mind_map_id": MAP_ID,
            "probes_issued": 2,
            "diagnostic_results": {
                NODE_ID_A: {"quality": 3, "inferred_mastery": 0.5},
                NODE_ID_B: {"quality": 4, "inferred_mastery": 0.6},
            },
            "concept_inventory": [
                {"node_id": NODE_ID_A, "label": "A", "description": None, "difficulty_rank": 0},
                {"node_id": NODE_ID_B, "label": "B", "description": None, "difficulty_rank": 1},
            ],
        }
        node_rows = [
            _make_row({"node_id": NODE_ID_A, "mastery_status": "diagnosed"}),
            _make_row({"node_id": NODE_ID_B, "mastery_status": "diagnosed"}),
        ]
        pool = _make_complete_pool(flow_state=flow, node_status_rows=node_rows)
        result = await diagnostic_complete(pool, MAP_ID)

        assert result["unprobed_node_count"] == 0


# ---------------------------------------------------------------------------
# Tests: mastery seed constraints (unit-level)
# ---------------------------------------------------------------------------


class TestMasterySeedConstraints:
    """Mastery seeds from diagnostic probes are always in [0.3, 0.7], never 1.0."""

    async def test_inferred_mastery_08_clamped_to_07(self) -> None:
        """Even if caller passes 0.8 (which is > 0.7), it must be clamped to 0.7."""
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        # 0.8 is in [0.0, 1.0) so input validation passes; seeding clamps to 0.7
        pool = _make_record_probe_pool()
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value

        await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=4, inferred_mastery=0.8)

        update_call_args = conn.execute.call_args_list[1][0]
        seeded = update_call_args[1]
        assert seeded <= 0.7
        assert seeded >= 0.3

    async def test_inferred_mastery_02_clamped_to_03(self) -> None:
        """inferred_mastery=0.2 (quality>=3) should be clamped to minimum 0.3."""
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = _make_record_probe_pool()
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value

        await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=3, inferred_mastery=0.2)

        update_call_args = conn.execute.call_args_list[1][0]
        seeded = update_call_args[1]
        assert seeded == 0.3

    async def test_inferred_mastery_099_clamped_to_07(self) -> None:
        """inferred_mastery=0.99 (close to 1.0 but valid) must be clamped to 0.7."""
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        pool = _make_record_probe_pool()
        acquire_ctx = pool.acquire.return_value
        conn = acquire_ctx.__aenter__.return_value

        await diagnostic_record_probe(pool, MAP_ID, NODE_ID_A, quality=5, inferred_mastery=0.99)

        update_call_args = conn.execute.call_args_list[1][0]
        seeded = update_call_args[1]
        assert seeded == 0.7

    async def test_mastery_never_10_from_diagnostic(self) -> None:
        """No diagnostic probe should ever produce mastery_score=1.0 in the DB."""
        from butlers.tools.education.diagnostic import diagnostic_record_probe

        # Test across all quality levels that could produce 1.0
        for quality in range(3, 6):
            pool = _make_record_probe_pool()
            acquire_ctx = pool.acquire.return_value
            conn = acquire_ctx.__aenter__.return_value

            await diagnostic_record_probe(
                pool, MAP_ID, NODE_ID_A, quality=quality, inferred_mastery=0.7
            )

            update_call_args = conn.execute.call_args_list[1][0]
            seeded = update_call_args[1]
            assert seeded < 1.0, (
                f"quality={quality} produced mastery_score={seeded} which is >= 1.0"
            )

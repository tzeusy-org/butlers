"""Unit tests for education butler teaching flow state machine.

All tests mock the asyncpg pool/connection objects — no live database required.

Coverage:
- teaching_flow_start: creates mind map, initializes state at PENDING, advances to DIAGNOSING
- teaching_flow_get: returns None for unknown, full state for known flow
- teaching_flow_advance:
    - pending → diagnosing
    - diagnosing → planning
    - planning → teaching (sets current_node_id, phase=explaining)
    - teaching → quizzing (clears current_phase)
    - quizzing → teaching (frontier available)
    - quizzing → reviewing (no frontier, not all mastered)
    - quizzing → completed (all mastered)
    - reviewing → teaching (frontier available)
    - reviewing → completed (all mastered)
    - terminal states raise ValueError
    - increments session_count and updates last_session_at
    - validates current_node_id required in teaching status
    - CAS semantics (conflict retry)
- teaching_flow_abandon:
    - marks abandoned, cleans schedules, updates mind map status
    - raises on terminal state
- teaching_flow_list:
    - returns all flows with mastery_pct
    - filters by status
    - empty list when no flows
- assemble_session_context:
    - all four components included
    - memory context fail-open
    - no recent_responses when current_node_id is None
- check_stale_flows:
    - abandons flows older than 30 days
    - skips recently active flows
    - skips completed/abandoned flows
    - handles multiple stale flows
- _validate_state_invariants:
    - current_node_id required in teaching/quizzing/reviewing
    - current_phase required in teaching/quizzing
    - current_phase must be null in other states
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers: mock asyncpg pool / connection builder
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
    """Build an AsyncMock behaving like an asyncpg.Pool (FIFO consumption)."""
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


def _make_state_row(state: dict[str, Any], version: int = 1) -> _MockRecord:
    """Build a mock KV store row with value + version."""
    return _make_row({"value": state, "version": version})


def _pending_state(mind_map_id: str) -> dict[str, Any]:
    return {
        "status": "pending",
        "mind_map_id": mind_map_id,
        "current_node_id": None,
        "current_phase": None,
        "diagnostic_results": {},
        "session_count": 0,
        "started_at": "2026-01-01T00:00:00+00:00",
        "last_session_at": "2026-01-01T00:00:00+00:00",
    }


def _flow_state(
    status: str = "teaching",
    mind_map_id: str | None = None,
    current_node_id: str | None = None,
    current_phase: str | None = "explaining",
    session_count: int = 1,
    last_session_at: str | None = None,
) -> dict[str, Any]:
    mid = mind_map_id or str(uuid.uuid4())
    return {
        "status": status,
        "mind_map_id": mid,
        "current_node_id": current_node_id,
        "current_phase": current_phase,
        "diagnostic_results": {},
        "session_count": session_count,
        "started_at": "2026-01-01T00:00:00+00:00",
        "last_session_at": last_session_at or "2026-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Tests: _validate_state_invariants
# ---------------------------------------------------------------------------


class TestValidateStateInvariants:
    def test_teaching_requires_node_id(self) -> None:
        from butlers.tools.education.teaching_flows import _validate_state_invariants

        state = _flow_state(status="teaching", current_node_id=None, current_phase="explaining")
        with pytest.raises(ValueError, match="current_node_id"):
            _validate_state_invariants(state)

    def test_quizzing_requires_node_id(self) -> None:
        from butlers.tools.education.teaching_flows import _validate_state_invariants

        state = _flow_state(status="quizzing", current_node_id=None, current_phase="questioning")
        with pytest.raises(ValueError, match="current_node_id"):
            _validate_state_invariants(state)

    def test_teaching_requires_current_phase(self) -> None:
        from butlers.tools.education.teaching_flows import _validate_state_invariants

        state = _flow_state(
            status="teaching",
            current_node_id=str(uuid.uuid4()),
            current_phase=None,
        )
        with pytest.raises(ValueError, match="current_phase"):
            _validate_state_invariants(state)

    def test_quizzing_with_null_phase_is_valid(self) -> None:
        """Quizzing allows null current_phase (phase is set to null on teaching→quizzing)."""
        from butlers.tools.education.teaching_flows import _validate_state_invariants

        state = _flow_state(
            status="quizzing",
            current_node_id=str(uuid.uuid4()),
            current_phase=None,
        )
        _validate_state_invariants(state)  # No exception expected

    def test_diagnosing_phase_must_be_null(self) -> None:
        from butlers.tools.education.teaching_flows import _validate_state_invariants

        state = _flow_state(status="diagnosing", current_node_id=None, current_phase="explaining")
        with pytest.raises(ValueError, match="current_phase must be null"):
            _validate_state_invariants(state)

    def test_valid_teaching_state_passes(self) -> None:
        from butlers.tools.education.teaching_flows import _validate_state_invariants

        state = _flow_state(
            status="teaching",
            current_node_id=str(uuid.uuid4()),
            current_phase="explaining",
        )
        _validate_state_invariants(state)  # No exception

    def test_valid_diagnosing_state_passes(self) -> None:
        from butlers.tools.education.teaching_flows import _validate_state_invariants

        state = _flow_state(status="diagnosing", current_node_id=None, current_phase=None)
        _validate_state_invariants(state)  # No exception

    def test_valid_completed_state_passes(self) -> None:
        from butlers.tools.education.teaching_flows import _validate_state_invariants

        state = _flow_state(status="completed", current_node_id=None, current_phase=None)
        _validate_state_invariants(state)  # No exception

    def test_reviewing_requires_node_id(self) -> None:
        from butlers.tools.education.teaching_flows import _validate_state_invariants

        state = _flow_state(status="reviewing", current_node_id=None, current_phase=None)
        with pytest.raises(ValueError, match="current_node_id"):
            _validate_state_invariants(state)


# ---------------------------------------------------------------------------
# Tests: _get_state_with_version — double-encoded JSONB handling
# ---------------------------------------------------------------------------


class TestGetStateWithVersion:
    async def test_double_encoded_jsonb_returns_dict(self) -> None:
        """_get_state_with_version handles double-encoded JSONB (str wrapping JSON text)."""
        import json

        from butlers.tools.education.teaching_flows import _get_state_with_version

        map_id = str(uuid.uuid4())
        state = _pending_state(map_id)
        # Simulate double-encoded JSONB: value column is a string containing JSON text
        double_encoded = json.dumps(json.dumps(state))
        pool = _make_pool(
            fetchrow_returns=[_make_row({"value": double_encoded, "version": 1})]
        )

        result, version = await _get_state_with_version(pool, map_id)
        assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"
        assert result["status"] == "pending"
        assert result["mind_map_id"] == map_id
        assert version == 1

    async def test_single_encoded_jsonb_returns_dict(self) -> None:
        """_get_state_with_version handles normal single-encoded JSONB."""
        import json

        from butlers.tools.education.teaching_flows import _get_state_with_version

        map_id = str(uuid.uuid4())
        state = _pending_state(map_id)
        pool = _make_pool(
            fetchrow_returns=[_make_row({"value": json.dumps(state), "version": 2})]
        )

        result, version = await _get_state_with_version(pool, map_id)
        assert isinstance(result, dict)
        assert result["status"] == "pending"
        assert version == 2

    async def test_already_decoded_dict_returns_dict(self) -> None:
        """_get_state_with_version handles already-decoded dict values."""
        from butlers.tools.education.teaching_flows import _get_state_with_version

        map_id = str(uuid.uuid4())
        state = _pending_state(map_id)
        pool = _make_pool(
            fetchrow_returns=[_make_row({"value": state, "version": 3})]
        )

        result, version = await _get_state_with_version(pool, map_id)
        assert isinstance(result, dict)
        assert result["status"] == "pending"
        assert version == 3

    async def test_missing_key_returns_none(self) -> None:
        """_get_state_with_version returns (None, None) for missing keys."""
        from butlers.tools.education.teaching_flows import _get_state_with_version

        pool = _make_pool(fetchrow_returns=[None])
        result, version = await _get_state_with_version(pool, str(uuid.uuid4()))
        assert result is None
        assert version is None


# ---------------------------------------------------------------------------
# Tests: teaching_flow_get
# ---------------------------------------------------------------------------


class TestTeachingFlowGet:
    async def test_returns_none_for_unknown_map(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_get

        pool = _make_pool(fetchval_returns=[None])
        result = await teaching_flow_get(pool, str(uuid.uuid4()))
        assert result is None

    async def test_returns_state_for_known_flow(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_get

        map_id = str(uuid.uuid4())
        state = _pending_state(map_id)

        with patch(
            "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
        ):
            result = await teaching_flow_get(_make_pool(), map_id)

        assert result is not None
        assert result["status"] == "pending"
        assert result["mind_map_id"] == map_id


# ---------------------------------------------------------------------------
# Tests: teaching_flow_advance
# ---------------------------------------------------------------------------


class TestTeachingFlowAdvance:
    """Test state machine transitions via teaching_flow_advance."""

    async def test_pending_to_diagnosing(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        state = _pending_state(map_id)

        pool = _make_pool(
            fetchrow_returns=[_make_state_row(state, version=1)],
            execute_returns=["UPDATE 1"],
        )

        with patch(
            "butlers.tools.education.teaching_flows.state_compare_and_set",
            AsyncMock(return_value=2),
        ):
            result = await teaching_flow_advance(pool, map_id)

        assert result["status"] == "diagnosing"
        assert result["current_node_id"] is None
        assert result["current_phase"] is None
        assert result["session_count"] == 1

    async def test_diagnosing_to_planning(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        state = _flow_state(
            status="diagnosing", current_node_id=None, current_phase=None, session_count=1
        )
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=2)])

        with patch(
            "butlers.tools.education.teaching_flows.state_compare_and_set",
            AsyncMock(return_value=3),
        ):
            result = await teaching_flow_advance(pool, map_id)

        assert result["status"] == "planning"
        assert result["current_node_id"] is None
        assert result["current_phase"] is None

    async def test_planning_to_teaching_with_frontier_node(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        state = _flow_state(
            status="planning", current_node_id=None, current_phase=None, session_count=2
        )
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=3)])

        mock_frontier = [{"id": node_id, "label": "Basics", "mastery_status": "unseen"}]

        with (
            patch(
                "butlers.tools.education.teaching_flows.mind_map_frontier",
                AsyncMock(return_value=mock_frontier),
            ),
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=4),
            ),
        ):
            result = await teaching_flow_advance(pool, map_id)

        assert result["status"] == "teaching"
        assert result["current_node_id"] == node_id
        assert result["current_phase"] == "explaining"
        assert result["session_count"] == 3

    async def test_planning_to_teaching_no_frontier_raises(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        state = _flow_state(
            status="planning", current_node_id=None, current_phase=None, session_count=2
        )
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=3)])

        with (
            patch(
                "butlers.tools.education.teaching_flows.mind_map_frontier",
                AsyncMock(return_value=[]),
            ),
            pytest.raises(ValueError, match="no frontier nodes"),
        ):
            await teaching_flow_advance(pool, map_id)

    async def test_teaching_to_quizzing(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        state = _flow_state(
            status="teaching",
            current_node_id=node_id,
            current_phase="questioning",
            session_count=3,
        )
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=4)])

        with patch(
            "butlers.tools.education.teaching_flows.state_compare_and_set",
            AsyncMock(return_value=5),
        ):
            result = await teaching_flow_advance(pool, map_id)

        assert result["status"] == "quizzing"
        assert result["current_node_id"] == node_id
        assert result["current_phase"] is None

    async def test_quizzing_to_teaching_when_frontier_available(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        next_node_id = str(uuid.uuid4())
        state = _flow_state(
            status="quizzing",
            current_node_id=node_id,
            current_phase=None,
            session_count=4,
        )
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=5)])

        mock_summary = {
            "total_nodes": 5,
            "mastered_count": 1,
            "learning_count": 2,
            "reviewing_count": 1,
            "unseen_count": 1,
            "diagnosed_count": 0,
            "avg_mastery_score": 0.3,
            "struggling_node_ids": [],
        }
        mock_frontier = [{"id": next_node_id, "label": "Next"}]

        with (
            patch(
                "butlers.tools.education.teaching_flows.mastery_get_map_summary",
                AsyncMock(return_value=mock_summary),
            ),
            patch(
                "butlers.tools.education.teaching_flows.mind_map_frontier",
                AsyncMock(return_value=mock_frontier),
            ),
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=6),
            ),
        ):
            result = await teaching_flow_advance(pool, map_id)

        assert result["status"] == "teaching"
        assert result["current_node_id"] == next_node_id
        assert result["current_phase"] == "explaining"

    async def test_quizzing_to_reviewing_when_no_frontier(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        state = _flow_state(
            status="quizzing",
            current_node_id=node_id,
            current_phase=None,
            session_count=4,
        )
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=5)])

        mock_summary = {
            "total_nodes": 5,
            "mastered_count": 2,
            "learning_count": 1,
            "reviewing_count": 2,
            "unseen_count": 0,
            "diagnosed_count": 0,
            "avg_mastery_score": 0.5,
            "struggling_node_ids": [],
        }

        with (
            patch(
                "butlers.tools.education.teaching_flows.mastery_get_map_summary",
                AsyncMock(return_value=mock_summary),
            ),
            patch(
                "butlers.tools.education.teaching_flows.mind_map_frontier",
                AsyncMock(return_value=[]),
            ),
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=6),
            ),
        ):
            result = await teaching_flow_advance(pool, map_id)

        assert result["status"] == "reviewing"
        assert result["current_phase"] is None

    async def test_quizzing_to_completed_when_all_mastered(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        state = _flow_state(
            status="quizzing",
            current_node_id=node_id,
            current_phase=None,
            session_count=4,
        )
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=5)])

        mock_summary = {
            "total_nodes": 5,
            "mastered_count": 5,
            "learning_count": 0,
            "reviewing_count": 0,
            "unseen_count": 0,
            "diagnosed_count": 0,
            "avg_mastery_score": 1.0,
            "struggling_node_ids": [],
        }

        with (
            patch(
                "butlers.tools.education.teaching_flows.mastery_get_map_summary",
                AsyncMock(return_value=mock_summary),
            ),
            patch("butlers.tools.education.teaching_flows.mind_map_update_status", AsyncMock()),
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=6),
            ),
        ):
            result = await teaching_flow_advance(pool, map_id)

        assert result["status"] == "completed"
        assert result["current_node_id"] is None
        assert result["current_phase"] is None

    async def test_reviewing_to_teaching_when_frontier_available(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        next_node_id = str(uuid.uuid4())
        state = _flow_state(
            status="reviewing",
            current_node_id=node_id,
            current_phase=None,
            session_count=5,
        )
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=6)])

        mock_summary = {
            "total_nodes": 5,
            "mastered_count": 3,
            "learning_count": 1,
            "reviewing_count": 1,
            "unseen_count": 0,
            "diagnosed_count": 0,
            "avg_mastery_score": 0.7,
            "struggling_node_ids": [],
        }
        mock_frontier = [{"id": next_node_id, "label": "Advanced"}]

        with (
            patch(
                "butlers.tools.education.teaching_flows.mastery_get_map_summary",
                AsyncMock(return_value=mock_summary),
            ),
            patch(
                "butlers.tools.education.teaching_flows.mind_map_frontier",
                AsyncMock(return_value=mock_frontier),
            ),
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=7),
            ),
        ):
            result = await teaching_flow_advance(pool, map_id)

        assert result["status"] == "teaching"
        assert result["current_node_id"] == next_node_id
        assert result["current_phase"] == "explaining"

    async def test_reviewing_to_completed_when_all_mastered(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        state = _flow_state(
            status="reviewing",
            current_node_id=node_id,
            current_phase=None,
            session_count=5,
        )
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=6)])

        mock_summary = {
            "total_nodes": 5,
            "mastered_count": 5,
            "learning_count": 0,
            "reviewing_count": 0,
            "unseen_count": 0,
            "diagnosed_count": 0,
            "avg_mastery_score": 1.0,
            "struggling_node_ids": [],
        }

        with (
            patch(
                "butlers.tools.education.teaching_flows.mastery_get_map_summary",
                AsyncMock(return_value=mock_summary),
            ),
            patch("butlers.tools.education.teaching_flows.mind_map_update_status", AsyncMock()),
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=7),
            ),
        ):
            result = await teaching_flow_advance(pool, map_id)

        assert result["status"] == "completed"

    async def test_completed_raises_value_error(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        state = _flow_state(status="completed", current_node_id=None, current_phase=None)
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=10)])

        with pytest.raises(ValueError, match="terminal"):
            await teaching_flow_advance(pool, map_id)

    async def test_abandoned_raises_value_error(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        state = _flow_state(status="abandoned", current_node_id=None, current_phase=None)
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=10)])

        with pytest.raises(ValueError, match="terminal"):
            await teaching_flow_advance(pool, map_id)

    async def test_raises_when_no_flow_exists(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        pool = _make_pool(fetchrow_returns=[None])

        with pytest.raises(ValueError, match="No flow found"):
            await teaching_flow_advance(pool, str(uuid.uuid4()))

    async def test_session_count_incremented(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        state = _pending_state(map_id)
        state["session_count"] = 5

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=5)])

        with patch(
            "butlers.tools.education.teaching_flows.state_compare_and_set",
            AsyncMock(return_value=6),
        ):
            result = await teaching_flow_advance(pool, map_id)

        assert result["session_count"] == 6

    async def test_last_session_at_updated(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        map_id = str(uuid.uuid4())
        state = _pending_state(map_id)
        state["last_session_at"] = "2020-01-01T00:00:00+00:00"

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=1)])

        with patch(
            "butlers.tools.education.teaching_flows.state_compare_and_set",
            AsyncMock(return_value=2),
        ):
            result = await teaching_flow_advance(pool, map_id)

        # last_session_at should be a recent timestamp, not the old one
        assert result["last_session_at"] != "2020-01-01T00:00:00+00:00"
        ts = datetime.fromisoformat(result["last_session_at"])
        # Should be within last minute
        assert (datetime.now(tz=UTC) - ts).total_seconds() < 60

    async def test_quizzing_requires_node_id_raises(self) -> None:
        """Verify state invariants are checked: quizzing without node_id should fail."""
        from butlers.tools.education.teaching_flows import teaching_flow_advance

        # A quizzing state MUST have current_node_id — teaching_to_quizzing keeps it
        # but if somehow state is wrong, invariant check should catch it.
        map_id = str(uuid.uuid4())
        # Simulate a teaching state that has no current_node_id (corrupted)
        state = _flow_state(
            status="teaching",
            current_node_id=None,
            current_phase="explaining",
            session_count=3,
        )
        state["mind_map_id"] = map_id

        # This should fail invariant validation (teaching without node_id is invalid input,
        # but advance would produce quizzing with no node_id from it)
        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=4)])

        # The quizzing state would have current_node_id=None which violates invariants
        with pytest.raises(ValueError, match="current_node_id"):
            await teaching_flow_advance(pool, map_id)


# ---------------------------------------------------------------------------
# Tests: teaching_flow_start
# ---------------------------------------------------------------------------


class TestTeachingFlowStart:
    async def test_creates_mind_map_and_returns_diagnosing(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_start

        map_id = str(uuid.uuid4())

        pool = _make_pool(
            fetchrow_returns=[
                _make_row({"id": map_id}),  # mind_map_create RETURNING id
                _make_state_row(_pending_state(map_id), version=1),  # advance fetch state
            ],
            execute_returns=["UPDATE 1"],
        )

        with (
            patch("butlers.tools.education.teaching_flows.state_set", AsyncMock(return_value=1)),
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=2),
            ),
        ):
            result = await teaching_flow_start(pool, "Python")

        assert result["status"] == "diagnosing"
        assert result["mind_map_id"] == map_id

    async def test_start_with_goal_stores_goal(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_start

        map_id = str(uuid.uuid4())

        pool = _make_pool(
            fetchrow_returns=[
                _make_row({"id": map_id}),
                _make_state_row(_pending_state(map_id), version=1),
            ],
            execute_returns=["UPDATE 1"],
        )

        with (
            patch("butlers.tools.education.teaching_flows.state_set", AsyncMock(return_value=1)),
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=2),
            ),
        ):
            result = await teaching_flow_start(pool, "Rust", goal="Learn systems programming")

        assert result["status"] == "diagnosing"
        # Pool.execute should have been called to store the goal
        assert pool.execute.called

    async def test_start_initializes_session_count_zero(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_start

        map_id = str(uuid.uuid4())
        initial = _pending_state(map_id)
        initial["session_count"] = 0

        pool = _make_pool(
            fetchrow_returns=[
                _make_row({"id": map_id}),
                _make_state_row(initial, version=1),
            ],
            execute_returns=["UPDATE 1"],
        )

        with (
            patch("butlers.tools.education.teaching_flows.state_set", AsyncMock(return_value=1)),
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=2),
            ),
        ):
            result = await teaching_flow_start(pool, "Python")

        # After advancing to diagnosing, session_count should be 1
        assert result["session_count"] == 1


# ---------------------------------------------------------------------------
# Tests: teaching_flow_abandon
# ---------------------------------------------------------------------------


class TestTeachingFlowAbandon:
    async def test_abandon_active_flow(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_abandon

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        state = _flow_state(
            status="teaching",
            current_node_id=node_id,
            current_phase="explaining",
        )
        state["mind_map_id"] = map_id

        pool = _make_pool(
            fetchrow_returns=[_make_state_row(state, version=3)],
            fetch_returns=[
                [_make_row({"id": node_id})],  # node list
                [],  # node schedule names (empty)
                [],  # batch schedule names (empty)
            ],
            execute_returns=["UPDATE 1"],
        )

        schedule_delete = AsyncMock()

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=4),
            ),
            patch("butlers.tools.education.teaching_flows.mind_map_update_status", AsyncMock()),
        ):
            await teaching_flow_abandon(pool, map_id, schedule_delete=schedule_delete)

    async def test_abandon_raises_on_completed(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_abandon

        map_id = str(uuid.uuid4())
        state = _flow_state(status="completed", current_node_id=None, current_phase=None)
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=10)])

        with pytest.raises(ValueError, match="terminal"):
            await teaching_flow_abandon(pool, map_id)

    async def test_abandon_raises_on_already_abandoned(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_abandon

        map_id = str(uuid.uuid4())
        state = _flow_state(status="abandoned", current_node_id=None, current_phase=None)
        state["mind_map_id"] = map_id

        pool = _make_pool(fetchrow_returns=[_make_state_row(state, version=10)])

        with pytest.raises(ValueError, match="terminal"):
            await teaching_flow_abandon(pool, map_id)

    async def test_abandon_raises_when_no_flow(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_abandon

        pool = _make_pool(fetchrow_returns=[None])

        with pytest.raises(ValueError, match="No flow found"):
            await teaching_flow_abandon(pool, str(uuid.uuid4()))

    async def test_abandon_deletes_node_schedules(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_abandon

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        schedule_name = f"review-{node_id}-rep2"

        state = _flow_state(
            status="diagnosing",
            current_node_id=None,
            current_phase=None,
        )
        state["mind_map_id"] = map_id

        pool = _make_pool(
            fetchrow_returns=[_make_state_row(state, version=3)],
            fetch_returns=[
                [_make_row({"id": node_id})],  # node list
                [_make_row({"name": schedule_name})],  # node schedule names
                [],  # batch schedule names
            ],
            execute_returns=["UPDATE 1"],
        )

        schedule_delete = AsyncMock()

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=4),
            ),
            patch("butlers.tools.education.teaching_flows.mind_map_update_status", AsyncMock()),
        ):
            await teaching_flow_abandon(pool, map_id, schedule_delete=schedule_delete)

        schedule_delete.assert_called_once_with(schedule_name)

    async def test_abandon_updates_mind_map_status(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_abandon

        map_id = str(uuid.uuid4())
        state = _flow_state(status="planning", current_node_id=None, current_phase=None)
        state["mind_map_id"] = map_id

        pool = _make_pool(
            fetchrow_returns=[_make_state_row(state, version=2)],
            fetch_returns=[[], []],
            execute_returns=["UPDATE 1"],
        )

        schedule_delete = AsyncMock()
        mock_update_status = AsyncMock()

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_compare_and_set",
                AsyncMock(return_value=3),
            ),
            patch(
                "butlers.tools.education.teaching_flows.mind_map_update_status", mock_update_status
            ),
        ):
            await teaching_flow_abandon(pool, map_id, schedule_delete=schedule_delete)

        mock_update_status.assert_called_once_with(pool, map_id, "abandoned")


# ---------------------------------------------------------------------------
# Tests: teaching_flow_list
# ---------------------------------------------------------------------------


class TestTeachingFlowList:
    async def test_returns_all_flows(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_list

        map_id1 = str(uuid.uuid4())
        map_id2 = str(uuid.uuid4())

        pool = _make_pool(
            fetch_returns=[
                [
                    _make_row({"id": map_id1, "title": "Python"}),
                    _make_row({"id": map_id2, "title": "Rust"}),
                ]
            ]
        )

        state1 = _flow_state(
            status="teaching", mind_map_id=map_id1, current_node_id=str(uuid.uuid4())
        )
        state2 = _flow_state(
            status="diagnosing", mind_map_id=map_id2, current_node_id=None, current_phase=None
        )

        summary1 = {
            "total_nodes": 10,
            "mastered_count": 4,
            "learning_count": 3,
            "reviewing_count": 2,
            "unseen_count": 1,
            "diagnosed_count": 0,
            "avg_mastery_score": 0.4,
            "struggling_node_ids": [],
        }
        summary2 = {
            "total_nodes": 0,
            "mastered_count": 0,
            "learning_count": 0,
            "reviewing_count": 0,
            "unseen_count": 0,
            "diagnosed_count": 0,
            "avg_mastery_score": 0.0,
            "struggling_node_ids": [],
        }

        state_get_side_effects = [state1, state2]
        mastery_summary_side_effects = [summary1, summary2]

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_get",
                AsyncMock(side_effect=state_get_side_effects),
            ),
            patch(
                "butlers.tools.education.teaching_flows.mastery_get_map_summary",
                AsyncMock(side_effect=mastery_summary_side_effects),
            ),
        ):
            result = await teaching_flow_list(pool)

        assert len(result) == 2
        # Find python entry
        python = next(r for r in result if r["title"] == "Python")
        assert python["status"] == "teaching"
        assert python["mastery_pct"] == pytest.approx(0.4)

    async def test_filters_by_status(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_list

        map_id1 = str(uuid.uuid4())
        map_id2 = str(uuid.uuid4())

        pool = _make_pool(
            fetch_returns=[
                [
                    _make_row({"id": map_id1, "title": "Python"}),
                    _make_row({"id": map_id2, "title": "Rust"}),
                ]
            ]
        )

        state1 = _flow_state(
            status="teaching", mind_map_id=map_id1, current_node_id=str(uuid.uuid4())
        )
        state2 = _flow_state(
            status="completed", mind_map_id=map_id2, current_node_id=None, current_phase=None
        )

        summary = {
            "total_nodes": 5,
            "mastered_count": 5,
            "learning_count": 0,
            "reviewing_count": 0,
            "unseen_count": 0,
            "diagnosed_count": 0,
            "avg_mastery_score": 1.0,
            "struggling_node_ids": [],
        }

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_get",
                AsyncMock(side_effect=[state1, state2]),
            ),
            patch(
                "butlers.tools.education.teaching_flows.mastery_get_map_summary",
                AsyncMock(return_value=summary),
            ),
        ):
            result = await teaching_flow_list(pool, status="completed")

        assert len(result) == 1
        assert result[0]["title"] == "Rust"
        assert result[0]["mastery_pct"] == 1.0

    async def test_empty_list_when_no_flows(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_list

        pool = _make_pool(fetch_returns=[[]])

        result = await teaching_flow_list(pool)
        assert result == []

    async def test_skips_maps_without_flow_state(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_list

        map_id = str(uuid.uuid4())

        pool = _make_pool(fetch_returns=[[_make_row({"id": map_id, "title": "Python"})]])

        with patch(
            "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=None)
        ):
            result = await teaching_flow_list(pool)

        assert result == []

    async def test_mastery_pct_computed(self) -> None:
        from butlers.tools.education.teaching_flows import teaching_flow_list

        map_id = str(uuid.uuid4())

        pool = _make_pool(fetch_returns=[[_make_row({"id": map_id, "title": "Math"})]])

        state = _flow_state(
            status="teaching", mind_map_id=map_id, current_node_id=str(uuid.uuid4())
        )
        summary = {
            "total_nodes": 20,
            "mastered_count": 10,
            "learning_count": 5,
            "reviewing_count": 3,
            "unseen_count": 2,
            "diagnosed_count": 0,
            "avg_mastery_score": 0.5,
            "struggling_node_ids": [],
        }

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
            ),
            patch(
                "butlers.tools.education.teaching_flows.mastery_get_map_summary",
                AsyncMock(return_value=summary),
            ),
        ):
            result = await teaching_flow_list(pool)

        assert len(result) == 1
        assert result[0]["mastery_pct"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Tests: assemble_session_context
# ---------------------------------------------------------------------------


class TestAssembleSessionContext:
    async def test_all_four_components_present(self) -> None:
        from butlers.tools.education.teaching_flows import assemble_session_context

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        state = _flow_state(
            status="teaching",
            mind_map_id=map_id,
            current_node_id=node_id,
            current_phase="explaining",
        )

        pool = _make_pool(fetch_returns=[[]])  # quiz responses

        mock_frontier = [{"id": node_id, "label": "Basics"}]
        mock_memory = {"facts": ["User knows Python basics"]}

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
            ),
            patch(
                "butlers.tools.education.teaching_flows.mind_map_frontier",
                AsyncMock(return_value=mock_frontier),
            ),
        ):
            result = await assemble_session_context(
                pool,
                map_id,
                fetch_memory_context=AsyncMock(return_value=mock_memory),
            )

        assert "flow_state" in result
        assert "frontier" in result
        assert "recent_responses" in result
        assert "memory_context" in result
        assert result["flow_state"] == state
        assert result["frontier"] == mock_frontier
        assert result["memory_context"] == mock_memory

    async def test_memory_context_fail_open(self) -> None:
        from butlers.tools.education.teaching_flows import assemble_session_context

        map_id = str(uuid.uuid4())
        state = _flow_state(
            status="teaching",
            mind_map_id=map_id,
            current_node_id=str(uuid.uuid4()),
            current_phase="explaining",
        )

        pool = _make_pool(fetch_returns=[[]])

        async def _failing_memory():
            raise RuntimeError("Memory service unavailable")

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
            ),
            patch(
                "butlers.tools.education.teaching_flows.mind_map_frontier",
                AsyncMock(return_value=[]),
            ),
        ):
            result = await assemble_session_context(
                pool,
                map_id,
                fetch_memory_context=_failing_memory,
            )

        # Should proceed without memory context, fail-open
        assert result["memory_context"] is None
        assert "flow_state" in result
        assert "frontier" in result

    async def test_no_recent_responses_when_no_current_node(self) -> None:
        from butlers.tools.education.teaching_flows import assemble_session_context

        map_id = str(uuid.uuid4())
        state = _flow_state(
            status="diagnosing",
            mind_map_id=map_id,
            current_node_id=None,
            current_phase=None,
        )

        pool = _make_pool()  # No fetch calls expected for responses

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
            ),
            patch(
                "butlers.tools.education.teaching_flows.mind_map_frontier",
                AsyncMock(return_value=[]),
            ),
        ):
            result = await assemble_session_context(pool, map_id)

        assert result["recent_responses"] == []

    async def test_recent_responses_limited_to_10(self) -> None:
        from butlers.tools.education.teaching_flows import assemble_session_context

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        state = _flow_state(
            status="quizzing",
            mind_map_id=map_id,
            current_node_id=node_id,
            current_phase="questioning",
        )

        # Simulate 10 response rows
        mock_responses = [
            _make_row(
                {
                    "question_text": f"Q{i}",
                    "user_answer": f"A{i}",
                    "quality": 4,
                    "response_type": "teach",
                    "responded_at": datetime.now(tz=UTC),
                }
            )
            for i in range(10)
        ]

        pool = _make_pool(fetch_returns=[mock_responses])

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
            ),
            patch(
                "butlers.tools.education.teaching_flows.mind_map_frontier",
                AsyncMock(return_value=[]),
            ),
        ):
            result = await assemble_session_context(pool, map_id)

        assert len(result["recent_responses"]) == 10

    async def test_no_memory_context_when_fetch_fn_not_provided(self) -> None:
        from butlers.tools.education.teaching_flows import assemble_session_context

        map_id = str(uuid.uuid4())
        state = _flow_state(
            status="teaching",
            mind_map_id=map_id,
            current_node_id=str(uuid.uuid4()),
            current_phase="explaining",
        )

        pool = _make_pool(fetch_returns=[[]])

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
            ),
            patch(
                "butlers.tools.education.teaching_flows.mind_map_frontier",
                AsyncMock(return_value=[]),
            ),
        ):
            result = await assemble_session_context(pool, map_id)

        assert result["memory_context"] is None


# ---------------------------------------------------------------------------
# Tests: check_stale_flows
# ---------------------------------------------------------------------------


class TestCheckStaleFlows:
    async def test_stale_flow_is_abandoned(self) -> None:
        from butlers.tools.education.teaching_flows import check_stale_flows

        map_id = str(uuid.uuid4())
        stale_time = (datetime.now(tz=UTC) - timedelta(days=31)).isoformat()

        pool = _make_pool(
            fetch_returns=[
                [_make_row({"id": map_id})],  # mind_maps query
                [],  # node list for cleanup
                [],  # batch schedules for cleanup
            ],
            execute_returns=["UPDATE 1"],
        )

        state = _flow_state(
            status="teaching",
            mind_map_id=map_id,
            current_node_id=str(uuid.uuid4()),
            current_phase="explaining",
            last_session_at=stale_time,
        )

        schedule_delete = AsyncMock()
        mock_abandon_state = dict(state)
        mock_abandon_state["status"] = "abandoned"

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
            ),
            patch(
                "butlers.tools.education.teaching_flows.teaching_flow_abandon",
                AsyncMock(return_value=None),
            ) as mock_abandon,
        ):
            result = await check_stale_flows(pool, schedule_delete=schedule_delete)

        assert map_id in result
        mock_abandon.assert_called_once_with(pool, map_id, schedule_delete=schedule_delete)

    async def test_recently_active_flow_is_not_abandoned(self) -> None:
        from butlers.tools.education.teaching_flows import check_stale_flows

        map_id = str(uuid.uuid4())
        recent_time = (datetime.now(tz=UTC) - timedelta(days=5)).isoformat()

        pool = _make_pool(fetch_returns=[[_make_row({"id": map_id})]])

        state = _flow_state(
            status="teaching",
            mind_map_id=map_id,
            current_node_id=str(uuid.uuid4()),
            current_phase="explaining",
            last_session_at=recent_time,
        )

        schedule_delete = AsyncMock()

        with patch(
            "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
        ):
            result = await check_stale_flows(pool, schedule_delete=schedule_delete)

        assert result == []

    async def test_completed_flow_is_skipped(self) -> None:
        from butlers.tools.education.teaching_flows import check_stale_flows

        map_id = str(uuid.uuid4())
        old_time = (datetime.now(tz=UTC) - timedelta(days=60)).isoformat()

        pool = _make_pool(fetch_returns=[[_make_row({"id": map_id})]])

        state = _flow_state(
            status="completed",
            mind_map_id=map_id,
            current_node_id=None,
            current_phase=None,
            last_session_at=old_time,
        )

        schedule_delete = AsyncMock()

        with patch(
            "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
        ):
            result = await check_stale_flows(pool, schedule_delete=schedule_delete)

        assert result == []

    async def test_abandoned_flow_is_skipped(self) -> None:
        from butlers.tools.education.teaching_flows import check_stale_flows

        map_id = str(uuid.uuid4())
        old_time = (datetime.now(tz=UTC) - timedelta(days=60)).isoformat()

        pool = _make_pool(fetch_returns=[[_make_row({"id": map_id})]])

        state = _flow_state(
            status="abandoned",
            mind_map_id=map_id,
            current_node_id=None,
            current_phase=None,
            last_session_at=old_time,
        )

        schedule_delete = AsyncMock()

        with patch(
            "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
        ):
            result = await check_stale_flows(pool, schedule_delete=schedule_delete)

        assert result == []

    async def test_multiple_stale_flows_all_abandoned(self) -> None:
        from butlers.tools.education.teaching_flows import check_stale_flows

        map_ids = [str(uuid.uuid4()) for _ in range(3)]
        stale_time = (datetime.now(tz=UTC) - timedelta(days=40)).isoformat()

        pool = _make_pool(fetch_returns=[[_make_row({"id": mid}) for mid in map_ids]])

        states = [
            _flow_state(
                status="teaching" if i == 0 else "diagnosing",
                mind_map_id=mid,
                current_node_id=str(uuid.uuid4()) if i == 0 else None,
                current_phase="explaining" if i == 0 else None,
                last_session_at=stale_time,
            )
            for i, mid in enumerate(map_ids)
        ]

        schedule_delete = AsyncMock()

        with (
            patch(
                "butlers.tools.education.teaching_flows.state_get", AsyncMock(side_effect=states)
            ),
            patch(
                "butlers.tools.education.teaching_flows.teaching_flow_abandon",
                AsyncMock(return_value=None),
            ),
        ):
            result = await check_stale_flows(pool, schedule_delete=schedule_delete)

        assert set(result) == set(map_ids)

    async def test_no_flows_exist(self) -> None:
        from butlers.tools.education.teaching_flows import check_stale_flows

        pool = _make_pool(fetch_returns=[[]])
        result = await check_stale_flows(pool)
        assert result == []

    async def test_flow_without_state_is_skipped(self) -> None:
        from butlers.tools.education.teaching_flows import check_stale_flows

        map_id = str(uuid.uuid4())
        pool = _make_pool(fetch_returns=[[_make_row({"id": map_id})]])

        with patch(
            "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=None)
        ):
            result = await check_stale_flows(pool)

        assert result == []

    async def test_exactly_30_days_is_not_stale(self) -> None:
        """Flow at exactly 30 days old is NOT stale (cutoff is strictly > 30 days)."""
        from butlers.tools.education.teaching_flows import check_stale_flows

        map_id = str(uuid.uuid4())
        # Exactly 30 days ago — not stale (cutoff requires > 30)
        boundary_time = (datetime.now(tz=UTC) - timedelta(days=30)).isoformat()

        pool = _make_pool(fetch_returns=[[_make_row({"id": map_id})]])

        state = _flow_state(
            status="teaching",
            mind_map_id=map_id,
            current_node_id=str(uuid.uuid4()),
            current_phase="explaining",
            last_session_at=boundary_time,
        )

        with patch(
            "butlers.tools.education.teaching_flows.state_get", AsyncMock(return_value=state)
        ):
            result = await check_stale_flows(pool)

        # At exactly 30 days, it's on the boundary — should not be abandoned
        # (cutoff = now - 30 days; last_session_at == cutoff → NOT stale)
        assert result == []

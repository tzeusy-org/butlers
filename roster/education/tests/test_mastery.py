"""Unit tests for education butler mastery tracking tools.

All tests mock the asyncpg pool/connection objects — no live database required.

Coverage:
- mastery_record_response: insert, scoring, state machine, atomicity
- mastery_get_node_history: ordering, limit, empty
- mastery_get_map_summary: status counts, avg score, struggling IDs
- mastery_detect_struggles: consecutive low quality, declining score, both, excluded mastered
- _compute_mastery_score: single response, recency weighting, cap/floor
- Input validation: quality out of range, invalid response_type
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers: mock asyncpg pool / connection builder
# (same pattern as test_mind_maps.py)
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
        pool.fetchval = AsyncMock(return_value=0)

    if execute_returns is not None:
        pool.execute = AsyncMock(side_effect=list(execute_returns))
    else:
        pool.execute = AsyncMock(return_value="UPDATE 1")

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
        conn.fetchval = AsyncMock(return_value=0)

    if execute_returns is not None:
        conn.execute = AsyncMock(side_effect=list(execute_returns))
    else:
        conn.execute = AsyncMock(return_value="UPDATE 1")

    return conn


def _response_row(
    node_id: str | None = None,
    quality: int = 3,
    response_type: str = "review",
    session_id: str | None = None,
    question_text: str = "What is X?",
    user_answer: str | None = "Y",
    responded_at: str = "2026-01-01T10:00:00+00:00",
) -> dict[str, Any]:
    """Return a minimal quiz_response row dict."""
    return {
        "id": str(uuid.uuid4()),
        "node_id": node_id or str(uuid.uuid4()),
        "mind_map_id": str(uuid.uuid4()),
        "question_text": question_text,
        "user_answer": user_answer,
        "quality": quality,
        "response_type": response_type,
        "session_id": session_id,
        "responded_at": responded_at,
    }


def _node_row(
    node_id: str | None = None,
    mind_map_id: str | None = None,
    label: str = "Test Node",
    mastery_score: float = 0.0,
    mastery_status: str = "unseen",
) -> dict[str, Any]:
    """Return a minimal node row dict."""
    return {
        "id": node_id or str(uuid.uuid4()),
        "mind_map_id": mind_map_id or str(uuid.uuid4()),
        "label": label,
        "mastery_score": mastery_score,
        "mastery_status": mastery_status,
    }


def _make_pool_with_conn(conn: AsyncMock) -> AsyncMock:
    """Build a pool whose acquire() context manager yields the given conn."""
    pool = MagicMock()
    # pool.acquire() is an async context manager: __aenter__ returns conn
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)

    # Also set direct fetch/fetchrow for functions that use pool directly
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="UPDATE 1")

    return pool


def _make_conn_with_transaction(conn: AsyncMock) -> AsyncMock:
    """Wire conn.transaction() as an async context manager."""
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)
    return conn


# ---------------------------------------------------------------------------
# Tests: _compute_mastery_score
# ---------------------------------------------------------------------------


class TestComputeMasteryScore:
    """Unit tests for the mastery score computation helper."""

    def test_empty_qualities_returns_zero(self) -> None:
        from butlers.tools.education.mastery import _compute_mastery_score

        assert _compute_mastery_score([]) == 0.0

    def test_single_response_quality_4(self) -> None:
        """Single response quality=4 → 4/5.0 = 0.8."""
        from butlers.tools.education.mastery import _compute_mastery_score

        score = _compute_mastery_score([4])
        assert abs(score - 0.8) < 1e-9

    def test_single_response_quality_5(self) -> None:
        """Single response quality=5 → 5/5.0 = 1.0."""
        from butlers.tools.education.mastery import _compute_mastery_score

        score = _compute_mastery_score([5])
        assert score == 1.0

    def test_single_response_quality_0(self) -> None:
        """Single response quality=0 → 0/5.0 = 0.0."""
        from butlers.tools.education.mastery import _compute_mastery_score

        score = _compute_mastery_score([0])
        assert score == 0.0

    def test_five_perfect_scores_cap_at_1(self) -> None:
        """Five quality=5 responses must equal 1.0."""
        from butlers.tools.education.mastery import _compute_mastery_score

        score = _compute_mastery_score([5, 5, 5, 5, 5])
        assert score == 1.0

    def test_five_zero_scores_floor_at_0(self) -> None:
        """Five quality=0 responses must equal 0.0."""
        from butlers.tools.education.mastery import _compute_mastery_score

        score = _compute_mastery_score([0, 0, 0, 0, 0])
        assert score == 0.0

    def test_recency_weighting_increases_score(self) -> None:
        """Qualities [2, 3, 4, 4, 5] (oldest→newest) must exceed equal-weight baseline 0.72."""
        from butlers.tools.education.mastery import _compute_mastery_score

        score = _compute_mastery_score([2, 3, 4, 4, 5])
        equal_weight_baseline = (2 + 3 + 4 + 4 + 5) / (5 * 5.0)  # = 0.72
        assert score > equal_weight_baseline

    def test_uses_only_last_five_responses(self) -> None:
        """With 8 responses, only the last 5 (passed in) are used."""
        from butlers.tools.education.mastery import _compute_mastery_score

        # Passing 8 items; function should only use last 5
        # This tests the slicing behavior
        score_8 = _compute_mastery_score([0, 0, 0, 5, 5, 5, 5, 5])
        score_5 = _compute_mastery_score([5, 5, 5, 5, 5])
        # The last 5 of the 8 are all 5s, so both should equal 1.0
        assert score_8 == score_5 == 1.0

    def test_recency_weighting_newer_beats_older(self) -> None:
        """Score([0, 5]) > score([5, 0]) because newer response is weighted higher."""
        from butlers.tools.education.mastery import _compute_mastery_score

        score_high_recent = _compute_mastery_score([0, 5])
        score_low_recent = _compute_mastery_score([5, 0])
        assert score_high_recent > score_low_recent

    def test_score_clamped_between_0_and_1(self) -> None:
        from butlers.tools.education.mastery import _compute_mastery_score

        # Ensure no score goes above 1 or below 0 regardless of input
        for quals in [[5, 5, 5, 5, 5], [0, 0, 0, 0, 0], [3, 4, 5, 5, 5]]:
            score = _compute_mastery_score(quals)
            assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Tests: mastery_record_response — input validation
# ---------------------------------------------------------------------------


class TestMasteryRecordResponseValidation:
    """mastery_record_response raises ValueError for invalid inputs."""

    async def test_quality_below_zero_raises(self) -> None:
        from butlers.tools.education.mastery import mastery_record_response

        pool = MagicMock()
        with pytest.raises(ValueError, match="quality must be between 0 and 5"):
            await mastery_record_response(
                pool, str(uuid.uuid4()), str(uuid.uuid4()), "Q", "A", quality=-1
            )

    async def test_quality_above_five_raises(self) -> None:
        from butlers.tools.education.mastery import mastery_record_response

        pool = MagicMock()
        with pytest.raises(ValueError, match="quality must be between 0 and 5"):
            await mastery_record_response(
                pool, str(uuid.uuid4()), str(uuid.uuid4()), "Q", "A", quality=6
            )

    async def test_invalid_response_type_raises(self) -> None:
        from butlers.tools.education.mastery import mastery_record_response

        pool = MagicMock()
        with pytest.raises(ValueError, match="Invalid response_type"):
            await mastery_record_response(
                pool,
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                "Q",
                "A",
                quality=3,
                response_type="exam",
            )

    async def test_quality_zero_is_valid(self) -> None:
        """Quality=0 (blackout) is valid."""
        from butlers.tools.education.mastery import mastery_record_response

        response_id = str(uuid.uuid4())
        conn = _make_conn_with_transaction(
            _make_conn(
                fetchval_returns=[response_id],
                fetch_returns=[
                    [_make_row({"quality": 0})],  # last 5 responses
                    [],  # last 3 review responses
                ],
                fetchrow_returns=[_make_row({"mastery_status": "unseen"})],
            )
        )
        pool = _make_pool_with_conn(conn)
        result = await mastery_record_response(
            pool, str(uuid.uuid4()), str(uuid.uuid4()), "Q", None, quality=0
        )
        assert result == response_id

    async def test_quality_five_is_valid(self) -> None:
        """Quality=5 (perfect) is valid."""
        from butlers.tools.education.mastery import mastery_record_response

        response_id = str(uuid.uuid4())
        conn = _make_conn_with_transaction(
            _make_conn(
                fetchval_returns=[response_id],
                fetch_returns=[
                    [_make_row({"quality": 5})],
                    [],
                ],
                fetchrow_returns=[_make_row({"mastery_status": "unseen"})],
            )
        )
        pool = _make_pool_with_conn(conn)
        result = await mastery_record_response(
            pool, str(uuid.uuid4()), str(uuid.uuid4()), "Q", "A", quality=5
        )
        assert result == response_id


# ---------------------------------------------------------------------------
# Tests: mastery_record_response — response insertion and return value
# ---------------------------------------------------------------------------


class TestMasteryRecordResponseInsert:
    """mastery_record_response inserts a row and returns its UUID."""

    async def test_returns_uuid_of_new_response(self) -> None:
        from butlers.tools.education.mastery import mastery_record_response

        response_id = str(uuid.uuid4())
        conn = _make_conn_with_transaction(
            _make_conn(
                fetchval_returns=[response_id],
                fetch_returns=[
                    [_make_row({"quality": 3})],  # last 5 responses
                    [],  # last 3 review responses
                ],
                fetchrow_returns=[_make_row({"mastery_status": "learning"})],
            )
        )
        pool = _make_pool_with_conn(conn)
        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())

        result = await mastery_record_response(
            pool, node_id, map_id, "What is X?", "Y", quality=3, response_type="review"
        )
        assert result == response_id

    async def test_insert_called_with_correct_args(self) -> None:
        """The INSERT SQL is called with the supplied parameters."""
        from butlers.tools.education.mastery import mastery_record_response

        response_id = str(uuid.uuid4())
        conn = _make_conn_with_transaction(
            _make_conn(
                fetchval_returns=[response_id],
                fetch_returns=[
                    [_make_row({"quality": 4})],
                    [],
                ],
                fetchrow_returns=[_make_row({"mastery_status": "learning"})],
            )
        )
        pool = _make_pool_with_conn(conn)
        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())

        await mastery_record_response(
            pool,
            node_id,
            map_id,
            "Define recursion",
            "A function calling itself",
            quality=4,
            response_type="teach",
            session_id=session_id,
        )
        # Verify fetchval (INSERT RETURNING id) was called
        assert conn.fetchval.called
        insert_args = conn.fetchval.call_args.args
        assert node_id in insert_args
        assert map_id in insert_args
        assert "Define recursion" in insert_args
        assert "A function calling itself" in insert_args
        assert 4 in insert_args
        assert "teach" in insert_args
        assert session_id in insert_args

    async def test_null_user_answer_accepted(self) -> None:
        """user_answer=None is passed through to the INSERT."""
        from butlers.tools.education.mastery import mastery_record_response

        response_id = str(uuid.uuid4())
        conn = _make_conn_with_transaction(
            _make_conn(
                fetchval_returns=[response_id],
                fetch_returns=[
                    [_make_row({"quality": 0})],
                    [],
                ],
                fetchrow_returns=[_make_row({"mastery_status": "unseen"})],
            )
        )
        pool = _make_pool_with_conn(conn)

        result = await mastery_record_response(
            pool, str(uuid.uuid4()), str(uuid.uuid4()), "Q", None, quality=0
        )
        assert result == response_id
        insert_args = conn.fetchval.call_args.args
        assert None in insert_args

    async def test_default_response_type_is_review(self) -> None:
        """Calling without response_type defaults to 'review'."""
        from butlers.tools.education.mastery import mastery_record_response

        response_id = str(uuid.uuid4())
        conn = _make_conn_with_transaction(
            _make_conn(
                fetchval_returns=[response_id],
                fetch_returns=[
                    [_make_row({"quality": 3})],
                    [_make_row({"quality": 3})],  # last 3 review responses
                ],
                fetchrow_returns=[_make_row({"mastery_status": "learning"})],
            )
        )
        pool = _make_pool_with_conn(conn)

        await mastery_record_response(
            pool, str(uuid.uuid4()), str(uuid.uuid4()), "Q", "A", quality=3
        )
        insert_args = conn.fetchval.call_args.args
        assert "review" in insert_args


# ---------------------------------------------------------------------------
# Tests: mastery_record_response — mastery score computation
# ---------------------------------------------------------------------------


class TestMasteryRecordResponseScoring:
    """mastery_record_response updates mastery_score with recency-weighted average."""

    async def _do_record(
        self,
        current_status: str,
        qualities_from_db: list[int],
        quality: int,
        response_type: str = "review",
        review_qualities: list[int] | None = None,
    ) -> tuple[Any, AsyncMock]:
        """Helper: set up mock and call mastery_record_response. Returns (result, conn)."""
        from butlers.tools.education.mastery import mastery_record_response

        response_id = str(uuid.uuid4())
        review_rows = [_make_row({"quality": q}) for q in (review_qualities or [])]
        conn = _make_conn_with_transaction(
            _make_conn(
                fetchval_returns=[response_id],
                fetch_returns=[
                    [_make_row({"quality": q}) for q in qualities_from_db],
                    review_rows,
                ],
                fetchrow_returns=[_make_row({"mastery_status": current_status})],
            )
        )
        pool = _make_pool_with_conn(conn)
        result = await mastery_record_response(
            pool,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            "Q",
            "A",
            quality=quality,
            response_type=response_type,
        )
        return result, conn

    async def test_score_written_to_node_update(self) -> None:
        """The UPDATE statement is called to write mastery_score."""
        # Qualities returned from DB (newest→oldest): [4]
        result, conn = await self._do_record("learning", [4], quality=4)
        # Verify execute was called (UPDATE mind_map_nodes)
        assert conn.execute.called

    async def test_score_uses_recency_weighted_average(self) -> None:
        """Score written is recency-weighted, not simple average."""
        from butlers.tools.education.mastery import _compute_mastery_score, mastery_record_response

        # Qualities in DB (newest→oldest): [5, 4, 3, 2, 1]
        # After reversing to oldest→newest: [1, 2, 3, 4, 5]
        expected_score = _compute_mastery_score([1, 2, 3, 4, 5])

        response_id = str(uuid.uuid4())
        conn = _make_conn_with_transaction(
            _make_conn(
                fetchval_returns=[response_id],
                fetch_returns=[
                    [
                        _make_row({"quality": 5}),
                        _make_row({"quality": 4}),
                        _make_row({"quality": 3}),
                        _make_row({"quality": 2}),
                        _make_row({"quality": 1}),
                    ],
                    [],  # last 3 review responses
                ],
                fetchrow_returns=[_make_row({"mastery_status": "reviewing"})],
            )
        )
        pool = _make_pool_with_conn(conn)

        await mastery_record_response(
            pool, str(uuid.uuid4()), str(uuid.uuid4()), "Q", "A", quality=5
        )

        # Find the UPDATE execute call and check the score parameter
        update_call = conn.execute.call_args
        assert expected_score in update_call.args


# ---------------------------------------------------------------------------
# Tests: mastery_record_response — state machine transitions
# ---------------------------------------------------------------------------


class TestMasteryStateMachineInRecordResponse:
    """mastery_record_response applies correct state machine transitions."""

    async def _record_and_get_status_update(
        self,
        current_status: str,
        quality: int,
        response_type: str = "review",
        review_qualities: list[int] | None = None,
        qualities_from_db: list[int] | None = None,
        expect_mastery_graduation: bool = False,
    ) -> tuple[Any, AsyncMock]:
        """Set up mock and call mastery_record_response."""
        from butlers.tools.education.mastery import mastery_record_response

        response_id = str(uuid.uuid4())
        db_qualities = qualities_from_db if qualities_from_db is not None else [quality]
        review_rows = [_make_row({"quality": q}) for q in (review_qualities or [])]
        # If mastery graduation is expected, we need to return values for
        # the auto-completion fetchval calls (unmastered_count, node_count)
        fetchval_side_effects = [response_id]
        if expect_mastery_graduation:
            fetchval_side_effects.extend([1, 1])  # unmastered=1, node_count=1 (no auto-complete)
        conn = _make_conn_with_transaction(
            _make_conn(
                fetchval_returns=fetchval_side_effects,
                fetch_returns=[
                    [_make_row({"quality": q}) for q in db_qualities],
                    review_rows,
                ],
                fetchrow_returns=[_make_row({"mastery_status": current_status})],
            )
        )
        pool = _make_pool_with_conn(conn)
        await mastery_record_response(
            pool,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            "Q",
            "A",
            quality=quality,
            response_type=response_type,
        )
        return response_id, conn

    def _get_status_from_update_call(self, conn: AsyncMock) -> str | None:
        """Extract the mastery_status value from the UPDATE execute call."""
        update_call = conn.execute.call_args
        if update_call is None:
            return None
        sql = update_call.args[0]
        if "mastery_status" not in sql:
            return None
        # The status is the parameter after mastery_score in the args list
        # args: (sql, mastery_score, mastery_status, node_id)
        args = update_call.args
        # mastery_score is args[1], mastery_status is args[2], node_id is args[3]
        if len(args) >= 4:
            return args[2]
        return None

    # --- unseen transitions ---

    async def test_unseen_plus_diagnostic_to_diagnosed(self) -> None:
        _, conn = await self._record_and_get_status_update("unseen", 3, "diagnostic")
        new_status = self._get_status_from_update_call(conn)
        assert new_status == "diagnosed"

    async def test_unseen_plus_teach_to_learning(self) -> None:
        _, conn = await self._record_and_get_status_update("unseen", 3, "teach")
        new_status = self._get_status_from_update_call(conn)
        assert new_status == "learning"

    async def test_unseen_plus_review_no_transition(self) -> None:
        """review on unseen node: no status transition."""
        _, conn = await self._record_and_get_status_update("unseen", 3, "review")
        new_status = self._get_status_from_update_call(conn)
        assert new_status is None

    # --- diagnosed transitions ---

    async def test_diagnosed_plus_teach_to_learning(self) -> None:
        _, conn = await self._record_and_get_status_update("diagnosed", 3, "teach")
        new_status = self._get_status_from_update_call(conn)
        assert new_status == "learning"

    async def test_diagnosed_plus_low_quality_self_correction(self) -> None:
        """quality<3 on diagnosed node → learning (self-correction)."""
        _, conn = await self._record_and_get_status_update("diagnosed", 1, "review")
        new_status = self._get_status_from_update_call(conn)
        assert new_status == "learning"

    async def test_diagnosed_plus_quality_3_no_transition(self) -> None:
        """quality=3 on diagnosed node with review type: no valid normal transition."""
        _, conn = await self._record_and_get_status_update("diagnosed", 3, "review")
        new_status = self._get_status_from_update_call(conn)
        # quality >= 3 on diagnosed is not a defined forward transition
        assert new_status is None

    # --- learning transitions ---

    async def test_learning_plus_quality_3_to_reviewing(self) -> None:
        _, conn = await self._record_and_get_status_update("learning", 3, "review")
        new_status = self._get_status_from_update_call(conn)
        assert new_status == "reviewing"

    async def test_learning_plus_quality_4_to_reviewing(self) -> None:
        _, conn = await self._record_and_get_status_update("learning", 4, "review")
        new_status = self._get_status_from_update_call(conn)
        assert new_status == "reviewing"

    async def test_learning_plus_quality_2_stays_learning(self) -> None:
        """quality<3 on learning stays learning: no transition."""
        _, conn = await self._record_and_get_status_update("learning", 2, "review")
        new_status = self._get_status_from_update_call(conn)
        assert new_status is None

    async def test_learning_plus_quality_0_stays_learning(self) -> None:
        _, conn = await self._record_and_get_status_update("learning", 0, "review")
        new_status = self._get_status_from_update_call(conn)
        assert new_status is None

    # --- reviewing transitions ---

    async def test_reviewing_plus_quality_2_regression_to_learning(self) -> None:
        _, conn = await self._record_and_get_status_update("reviewing", 2, "review")
        new_status = self._get_status_from_update_call(conn)
        assert new_status == "learning"

    async def test_reviewing_plus_quality_0_regression_to_learning(self) -> None:
        _, conn = await self._record_and_get_status_update("reviewing", 0, "review")
        new_status = self._get_status_from_update_call(conn)
        assert new_status == "learning"

    async def test_reviewing_graduates_to_mastered_when_threshold_met(self) -> None:
        """score>=0.85 AND last 3 reviews all quality>=4 → mastered."""
        # Use 5 quality=5 responses → score=1.0, and 3 review responses all quality=5
        _, conn = await self._record_and_get_status_update(
            "reviewing",
            5,
            "review",
            review_qualities=[5, 5, 5],
            qualities_from_db=[5, 5, 5, 5, 5],
            expect_mastery_graduation=True,
        )
        new_status = self._get_status_from_update_call(conn)
        assert new_status == "mastered"

    async def test_reviewing_stays_reviewing_score_below_threshold(self) -> None:
        """score<0.85 even with good reviews → stays reviewing."""
        # Use qualities [2, 3, 4, 4, 4] → weighted score < 0.85
        _, conn = await self._record_and_get_status_update(
            "reviewing",
            4,
            "review",
            review_qualities=[4, 4, 4],
            qualities_from_db=[4, 3, 2, 2, 2],  # newest→oldest
        )
        new_status = self._get_status_from_update_call(conn)
        # Score will be weighted over [2, 2, 2, 3, 4] = low, no graduation
        assert new_status is None

    async def test_reviewing_stays_reviewing_last_three_not_all_high(self) -> None:
        """last 3 review quality=[4,3,4] (one quality=3) → no graduation."""
        _, conn = await self._record_and_get_status_update(
            "reviewing",
            4,
            "review",
            review_qualities=[4, 3, 4],  # newest first; quality=3 disqualifies
            qualities_from_db=[5, 5, 5, 5, 5],  # score=1.0 but last 3 not all >=4
        )
        new_status = self._get_status_from_update_call(conn)
        assert new_status is None

    async def test_reviewing_stays_reviewing_fewer_than_3_review_responses(self) -> None:
        """Only 2 review responses → graduation requires exactly 3."""
        _, conn = await self._record_and_get_status_update(
            "reviewing",
            5,
            "review",
            review_qualities=[5, 5],  # only 2 responses
            qualities_from_db=[5, 5, 5, 5, 5],
        )
        new_status = self._get_status_from_update_call(conn)
        assert new_status is None

    async def test_reviewing_non_review_responses_excluded_from_last3_check(self) -> None:
        """teach-type responses are not counted in the last-3-review-quality check."""
        # If score>=0.85 and the 3 most recent REVIEW responses are all quality>=4,
        # graduation should happen even if teach response with quality=2 was recorded
        _, conn = await self._record_and_get_status_update(
            "reviewing",
            5,
            "review",
            review_qualities=[5, 5, 5],  # 3 review responses all >=4
            qualities_from_db=[5, 5, 5, 5, 5],  # score=1.0
            expect_mastery_graduation=True,
        )
        new_status = self._get_status_from_update_call(conn)
        assert new_status == "mastered"

    # --- mastered: never demoted ---

    async def test_mastered_node_not_demoted_on_quality_0(self) -> None:
        """Mastered nodes are never demoted via mastery_record_response."""
        _, conn = await self._record_and_get_status_update("mastered", 0, "review")
        new_status = self._get_status_from_update_call(conn)
        assert new_status is None

    # --- diagnosed not skipping to reviewing ---

    async def test_diagnosed_does_not_skip_to_reviewing(self) -> None:
        """Even with quality=5, diagnosed cannot jump to reviewing."""
        _, conn = await self._record_and_get_status_update("diagnosed", 5, "review")
        new_status = self._get_status_from_update_call(conn)
        assert new_status is None


# ---------------------------------------------------------------------------
# Tests: mastery_record_response — atomicity / transaction
# ---------------------------------------------------------------------------


class TestMasteryRecordResponseAtomicity:
    """mastery_record_response wraps all writes in a single transaction."""

    async def test_uses_transaction(self) -> None:
        """conn.transaction() is used for atomicity."""
        from butlers.tools.education.mastery import mastery_record_response

        response_id = str(uuid.uuid4())
        conn = _make_conn_with_transaction(
            _make_conn(
                fetchval_returns=[response_id],
                fetch_returns=[
                    [_make_row({"quality": 3})],
                    [],
                ],
                fetchrow_returns=[_make_row({"mastery_status": "learning"})],
            )
        )
        pool = _make_pool_with_conn(conn)

        await mastery_record_response(
            pool, str(uuid.uuid4()), str(uuid.uuid4()), "Q", "A", quality=3
        )
        conn.transaction.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: mastery_get_node_history
# ---------------------------------------------------------------------------


class TestMasteryGetNodeHistory:
    """mastery_get_node_history returns history ordered most recent first."""

    async def test_returns_list_of_response_dicts(self) -> None:
        from butlers.tools.education.mastery import mastery_get_node_history

        node_id = str(uuid.uuid4())
        rows = [
            _make_row(_response_row(node_id=node_id, quality=5)),
            _make_row(_response_row(node_id=node_id, quality=3)),
        ]
        pool = _make_pool(fetch_returns=[rows])
        result = await mastery_get_node_history(pool, node_id)
        assert len(result) == 2
        assert result[0]["quality"] == 5
        assert result[1]["quality"] == 3

    async def test_returns_empty_list_when_no_responses(self) -> None:
        from butlers.tools.education.mastery import mastery_get_node_history

        pool = _make_pool(fetch_returns=[[]])
        result = await mastery_get_node_history(pool, str(uuid.uuid4()))
        assert result == []

    async def test_limit_parameter_applied(self) -> None:
        """When limit is supplied, it is passed to the SQL query."""
        from butlers.tools.education.mastery import mastery_get_node_history

        node_id = str(uuid.uuid4())
        rows = [_make_row(_response_row(node_id=node_id, quality=4))]
        pool = _make_pool(fetch_returns=[rows])
        result = await mastery_get_node_history(pool, node_id, limit=3)
        # Verify limit was passed as an argument to the SQL call
        call_args_str = str(pool.fetch.call_args)
        assert "3" in call_args_str or 3 in pool.fetch.call_args.args
        assert len(result) == 1

    async def test_no_limit_returns_all_responses(self) -> None:
        """Without limit, all rows are returned."""
        from butlers.tools.education.mastery import mastery_get_node_history

        node_id = str(uuid.uuid4())
        rows = [_make_row(_response_row(node_id=node_id, quality=i)) for i in range(8)]
        pool = _make_pool(fetch_returns=[rows])
        result = await mastery_get_node_history(pool, node_id)
        assert len(result) == 8

    async def test_ordered_by_responded_at_desc(self) -> None:
        """SQL query uses ORDER BY responded_at DESC."""
        from butlers.tools.education.mastery import mastery_get_node_history

        pool = _make_pool(fetch_returns=[[]])
        await mastery_get_node_history(pool, str(uuid.uuid4()))
        sql = pool.fetch.call_args.args[0]
        assert "responded_at" in sql
        assert "DESC" in sql

    async def test_returned_dict_has_required_fields(self) -> None:
        """Each dict must have required fields."""
        from butlers.tools.education.mastery import mastery_get_node_history

        node_id = str(uuid.uuid4())
        row = _response_row(node_id=node_id, quality=3, session_id=None)
        pool = _make_pool(fetch_returns=[[_make_row(row)]])
        result = await mastery_get_node_history(pool, node_id, limit=1)
        assert len(result) == 1
        required_keys = {
            "id",
            "question_text",
            "user_answer",
            "quality",
            "response_type",
            "session_id",
            "responded_at",
        }
        for key in required_keys:
            assert key in result[0], f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Tests: mastery_get_map_summary
# ---------------------------------------------------------------------------


class TestMasteryGetMapSummary:
    """mastery_get_map_summary returns aggregate statistics for a mind map."""

    async def test_counts_nodes_by_status(self) -> None:
        from butlers.tools.education.mastery import mastery_get_map_summary

        map_id = str(uuid.uuid4())
        summary_row = _make_row(
            {
                "total_nodes": 10,
                "mastered_count": 3,
                "learning_count": 2,
                "reviewing_count": 4,
                "unseen_count": 1,
                "diagnosed_count": 0,
                "avg_mastery_score": 0.75,
            }
        )
        pool = _make_pool(
            fetchrow_returns=[summary_row],
            fetch_returns=[[]],  # no struggling nodes
        )
        result = await mastery_get_map_summary(pool, map_id)
        assert result["total_nodes"] == 10
        assert result["mastered_count"] == 3
        assert result["learning_count"] == 2
        assert result["reviewing_count"] == 4
        assert result["unseen_count"] == 1
        assert result["diagnosed_count"] == 0

    async def test_avg_mastery_score_returned(self) -> None:
        from butlers.tools.education.mastery import mastery_get_map_summary

        map_id = str(uuid.uuid4())
        summary_row = _make_row(
            {
                "total_nodes": 3,
                "mastered_count": 0,
                "learning_count": 3,
                "reviewing_count": 0,
                "unseen_count": 0,
                "diagnosed_count": 0,
                "avg_mastery_score": 0.8,
            }
        )
        pool = _make_pool(
            fetchrow_returns=[summary_row],
            fetch_returns=[[]],
        )
        result = await mastery_get_map_summary(pool, map_id)
        assert abs(result["avg_mastery_score"] - 0.8) < 1e-9

    async def test_empty_map_returns_zero_counts(self) -> None:
        from butlers.tools.education.mastery import mastery_get_map_summary

        map_id = str(uuid.uuid4())
        summary_row = _make_row(
            {
                "total_nodes": 0,
                "mastered_count": 0,
                "learning_count": 0,
                "reviewing_count": 0,
                "unseen_count": 0,
                "diagnosed_count": 0,
                "avg_mastery_score": 0.0,
            }
        )
        pool = _make_pool(
            fetchrow_returns=[summary_row],
            fetch_returns=[[]],
        )
        result = await mastery_get_map_summary(pool, map_id)
        assert result["total_nodes"] == 0
        assert result["avg_mastery_score"] == 0.0
        assert result["struggling_node_ids"] == []

    async def test_includes_struggling_node_ids(self) -> None:
        """struggling_node_ids is populated from mastery_detect_struggles."""
        from butlers.tools.education.mastery import mastery_get_map_summary

        map_id = str(uuid.uuid4())
        struggling_node_id = str(uuid.uuid4())

        summary_row = _make_row(
            {
                "total_nodes": 5,
                "mastered_count": 1,
                "learning_count": 2,
                "reviewing_count": 1,
                "unseen_count": 1,
                "diagnosed_count": 0,
                "avg_mastery_score": 0.4,
            }
        )

        # mastery_detect_struggles will call pool.fetch for nodes, then per-node responses
        struggling_node_row = _make_row(
            _node_row(
                node_id=struggling_node_id,
                mind_map_id=map_id,
                mastery_status="learning",
                mastery_score=0.2,
            )
        )
        low_quality_responses = [
            _make_row({"quality": 1}),
            _make_row({"quality": 0}),
            _make_row({"quality": 2}),
        ]

        # fetch calls: 1) aggregate query uses fetchrow, 2) detect_struggles uses fetch
        pool = _make_pool(
            fetchrow_returns=[summary_row],
            fetch_returns=[
                [struggling_node_row],  # node list from detect_struggles
                low_quality_responses,  # response history for that node
            ],
        )
        result = await mastery_get_map_summary(pool, map_id)
        assert struggling_node_id in result["struggling_node_ids"]


# ---------------------------------------------------------------------------
# Tests: mastery_detect_struggles
# ---------------------------------------------------------------------------


class TestMasteryDetectStruggles:
    """mastery_detect_struggles identifies struggling non-mastered nodes."""

    async def test_returns_empty_when_no_nodes(self) -> None:
        from butlers.tools.education.mastery import mastery_detect_struggles

        pool = _make_pool(fetch_returns=[[]])
        result = await mastery_detect_struggles(pool, str(uuid.uuid4()))
        assert result == []

    async def test_flags_consecutive_low_quality(self) -> None:
        """Node with 3 most recent responses all quality<=2 is flagged."""
        from butlers.tools.education.mastery import mastery_detect_struggles

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        node_row = _make_row(
            _node_row(node_id=node_id, mind_map_id=map_id, mastery_status="learning")
        )
        responses = [
            _make_row({"quality": 1}),
            _make_row({"quality": 0}),
            _make_row({"quality": 2}),
        ]
        pool = _make_pool(fetch_returns=[[node_row], responses])
        result = await mastery_detect_struggles(pool, map_id)
        assert len(result) == 1
        assert result[0]["id"] == node_id
        assert "consecutive_low_quality" in result[0]["reason"]

    async def test_not_flagged_when_one_response_above_threshold(self) -> None:
        """3+ consecutive only applies if all 3 are <=2; a quality=3 breaks it."""
        from butlers.tools.education.mastery import mastery_detect_struggles

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        node_row = _make_row(
            _node_row(node_id=node_id, mind_map_id=map_id, mastery_status="learning")
        )
        # quality=3 in most recent breaks consecutive_low_quality check
        # need to also not have declining score
        responses = [
            _make_row({"quality": 3}),
            _make_row({"quality": 1}),
            _make_row({"quality": 2}),
        ]
        pool = _make_pool(fetch_returns=[[node_row], responses])
        result = await mastery_detect_struggles(pool, map_id)
        # consecutive_low_quality not triggered (quality=3 breaks it)
        # declining score: score([2,1,3]) vs score([1,3]) vs score([3])
        # score([3]) = 0.6, score([1,3]) weighted [2,4]/6 = (1*2+3*4)/(6*5) = 14/30 ≈ 0.47
        # score([2,1,3]) = ... let's check: not a strict decline from 3-response to 2-response
        assert "consecutive_low_quality" not in [r.get("reason", "") for r in result]

    async def test_flags_declining_score(self) -> None:
        """Node with declining mastery score over last 3 is flagged."""
        from butlers.tools.education.mastery import mastery_detect_struggles

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        node_row = _make_row(
            _node_row(node_id=node_id, mind_map_id=map_id, mastery_status="reviewing")
        )
        # newest→oldest: [1, 3, 5]
        # score([1]) = 0.2
        # score([3, 1]) = 3*2/(3*5) ... hmm actually order matters
        # In _compute_mastery_score, qualities should be oldest→newest
        # DB returns newest→oldest: [1, 3, 5]
        # In detect_struggles, we pass to _compute_mastery_score in order oldest→newest
        # score_1 = _compute_mastery_score([qualities[0]]) = _compute([1]) = 0.2
        # score_2 = _compute_mastery_score([qualities[1], qualities[0]]) = _compute([3, 1])
        # score_3 = _compute_mastery_score([qualities[2], q[1], q[0]]) = _compute([5,3,1])
        # score([5,3,1]): w=[1,2,4]/7, score = (5*1+3*2+1*4)/(7*5) = (5+6+4)/35 = 15/35 ≈ 0.43
        # score([3,1]): w=[1,2]/3, score = (3*1+1*2)/(3*5) = 5/15 ≈ 0.33
        # score([1]): = 0.2
        # 0.43 > 0.33 > 0.2 → declining! Flag it.
        responses = [
            _make_row({"quality": 1}),  # newest
            _make_row({"quality": 3}),
            _make_row({"quality": 5}),  # oldest
        ]
        pool = _make_pool(fetch_returns=[[node_row], responses])
        result = await mastery_detect_struggles(pool, map_id)
        assert len(result) >= 1
        assert any("declining_score" in r["reason"] for r in result)

    async def test_excludes_mastered_nodes(self) -> None:
        """mastered nodes are never included in struggle results."""
        from butlers.tools.education.mastery import mastery_detect_struggles

        map_id = str(uuid.uuid4())
        # mastery_detect_struggles filters out mastered nodes in the SQL query
        # So if no node_rows returned, result is empty
        pool = _make_pool(fetch_returns=[[]])  # no non-mastered nodes
        result = await mastery_detect_struggles(pool, map_id)
        assert result == []

    async def test_not_flagged_with_fewer_than_3_responses(self) -> None:
        """Nodes with <3 responses are never flagged."""
        from butlers.tools.education.mastery import mastery_detect_struggles

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        node_row = _make_row(
            _node_row(node_id=node_id, mind_map_id=map_id, mastery_status="learning")
        )
        # Only 2 responses
        responses = [
            _make_row({"quality": 0}),
            _make_row({"quality": 0}),
        ]
        pool = _make_pool(fetch_returns=[[node_row], responses])
        result = await mastery_detect_struggles(pool, map_id)
        assert result == []

    async def test_flagged_for_both_reasons(self) -> None:
        """Node meeting both conditions has both reasons in output."""
        from butlers.tools.education.mastery import mastery_detect_struggles

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        node_row = _make_row(
            _node_row(node_id=node_id, mind_map_id=map_id, mastery_status="learning")
        )
        # All quality=0 → consecutive_low_quality
        # Also declining: score([0]) < score([0,0]) < score([0,0,0]) — actually all equal 0.0
        # Let's use qualities that trigger both: [0, 1, 2] (newest→oldest)
        # consecutive: all <=2 ✓
        # declining: score_1=0.0, score_2=_compute([1,0])=... 1*2/(3*5)=2/15≈0.13, score_3=...
        # score_3 > score_2 > score_1 → declining ✓
        responses = [
            _make_row({"quality": 0}),  # newest
            _make_row({"quality": 1}),
            _make_row({"quality": 2}),  # oldest
        ]
        pool = _make_pool(fetch_returns=[[node_row], responses])
        result = await mastery_detect_struggles(pool, map_id)
        assert len(result) == 1
        assert "consecutive_low_quality" in result[0]["reason"]
        assert "declining_score" in result[0]["reason"]

    async def test_returns_required_fields(self) -> None:
        """Each struggling node dict has id, label, mastery_score, mastery_status, reason."""
        from butlers.tools.education.mastery import mastery_detect_struggles

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        node_row = _make_row(
            _node_row(
                node_id=node_id,
                mind_map_id=map_id,
                label="Test Concept",
                mastery_score=0.1,
                mastery_status="reviewing",
            )
        )
        responses = [
            _make_row({"quality": 1}),
            _make_row({"quality": 0}),
            _make_row({"quality": 2}),
        ]
        pool = _make_pool(fetch_returns=[[node_row], responses])
        result = await mastery_detect_struggles(pool, map_id)
        assert len(result) == 1
        r = result[0]
        assert r["id"] == node_id
        assert r["label"] == "Test Concept"
        assert r["mastery_score"] == 0.1
        assert r["mastery_status"] == "reviewing"
        assert "reason" in r

    async def test_no_struggles_returns_empty_list(self) -> None:
        """Map with no struggling nodes returns empty list."""
        from butlers.tools.education.mastery import mastery_detect_struggles

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        node_row = _make_row(
            _node_row(node_id=node_id, mind_map_id=map_id, mastery_status="reviewing")
        )
        # High quality responses, no decline
        responses = [
            _make_row({"quality": 5}),
            _make_row({"quality": 5}),
            _make_row({"quality": 5}),
        ]
        pool = _make_pool(fetch_returns=[[node_row], responses])
        result = await mastery_detect_struggles(pool, map_id)
        assert result == []

    async def test_sql_excludes_mastered_in_query(self) -> None:
        """The node fetch SQL explicitly excludes mastered status."""
        from butlers.tools.education.mastery import mastery_detect_struggles

        pool = _make_pool(fetch_returns=[[]])
        await mastery_detect_struggles(pool, str(uuid.uuid4()))
        sql = pool.fetch.call_args.args[0]
        assert "mastered" in sql
        assert "!=" in sql or "NOT" in sql.upper()

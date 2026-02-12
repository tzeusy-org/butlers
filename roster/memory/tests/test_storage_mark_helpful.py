"""Tests for mark_helpful() maturity promotion in Memory Butler storage."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load storage module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_STORAGE_PATH = Path(__file__).resolve().parent.parent / "storage.py"


def _load_storage_module():

    mock_st = MagicMock()
    mock_st.SentenceTransformer.return_value = MagicMock()
    # sys.modules.setdefault("sentence_transformers", mock_st)

    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
mark_helpful = _mod.mark_helpful

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Async context manager helper
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Simple async context manager wrapper returning a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RULE_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _make_row(
    *,
    applied_count: int = 1,
    success_count: int = 1,
    harmful_count: int = 0,
    maturity: str = "candidate",
    effectiveness_score: float = 0.0,
    created_at: datetime | None = None,
) -> dict:
    """Build a dict resembling an asyncpg Record returned by RETURNING *."""
    return {
        "id": _RULE_ID,
        "content": "test rule",
        "embedding": "[0.1, 0.2]",
        "search_vector": "test",
        "scope": "global",
        "maturity": maturity,
        "confidence": 0.5,
        "decay_rate": 0.01,
        "effectiveness_score": effectiveness_score,
        "applied_count": applied_count,
        "success_count": success_count,
        "harmful_count": harmful_count,
        "source_episode_id": None,
        "source_butler": "test-butler",
        "created_at": created_at or datetime.now(UTC),
        "tags": "[]",
        "metadata": "{}",
        "last_applied_at": datetime.now(UTC),
        "reference_count": 0,
        "last_referenced_at": None,
    }


def _make_pool_and_conn(fetchrow_return=None):
    """Create mock pool and conn wired with _AsyncCM pattern."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))

    return pool, conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMarkHelpfulNotFound:
    """mark_helpful returns None when rule not found."""

    async def test_returns_none_when_rule_not_found(self) -> None:
        pool, conn = _make_pool_and_conn(fetchrow_return=None)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is None

    async def test_does_not_call_second_update_when_not_found(self) -> None:
        pool, conn = _make_pool_and_conn(fetchrow_return=None)

        await mark_helpful(pool, _RULE_ID)

        conn.execute.assert_not_awaited()


class TestMarkHelpfulIncrements:
    """mark_helpful increments applied_count and success_count."""

    async def test_sql_increments_applied_count(self) -> None:
        row = _make_row(applied_count=3, success_count=3)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        sql = conn.fetchrow.call_args[0][0]
        assert "applied_count = applied_count + 1" in sql

    async def test_sql_increments_success_count(self) -> None:
        row = _make_row(applied_count=3, success_count=3)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        sql = conn.fetchrow.call_args[0][0]
        assert "success_count = success_count + 1" in sql


class TestMarkHelpfulLastApplied:
    """mark_helpful updates last_applied_at to now()."""

    async def test_sql_updates_last_applied_at(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        sql = conn.fetchrow.call_args[0][0]
        assert "last_applied_at = now()" in sql


class TestMarkHelpfulEffectivenessRecalc:
    """mark_helpful recalculates effectiveness_score."""

    async def test_effectiveness_is_success_over_applied(self) -> None:
        # After the UPDATE, applied=4, success=3 -> effectiveness = 3/4 = 0.75
        row = _make_row(applied_count=4, success_count=3)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["effectiveness_score"] == pytest.approx(0.75)

    async def test_effectiveness_written_to_db(self) -> None:
        row = _make_row(applied_count=10, success_count=8)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        # Second UPDATE should set effectiveness_score
        args = conn.execute.call_args[0]
        sql = args[0]
        assert "effectiveness_score" in sql
        # $1 is effectiveness = 8/10 = 0.8
        assert args[1] == pytest.approx(0.8)


class TestMarkHelpfulNoPromotionCandidate:
    """No promotion when candidate has < 5 successes."""

    async def test_candidate_stays_candidate_below_threshold(self) -> None:
        # success=4 (< 5), so no promotion
        row = _make_row(applied_count=4, success_count=4, maturity="candidate")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "candidate"

    async def test_candidate_stays_when_effectiveness_too_low(self) -> None:
        # success=5 but effectiveness = 5/10 = 0.5 (< 0.6)
        row = _make_row(applied_count=10, success_count=5, maturity="candidate")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "candidate"


class TestMarkHelpfulPromoteCandidateToEstablished:
    """Promotes candidate -> established when success>=5 and effectiveness>=0.6."""

    async def test_promotes_at_exact_threshold(self) -> None:
        # success=5, applied=8 -> effectiveness = 5/8 = 0.625 >= 0.6
        row = _make_row(applied_count=8, success_count=5, maturity="candidate")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "established"

    async def test_promotes_above_threshold(self) -> None:
        # success=7, applied=10 -> effectiveness = 0.7
        row = _make_row(applied_count=10, success_count=7, maturity="candidate")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "established"

    async def test_maturity_written_to_db(self) -> None:
        row = _make_row(applied_count=8, success_count=5, maturity="candidate")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        args = conn.execute.call_args[0]
        # $2 is the maturity value
        assert args[2] == "established"


class TestMarkHelpfulNoPromotionEstablishedYoung:
    """No promotion established -> proven when age < 30 days."""

    async def test_established_stays_when_too_young(self) -> None:
        # success=15, effectiveness=0.8, but age=10 days (< 30)
        created = datetime.now(UTC) - timedelta(days=10)
        row = _make_row(
            applied_count=18,
            success_count=15,
            maturity="established",
            created_at=created,
        )
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "established"


class TestMarkHelpfulPromoteEstablishedToProven:
    """Promotes established -> proven (success>=15, effectiveness>=0.8, age>=30d)."""

    async def test_promotes_at_exact_thresholds(self) -> None:
        created = datetime.now(UTC) - timedelta(days=30)
        # success=15, applied=18 -> effectiveness = 15/18 ~ 0.833
        row = _make_row(
            applied_count=18,
            success_count=15,
            maturity="established",
            created_at=created,
        )
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "proven"

    async def test_no_promotion_when_success_below_15(self) -> None:
        created = datetime.now(UTC) - timedelta(days=60)
        # success=14, effectiveness=14/16=0.875
        row = _make_row(
            applied_count=16,
            success_count=14,
            maturity="established",
            created_at=created,
        )
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "established"

    async def test_no_promotion_when_effectiveness_below_08(self) -> None:
        created = datetime.now(UTC) - timedelta(days=60)
        # success=15, applied=20 -> effectiveness=0.75 (< 0.8)
        row = _make_row(
            applied_count=20,
            success_count=15,
            maturity="established",
            created_at=created,
        )
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "established"


class TestMarkHelpfulReturnsUpdatedDict:
    """mark_helpful returns updated dict with new values."""

    async def test_returned_dict_has_new_effectiveness(self) -> None:
        row = _make_row(applied_count=5, success_count=4)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["effectiveness_score"] == pytest.approx(4 / 5)

    async def test_returned_dict_has_new_maturity(self) -> None:
        row = _make_row(applied_count=8, success_count=6, maturity="candidate")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "established"

    async def test_returned_dict_includes_all_original_keys(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert "id" in result
        assert "content" in result
        assert "scope" in result
        assert "created_at" in result


class TestMarkHelpfulUsesTransaction:
    """mark_helpful uses pool.acquire + conn.transaction for atomicity."""

    async def test_pool_acquire_called(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        pool.acquire.assert_called_once()

    async def test_conn_transaction_called(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        conn.transaction.assert_called_once()

    async def test_passes_rule_id_to_query(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        args = conn.fetchrow.call_args[0]
        assert args[1] == _RULE_ID


class TestMarkHelpfulProvenStaysProven:
    """proven maturity stays proven (no further promotion)."""

    async def test_proven_stays_proven(self) -> None:
        created = datetime.now(UTC) - timedelta(days=90)
        row = _make_row(
            applied_count=50,
            success_count=45,
            maturity="proven",
            created_at=created,
        )
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "proven"

    async def test_proven_effectiveness_still_recalculated(self) -> None:
        created = datetime.now(UTC) - timedelta(days=90)
        row = _make_row(
            applied_count=50,
            success_count=45,
            maturity="proven",
            created_at=created,
        )
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_helpful(pool, _RULE_ID)

        assert result is not None
        assert result["effectiveness_score"] == pytest.approx(45 / 50)

"""Tests for mark_harmful() demotion and anti-pattern triggering in Memory Butler storage."""

from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


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
mark_harmful = _mod.mark_harmful

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
    success_count: int = 0,
    harmful_count: int = 1,
    maturity: str = "candidate",
    effectiveness_score: float = 0.0,
    metadata: dict | None = None,
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
        "created_at": datetime.now(UTC),
        "tags": "[]",
        "metadata": json.dumps(metadata or {}),
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


class TestMarkHarmfulNotFound:
    """mark_harmful returns None when rule not found."""

    async def test_returns_none_when_rule_not_found(self) -> None:
        pool, conn = _make_pool_and_conn(fetchrow_return=None)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is None

    async def test_does_not_call_second_update_when_not_found(self) -> None:
        pool, conn = _make_pool_and_conn(fetchrow_return=None)

        await mark_harmful(pool, _RULE_ID)

        conn.execute.assert_not_awaited()


class TestMarkHarmfulIncrements:
    """mark_harmful increments harmful_count and applied_count."""

    async def test_sql_increments_applied_count(self) -> None:
        row = _make_row(applied_count=3, harmful_count=2)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        sql = conn.fetchrow.call_args[0][0]
        assert "applied_count = applied_count + 1" in sql

    async def test_sql_increments_harmful_count(self) -> None:
        row = _make_row(applied_count=3, harmful_count=2)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        sql = conn.fetchrow.call_args[0][0]
        assert "harmful_count = harmful_count + 1" in sql


class TestMarkHarmfulLastApplied:
    """mark_harmful updates last_applied_at to now()."""

    async def test_sql_updates_last_applied_at(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        sql = conn.fetchrow.call_args[0][0]
        assert "last_applied_at = now()" in sql


class TestMarkHarmfulEffectivenessRecalc:
    """mark_harmful recalculates effectiveness with 4x harmful penalty."""

    async def test_effectiveness_formula_with_4x_penalty(self) -> None:
        # success=3, harmful=2 -> 3 / (3 + 4*2 + 0.01) = 3 / 11.01 ~ 0.2725
        row = _make_row(applied_count=6, success_count=3, harmful_count=2)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        expected = 3 / (3 + 4 * 2 + 0.01)
        assert result["effectiveness_score"] == pytest.approx(expected)

    async def test_effectiveness_written_to_db(self) -> None:
        # success=5, harmful=1 -> 5 / (5 + 4*1 + 0.01) = 5 / 9.01 ~ 0.555
        row = _make_row(applied_count=7, success_count=5, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        args = conn.execute.call_args[0]
        sql = args[0]
        assert "effectiveness_score" in sql
        expected = 5 / (5 + 4 * 1 + 0.01)
        assert args[1] == pytest.approx(expected)

    async def test_effectiveness_zero_success_zero_harmful(self) -> None:
        # Edge: success=0, harmful=1 -> 0 / (0 + 4*1 + 0.01) = 0 / 4.01 ~ 0.0
        row = _make_row(applied_count=1, success_count=0, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        expected = 0 / (0 + 4 * 1 + 0.01)
        assert result["effectiveness_score"] == pytest.approx(expected)


class TestMarkHarmfulNoDemotionCandidate:
    """No demotion when rule is already candidate (lowest maturity)."""

    async def test_candidate_stays_candidate(self) -> None:
        # Candidate can't be demoted further
        row = _make_row(applied_count=5, success_count=0, harmful_count=3, maturity="candidate")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "candidate"


class TestMarkHarmfulDemoteEstablishedToCandidate:
    """Demotes established -> candidate when effectiveness < 0.6."""

    async def test_demotes_when_effectiveness_below_06(self) -> None:
        # success=1, harmful=2 -> 1 / (1 + 4*2 + 0.01) = 1/9.01 ~ 0.111 < 0.6
        row = _make_row(applied_count=5, success_count=1, harmful_count=2, maturity="established")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "candidate"

    async def test_no_demotion_when_effectiveness_at_06(self) -> None:
        # Need success / (success + 4*harmful + 0.01) >= 0.6
        # success=6, harmful=1 -> 6 / (6 + 4 + 0.01) = 6/10.01 ~ 0.599 < 0.6
        # Actually 0.5994... which IS < 0.6, so demotion triggers.
        # Let's use values that give >= 0.6:
        # success=10, harmful=1 -> 10 / (10 + 4 + 0.01) = 10/14.01 ~ 0.714 >= 0.6
        row = _make_row(applied_count=12, success_count=10, harmful_count=1, maturity="established")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "established"

    async def test_demotion_maturity_written_to_db(self) -> None:
        row = _make_row(applied_count=5, success_count=1, harmful_count=2, maturity="established")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        args = conn.execute.call_args[0]
        # $2 is maturity
        assert args[2] == "candidate"


class TestMarkHarmfulDemoteProvenToEstablished:
    """Demotes proven -> established when effectiveness < 0.8."""

    async def test_demotes_when_effectiveness_below_08(self) -> None:
        # success=5, harmful=1 -> 5 / (5 + 4 + 0.01) = 5/9.01 ~ 0.555 < 0.8
        row = _make_row(applied_count=7, success_count=5, harmful_count=1, maturity="proven")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "established"

    async def test_no_demotion_when_effectiveness_above_08(self) -> None:
        # success=20, harmful=1 -> 20 / (20 + 4 + 0.01) = 20/24.01 ~ 0.833 >= 0.8
        row = _make_row(applied_count=22, success_count=20, harmful_count=1, maturity="proven")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "proven"


class TestMarkHarmfulReasonStorage:
    """mark_harmful stores reason in metadata.harmful_reasons."""

    async def test_stores_reason_in_metadata(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID, reason="caused confusion")

        assert result is not None
        assert "harmful_reasons" in result["metadata"]
        assert result["metadata"]["harmful_reasons"] == ["caused confusion"]

    async def test_multiple_reasons_accumulate(self) -> None:
        existing_meta = {"harmful_reasons": ["first issue"]}
        row = _make_row(metadata=existing_meta)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID, reason="second issue")

        assert result is not None
        assert result["metadata"]["harmful_reasons"] == ["first issue", "second issue"]

    async def test_reason_none_does_not_add_to_reasons(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID, reason=None)

        assert result is not None
        assert "harmful_reasons" not in result["metadata"]

    async def test_metadata_written_to_db_as_json(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID, reason="bad advice")

        args = conn.execute.call_args[0]
        # $3 is the metadata JSON string
        metadata_json = args[3]
        parsed = json.loads(metadata_json)
        assert parsed["harmful_reasons"] == ["bad advice"]


class TestMarkHarmfulAntiPatternInversion:
    """Sets needs_inversion when harmful>=3 and effectiveness<0.3."""

    async def test_sets_needs_inversion_flag(self) -> None:
        # success=0, harmful=3 -> 0 / (0 + 12 + 0.01) = 0 < 0.3
        row = _make_row(applied_count=4, success_count=0, harmful_count=3)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        assert result["metadata"].get("needs_inversion") is True

    async def test_no_inversion_when_harmful_below_3(self) -> None:
        # harmful=2, effectiveness low but count not met
        row = _make_row(applied_count=3, success_count=0, harmful_count=2)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        assert "needs_inversion" not in result["metadata"]

    async def test_no_inversion_when_effectiveness_above_03(self) -> None:
        # success=10, harmful=3 -> 10 / (10 + 12 + 0.01) = 10/22.01 ~ 0.454 > 0.3
        row = _make_row(applied_count=14, success_count=10, harmful_count=3)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        assert "needs_inversion" not in result["metadata"]

    async def test_inversion_flag_written_to_db(self) -> None:
        row = _make_row(applied_count=4, success_count=0, harmful_count=3)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        args = conn.execute.call_args[0]
        metadata_json = args[3]
        parsed = json.loads(metadata_json)
        assert parsed["needs_inversion"] is True


class TestMarkHarmfulUsesTransaction:
    """mark_harmful uses pool.acquire + conn.transaction for atomicity."""

    async def test_pool_acquire_called(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        pool.acquire.assert_called_once()

    async def test_conn_transaction_called(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        conn.transaction.assert_called_once()

    async def test_passes_rule_id_to_query(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        args = conn.fetchrow.call_args[0]
        assert args[1] == _RULE_ID


class TestMarkHarmfulReturnsUpdatedDict:
    """mark_harmful returns updated dict with new values."""

    async def test_returned_dict_has_new_effectiveness(self) -> None:
        row = _make_row(applied_count=5, success_count=2, harmful_count=2)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        expected = 2 / (2 + 4 * 2 + 0.01)
        assert result["effectiveness_score"] == pytest.approx(expected)

    async def test_returned_dict_has_new_maturity(self) -> None:
        row = _make_row(applied_count=5, success_count=1, harmful_count=2, maturity="established")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        assert result["maturity"] == "candidate"

    async def test_returned_dict_includes_all_original_keys(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        result = await mark_harmful(pool, _RULE_ID)

        assert result is not None
        assert "id" in result
        assert "content" in result
        assert "scope" in result
        assert "created_at" in result

"""Tests for invert_to_anti_pattern() in Memory Butler storage."""

from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, datetime
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
invert_to_anti_pattern = _mod.invert_to_anti_pattern

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
    content: str = "always deploy on Fridays",
    harmful_count: int = 3,
    effectiveness_score: float = 0.1,
    maturity: str = "candidate",
    metadata: dict | str | None = None,
) -> dict:
    """Build a dict resembling an asyncpg Record from SELECT * on rules."""
    if metadata is None:
        metadata = json.dumps({"harmful_reasons": ["broke prod", "caused outage", "data loss"]})
    elif isinstance(metadata, dict):
        metadata = json.dumps(metadata)
    return {
        "id": _RULE_ID,
        "content": content,
        "embedding": "[0.1, 0.2]",
        "search_vector": "test",
        "scope": "global",
        "maturity": maturity,
        "confidence": 0.5,
        "decay_rate": 0.01,
        "effectiveness_score": effectiveness_score,
        "applied_count": 10,
        "success_count": 1,
        "harmful_count": harmful_count,
        "source_episode_id": None,
        "source_butler": "test-butler",
        "created_at": datetime.now(UTC),
        "tags": "[]",
        "metadata": metadata,
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


def _make_embedding_engine(return_value=None):
    """Create a mock EmbeddingEngine."""
    engine = MagicMock()
    engine.embed = MagicMock(return_value=return_value or [0.5, 0.6, 0.7])
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInvertNotFound:
    """invert_to_anti_pattern returns None when rule not found."""

    async def test_returns_none_when_rule_not_found(self) -> None:
        pool, conn = _make_pool_and_conn(fetchrow_return=None)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert result is None


class TestInvertCriteriaNotMet:
    """invert_to_anti_pattern returns None when criteria not met."""

    async def test_returns_none_when_harmful_count_below_3(self) -> None:
        row = _make_row(harmful_count=2, effectiveness_score=0.1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert result is None

    async def test_returns_none_when_effectiveness_at_threshold(self) -> None:
        row = _make_row(harmful_count=5, effectiveness_score=0.3)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert result is None

    async def test_returns_none_when_effectiveness_above_threshold(self) -> None:
        row = _make_row(harmful_count=3, effectiveness_score=0.5)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert result is None


class TestInvertAlreadyAntiPattern:
    """Returns existing row when already anti_pattern."""

    async def test_returns_existing_row_when_already_anti_pattern(self) -> None:
        row = _make_row(maturity="anti_pattern")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert result is not None
        assert result["maturity"] == "anti_pattern"
        # Should NOT have called execute (no update needed)
        conn.execute.assert_not_awaited()


class TestInvertContentRewrite:
    """Verifies the anti-pattern content rewrite."""

    async def test_rewrites_content_with_anti_pattern_prefix(self) -> None:
        row = _make_row(content="always deploy on Fridays")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert result["content"].startswith("ANTI-PATTERN: Do NOT ")

    async def test_includes_original_rule_in_content(self) -> None:
        row = _make_row(content="always deploy on Fridays")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert "always deploy on Fridays" in result["content"]

    async def test_includes_harmful_reasons_in_content(self) -> None:
        row = _make_row(metadata={"harmful_reasons": ["broke prod", "caused outage", "data loss"]})
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert "broke prod; caused outage; data loss" in result["content"]

    async def test_falls_back_to_repeated_failures_when_no_reasons(self) -> None:
        row = _make_row(metadata={})
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert "repeated failures" in result["content"]


class TestInvertMetadata:
    """Verifies metadata updates."""

    async def test_preserves_original_content_in_metadata(self) -> None:
        row = _make_row(content="always deploy on Fridays")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert result["metadata"]["original_content"] == "always deploy on Fridays"

    async def test_sets_needs_inversion_false(self) -> None:
        row = _make_row(metadata={"needs_inversion": True, "harmful_reasons": ["bad"]})
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert result["metadata"]["needs_inversion"] is False


class TestInvertMaturity:
    """Sets maturity to anti_pattern."""

    async def test_sets_maturity_to_anti_pattern(self) -> None:
        row = _make_row(maturity="candidate")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        assert result["maturity"] == "anti_pattern"


class TestInvertReEmbed:
    """Re-embeds the new content."""

    async def test_re_embeds_the_new_content(self) -> None:
        row = _make_row(content="always deploy on Fridays")
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine(return_value=[0.9, 0.8, 0.7])

        result = await invert_to_anti_pattern(pool, _RULE_ID, engine)

        # embed was called with the new anti-pattern content
        engine.embed.assert_called_once()
        call_arg = engine.embed.call_args[0][0]
        assert call_arg.startswith("ANTI-PATTERN: Do NOT ")
        assert result["embedding"] == [0.9, 0.8, 0.7]


class TestInvertSearchVector:
    """Updates search_vector via tsvector_sql."""

    async def test_updates_search_vector_in_sql(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        await invert_to_anti_pattern(pool, _RULE_ID, engine)

        sql = conn.execute.call_args[0][0]
        assert "search_vector" in sql
        assert "to_tsvector" in sql


class TestInvertTransaction:
    """Uses transaction for atomicity."""

    async def test_uses_transaction(self) -> None:
        row = _make_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine()

        await invert_to_anti_pattern(pool, _RULE_ID, engine)

        conn.transaction.assert_called_once()


class TestInvertDbUpdate:
    """Verifies the database UPDATE call."""

    async def test_passes_correct_args_to_execute(self) -> None:
        row = _make_row(
            content="always deploy on Fridays",
            metadata={"harmful_reasons": ["broke prod", "caused outage", "data loss"]},
        )
        pool, conn = _make_pool_and_conn(fetchrow_return=row)
        engine = _make_embedding_engine(return_value=[0.9, 0.8, 0.7])

        await invert_to_anti_pattern(pool, _RULE_ID, engine)

        call_args = conn.execute.call_args[0]
        sql = call_args[0]

        # Check SQL structure
        assert "UPDATE rules" in sql
        assert "maturity = 'anti_pattern'" in sql

        # Check positional parameters
        anti_content = call_args[1]
        assert anti_content.startswith("ANTI-PATTERN: Do NOT always deploy on Fridays")

        embedding_str = call_args[2]
        assert embedding_str == str([0.9, 0.8, 0.7])

        metadata_str = call_args[4]
        meta = json.loads(metadata_str)
        assert meta["original_content"] == "always deploy on Fridays"
        assert meta["needs_inversion"] is False

        passed_rule_id = call_args[5]
        assert passed_rule_id == _RULE_ID

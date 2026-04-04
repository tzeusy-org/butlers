"""Behavioral tests for memory reading MCP tools.

Covers:
  - memory_search: query with mode/type/scope filtering
  - memory_recall: composite recall with scoring
  - memory_get: fetch single memory by ID
  - memory_confirm: confidence reset
  - memory_mark_helpful / memory_mark_harmful: rule effectiveness feedback
  - memory_forget: soft-delete with correction provenance
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.memory.tools import (
    _helpers,
    memory_confirm,
    memory_forget,
    memory_get,
    memory_mark_harmful,
    memory_mark_helpful,
    memory_recall,
    memory_search,
)

pytestmark = pytest.mark.unit

CorrectionGuardError = _helpers._storage.CorrectionGuardError

SAMPLE_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")
SAMPLE_STR = str(SAMPLE_UUID)


@pytest.fixture()
def pool() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def engine() -> MagicMock:
    m = MagicMock()
    m.embed.return_value = [0.1] * 384
    return m


# ---------------------------------------------------------------------------
# memory_search
# ---------------------------------------------------------------------------


class TestMemorySearch:
    async def test_serializes_results(self, pool: AsyncMock, engine: MagicMock) -> None:
        dt = datetime(2025, 3, 1, tzinfo=UTC)
        _helpers._search.search = AsyncMock(return_value=[{"id": SAMPLE_UUID, "created_at": dt}])
        results = await memory_search(pool, engine, "query")
        assert results[0]["id"] == SAMPLE_STR
        assert results[0]["created_at"] == dt.isoformat()

    async def test_optional_params_forwarded(self, pool: AsyncMock, engine: MagicMock) -> None:
        _helpers._search.search = AsyncMock(return_value=[])
        await memory_search(pool, engine, "q", types=["fact"], scope="s", mode="semantic", limit=5)
        kw = _helpers._search.search.call_args.kwargs
        assert kw["types"] == ["fact"] and kw["scope"] == "s" and kw["limit"] == 5


# ---------------------------------------------------------------------------
# memory_recall
# ---------------------------------------------------------------------------


class TestMemoryRecall:
    async def test_serializes_results(self, pool: AsyncMock, engine: MagicMock) -> None:
        dt = datetime(2025, 5, 20, tzinfo=UTC)
        _helpers._search.recall = AsyncMock(
            return_value=[{"id": SAMPLE_UUID, "last_referenced_at": dt}]
        )
        results = await memory_recall(pool, engine, "topic")
        assert results[0]["id"] == SAMPLE_STR
        assert results[0]["last_referenced_at"] == dt.isoformat()


# ---------------------------------------------------------------------------
# memory_get
# ---------------------------------------------------------------------------


class TestMemoryGet:
    async def test_returns_none_when_not_found(self, pool: AsyncMock) -> None:
        _helpers._storage.get_memory = AsyncMock(return_value=None)
        assert await memory_get(pool, "fact", SAMPLE_STR) is None

    async def test_serializes_result(self, pool: AsyncMock) -> None:
        dt = datetime(2025, 4, 10, tzinfo=UTC)
        _helpers._storage.get_memory = AsyncMock(
            return_value={"id": SAMPLE_UUID, "created_at": dt, "content": "hello"}
        )
        result = await memory_get(pool, "fact", SAMPLE_STR)
        assert result["id"] == SAMPLE_STR
        assert result["created_at"] == dt.isoformat()


# ---------------------------------------------------------------------------
# memory_confirm
# ---------------------------------------------------------------------------


class TestMemoryConfirm:
    async def test_confirmed_true(self, pool: AsyncMock) -> None:
        _helpers._storage.confirm_memory = AsyncMock(return_value=True)
        assert await memory_confirm(pool, "fact", SAMPLE_STR) == {"confirmed": True}


# ---------------------------------------------------------------------------
# memory_mark_helpful / memory_mark_harmful
# ---------------------------------------------------------------------------


class TestMemoryFeedback:
    async def test_mark_helpful_serializes(self, pool: AsyncMock) -> None:
        dt = datetime(2025, 7, 1, tzinfo=UTC)
        _helpers._storage.mark_helpful = AsyncMock(
            return_value={"id": SAMPLE_UUID, "last_applied_at": dt, "success_count": 3}
        )
        result = await memory_mark_helpful(pool, SAMPLE_STR)
        assert result["id"] == SAMPLE_STR and result["last_applied_at"] == dt.isoformat()

    async def test_mark_helpful_returns_error_when_not_found(self, pool: AsyncMock) -> None:
        _helpers._storage.mark_helpful = AsyncMock(return_value=None)
        result = await memory_mark_helpful(pool, SAMPLE_STR)
        assert "error" in result

    async def test_mark_harmful_serializes(self, pool: AsyncMock) -> None:
        _helpers._storage.mark_harmful = AsyncMock(return_value={"id": SAMPLE_UUID})
        assert (await memory_mark_harmful(pool, SAMPLE_STR))["id"] == SAMPLE_STR


# ---------------------------------------------------------------------------
# memory_forget
# ---------------------------------------------------------------------------


class TestMemoryForget:
    async def test_returns_forgotten_true(self, pool: AsyncMock) -> None:
        _helpers._storage.forget_memory = AsyncMock(return_value=True)
        assert await memory_forget(pool, "fact", SAMPLE_STR) == {"forgotten": True}

    async def test_correction_guard_returns_structured_error(self, pool: AsyncMock) -> None:
        err = CorrectionGuardError("already_retracted", "already retracted")
        _helpers._storage.forget_memory = AsyncMock(side_effect=err)
        result = await memory_forget(pool, "fact", SAMPLE_STR)
        assert result["forgotten"] is False and "error" in result

    async def test_correction_id_forwarded(self, pool: AsyncMock) -> None:
        cid = str(uuid.uuid4())
        _helpers._storage.forget_memory = AsyncMock(return_value=True)
        await memory_forget(pool, "fact", SAMPLE_STR, correction_id=cid, correction_reason="bad")
        kw = _helpers._storage.forget_memory.call_args[1]
        assert kw["correction_id"] == cid and kw["correction_reason"] == "bad"

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
    memory_mark_harmful,
    memory_mark_helpful,
)
from butlers.modules.memory.tools.reading import memory_get, memory_recall, memory_search

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
# Serialization contract: every read tool serializes UUID->str + datetime->isoformat
# ---------------------------------------------------------------------------


class TestSerializationContract:
    @pytest.mark.parametrize(
        ("tool", "dt_field"),
        [
            ("search", "created_at"),
            ("recall", "last_referenced_at"),
            ("get", "created_at"),
            ("mark_helpful", "last_applied_at"),
        ],
    )
    async def test_uuid_and_datetime_serialized(
        self,
        pool: AsyncMock,
        engine: MagicMock,
        tool: str,
        dt_field: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        dt = datetime(2025, 3, 1, tzinfo=UTC)
        row = {"id": SAMPLE_UUID, dt_field: dt}
        if tool == "search":
            monkeypatch.setattr(_helpers._search, "search", AsyncMock(return_value=[row]))
            result = (await memory_search(pool, engine, "query"))[0]
        elif tool == "recall":
            monkeypatch.setattr(_helpers._search, "recall", AsyncMock(return_value=[row]))
            result = (await memory_recall(pool, engine, "topic"))[0]
        elif tool == "get":
            monkeypatch.setattr(
                _helpers._storage,
                "get_memory",
                AsyncMock(return_value={**row, "content": "hello"}),
            )
            result = await memory_get(pool, "fact", SAMPLE_STR)
        else:  # mark_helpful
            monkeypatch.setattr(
                _helpers._storage,
                "mark_helpful",
                AsyncMock(return_value={**row, "success_count": 3}),
            )
            result = await memory_mark_helpful(pool, SAMPLE_STR)

        assert result["id"] == SAMPLE_STR
        assert result[dt_field] == dt.isoformat()


# ---------------------------------------------------------------------------
# memory_get
# ---------------------------------------------------------------------------


class TestMemoryGet:
    async def test_returns_none_when_not_found(
        self, pool: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_helpers._storage, "get_memory", AsyncMock(return_value=None))
        assert await memory_get(pool, "fact", SAMPLE_STR) is None


class TestReadPolicyForwarding:
    async def test_search_forwards_held_policy(
        self, pool: AsyncMock, engine: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        policy = _helpers._search.resolve_catalog_read_policy("internal")
        search = AsyncMock(return_value=[])
        monkeypatch.setattr(_helpers._search, "search", search)

        result = await memory_search(pool, engine, "topic", read_policy=policy)

        assert result == []
        assert search.await_args.kwargs["read_policy"] is policy

    async def test_recall_forwards_held_policy(
        self, pool: AsyncMock, engine: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        policy = _helpers._search.resolve_catalog_read_policy("internal")
        recall = AsyncMock(return_value=[])
        monkeypatch.setattr(_helpers._search, "recall", recall)

        result = await memory_recall(pool, engine, "topic", read_policy=policy)

        assert result == []
        assert recall.await_args.kwargs["read_policy"] is policy


# ---------------------------------------------------------------------------
# memory_confirm
# ---------------------------------------------------------------------------


class TestMemoryConfirm:
    async def test_confirmed_true(self, pool: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
        confirm = AsyncMock(return_value=True)
        monkeypatch.setattr(_helpers._storage, "confirm_memory", confirm)
        policy = _helpers._search.resolve_catalog_read_policy("normal")

        assert await memory_confirm(pool, "fact", SAMPLE_STR, read_policy=policy) == {
            "confirmed": True
        }
        confirm.assert_awaited_once_with(
            pool,
            "fact",
            SAMPLE_UUID,
            allowed_sensitivities=policy.allowed_sensitivities,
        )

    async def test_typed_reference_resolves_without_legacy_target_fields(
        self, pool: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        confirm = AsyncMock(return_value=True)
        monkeypatch.setattr(_helpers._storage, "confirm_memory", confirm)

        result = await memory_confirm(pool, memory_ref=f"fact:{SAMPLE_STR}")

        assert result == {"confirmed": True}
        assert confirm.await_args.args[1:3] == ("fact", SAMPLE_UUID)

    @pytest.mark.parametrize(
        "memory_ref",
        [
            f"episode:{SAMPLE_STR}",
            f"FACT:{SAMPLE_STR}",
            "fact:not-a-uuid",
        ],
    )
    async def test_invalid_reference_refuses_without_storage_lookup(
        self,
        pool: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
        memory_ref: str,
    ) -> None:
        confirm = AsyncMock()
        monkeypatch.setattr(_helpers._storage, "confirm_memory", confirm)

        result = await memory_confirm(pool, memory_ref=memory_ref)

        assert result == {"confirmed": False, "error": "Memory reference unavailable"}
        confirm.assert_not_awaited()

    async def test_reference_and_legacy_target_are_mutually_exclusive(
        self, pool: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        confirm = AsyncMock()
        monkeypatch.setattr(_helpers._storage, "confirm_memory", confirm)

        result = await memory_confirm(
            pool,
            "fact",
            SAMPLE_STR,
            memory_ref=f"fact:{SAMPLE_STR}",
        )

        assert result == {"confirmed": False, "error": "Memory reference unavailable"}
        confirm.assert_not_awaited()


# ---------------------------------------------------------------------------
# memory_mark_helpful / memory_mark_harmful
# ---------------------------------------------------------------------------


class TestMemoryFeedback:
    async def test_mark_helpful_returns_error_when_not_found(
        self, pool: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_helpers._storage, "mark_helpful", AsyncMock(return_value=None))
        result = await memory_mark_helpful(pool, SAMPLE_STR)
        assert "error" in result

    async def test_mark_harmful_serializes(
        self, pool: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _helpers._storage, "mark_harmful", AsyncMock(return_value={"id": SAMPLE_UUID})
        )
        assert (await memory_mark_harmful(pool, SAMPLE_STR))["id"] == SAMPLE_STR

    @pytest.mark.parametrize("action", [memory_mark_helpful, memory_mark_harmful])
    async def test_rule_reference_resolves_with_held_policy(
        self,
        pool: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
        action,
    ) -> None:
        storage_action = AsyncMock(return_value={"id": SAMPLE_UUID})
        storage_name = "mark_helpful" if action is memory_mark_helpful else "mark_harmful"
        monkeypatch.setattr(_helpers._storage, storage_name, storage_action)
        policy = _helpers._search.resolve_catalog_read_policy("internal")

        result = await action(
            pool,
            memory_ref=f"rule:{SAMPLE_STR}",
            read_policy=policy,
        )

        assert result["id"] == SAMPLE_STR
        assert storage_action.await_args.args[:2] == (pool, SAMPLE_UUID)
        assert storage_action.await_args.kwargs["allowed_sensitivities"] == (
            "normal",
            "pii",
        )

    @pytest.mark.parametrize("action", [memory_mark_helpful, memory_mark_harmful])
    async def test_fact_reference_is_content_free_refusal_for_rule_feedback(
        self,
        pool: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
        action,
    ) -> None:
        storage_action = AsyncMock()
        storage_name = "mark_helpful" if action is memory_mark_helpful else "mark_harmful"
        monkeypatch.setattr(_helpers._storage, storage_name, storage_action)

        result = await action(pool, memory_ref=f"fact:{SAMPLE_STR}")

        assert result == {"error": "Memory reference unavailable"}
        assert SAMPLE_STR not in str(result)
        storage_action.assert_not_awaited()


# ---------------------------------------------------------------------------
# memory_forget
# ---------------------------------------------------------------------------


class TestMemoryForget:
    async def test_returns_forgotten_true(
        self, pool: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_helpers._storage, "forget_memory", AsyncMock(return_value=True))
        assert await memory_forget(pool, "fact", SAMPLE_STR) == {"forgotten": True}

    async def test_correction_guard_returns_structured_error(
        self, pool: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        err = CorrectionGuardError("already_retracted", "already retracted")
        monkeypatch.setattr(_helpers._storage, "forget_memory", AsyncMock(side_effect=err))
        result = await memory_forget(pool, "fact", SAMPLE_STR)
        assert result["forgotten"] is False and "error" in result

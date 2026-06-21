"""Unit tests for structured error responses from the memory_store_fact MCP tool.

Covers tasks 5.1–5.3 from openspec/changes/predicate-registry-enforcement/tasks.md.

Tests verify that:
  - _infer_recovery_steps() returns the correct recovery string for each ValueError pattern
  - The memory_store_fact MCP closure catches ValueError and returns a structured dict
  - All 6 validation failure scenarios produce isError=false structured responses:
    1. Invalid entity_id (entity does not exist)
    2. Edge predicate missing object_entity_id
    3. Temporal predicate missing valid_at
    4. Self-referencing edge (entity_id == object_entity_id)
    5. UUID embedded in content without object_entity_id
    6. Invalid permanence value
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory import MemoryModule, _infer_recovery_steps
from tests.modules.memory._test_helpers import make_embedding_engine_mock

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Tests — _infer_recovery_steps() helper
# ---------------------------------------------------------------------------


class TestInferRecoverySteps:
    """_infer_recovery_steps() maps each ValueError class to actionable next-tool hints."""

    @pytest.mark.parametrize(
        ("error_msg", "required_substrings"),
        [
            # Scenario 1: invalid entity_id → resolve + create fallback
            (
                "entity_id UUID('abc') does not exist in entities table",
                ["memory_entity_resolve", "memory_entity_create"],
            ),
            # Scenario 2: edge predicate missing object_entity_id
            (
                "Predicate 'parent_of' is registered as an edge predicate "
                "(is_edge=true) and requires object_entity_id to be set. "
                "Call memory_entity_resolve(identifier=<target_name>) to resolve "
                "the target entity, then retry with object_entity_id.",
                ["object_entity_id", "memory_entity_resolve"],
            ),
            # Scenario 3: temporal predicate missing valid_at (+ ISO-8601 guidance)
            (
                "Predicate 'interaction' is registered as a temporal predicate "
                "(is_temporal=true) and requires valid_at to be set.",
                ["valid_at", "ISO-8601"],
            ),
            # Scenario 4: self-referencing edge
            (
                "Self-referencing edges are not allowed: "
                "entity_id and object_entity_id must differ",
                ["object_entity_id", "memory_entity_resolve"],
            ),
            # Scenario 5: UUID embedded in content
            (
                "content contains an embedded UUID (abc-123) but object_entity_id is not set.",
                ["object_entity_id", "memory_entity_resolve"],
            ),
            # Scenario 6: invalid permanence → lists the valid enum values
            (
                "Invalid permanence: 'forever'. Must be one of "
                "['ephemeral', 'permanent', 'stable', 'standard', 'volatile']",
                ["permanent", "stable", "standard", "volatile", "ephemeral"],
            ),
        ],
    )
    def test_recovery_routing_per_pattern(
        self, error_msg: str, required_substrings: list[str]
    ) -> None:
        recovery = _infer_recovery_steps(ValueError(error_msg))
        assert isinstance(recovery, str) and recovery
        for needle in required_substrings:
            assert needle in recovery, f"{needle!r} missing from recovery for {error_msg!r}"

    def test_unknown_error_returns_generic_recovery(self) -> None:
        """An unrecognized ValueError message returns the generic fallback recovery."""
        recovery = _infer_recovery_steps(ValueError("Something completely unexpected happened"))
        assert recovery  # non-empty
        assert "memory_store_fact" in recovery or "memory_predicate_list" in recovery


# ---------------------------------------------------------------------------
# Tests — MCP tool wraps ValueError in structured dict
# ---------------------------------------------------------------------------


class TestMemoryStoreFactStructuredErrors:
    """Verify memory_store_fact MCP closure catches ValueError and returns structured dicts.

    The structured dict must have 'error', 'message', and 'recovery' keys.
    isError is implicitly False since we return a normal dict (not raise an exception).
    """

    async def _setup_and_get_fact_tool(self):
        """Register tools with mocked implementations and return memory_store_fact."""
        mod = MemoryModule()
        fake_db = MagicMock()
        fake_db.pool = MagicMock(name="fake_pool")
        mock_writing = MagicMock()

        parent_mock = MagicMock()
        parent_mock.writing = mock_writing

        mcp = MagicMock()
        registered_tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool

        with patch.dict(
            "sys.modules",
            {
                "butlers.modules.memory.tools": parent_mock,
                "butlers.modules.memory.tools.writing": mock_writing,
                "butlers.modules.memory.tools.reading": MagicMock(),
                "butlers.modules.memory.tools.feedback": MagicMock(),
                "butlers.modules.memory.tools.management": MagicMock(),
                "butlers.modules.memory.tools.context": MagicMock(),
                "butlers.modules.memory.tools.entities": MagicMock(),
            },
        ):
            await mod.register_tools(mcp=mcp, config=None, db=fake_db, butler_name="test-butler")

        mod._embedding_engine = make_embedding_engine_mock(mod._config.embedding_model)
        return mod, registered_tools["memory_store_fact"], fake_db.pool, mock_writing

    def _patch_routing(self, entity_id: str):
        """Helper: patch routing context to provide a valid entity_id."""
        return patch(
            "butlers.modules.memory.get_current_runtime_session_routing_context",
            return_value={"source_entity_id": entity_id},
        )

    @pytest.mark.parametrize(
        ("error_msg", "extra_kwargs", "recovery_substrings"),
        [
            # Scenario 1: invalid entity_id
            (
                "entity_id UUID('550e8400-e29b-41d4-a716-446655440000') "
                "does not exist in entities table",
                {},
                ["memory_entity_resolve"],
            ),
            # Scenario 2: edge predicate missing object_entity_id
            (
                "Predicate 'parent_of' is registered as an edge predicate "
                "(is_edge=true) and requires object_entity_id to be set. "
                "Call memory_entity_resolve(identifier=<target_name>) to resolve "
                "the target entity, then retry with object_entity_id.",
                {"predicate": "parent_of"},
                ["object_entity_id", "memory_entity_resolve"],
            ),
            # Scenario 3: temporal predicate missing valid_at
            (
                "Predicate 'interaction' is registered as a temporal predicate "
                "(is_temporal=true) and requires valid_at to be set. "
                "Provide an ISO-8601 valid_at timestamp.",
                {"predicate": "interaction"},
                ["valid_at"],
            ),
            # Scenario 4: self-referencing edge
            (
                "Self-referencing edges are not allowed: "
                "entity_id and object_entity_id must differ",
                {"predicate": "knows", "object_entity_id": "550e8400-e29b-41d4-a716-446655440000"},
                ["object_entity_id"],
            ),
            # Scenario 5: UUID embedded in content
            (
                "content contains an embedded UUID (660e8400-e29b-41d4-a716-446655440001) but "
                "object_entity_id is not set.",
                {"predicate": "knows"},
                ["object_entity_id", "memory_entity_resolve"],
            ),
            # Scenario 6: invalid permanence
            (
                "Invalid permanence: 'forever'. Must be one of "
                "['ephemeral', 'permanent', 'stable', 'standard', 'volatile']",
                {"permanence": "forever"},
                ["permanent"],
            ),
        ],
    )
    async def test_value_error_returns_structured_dict(
        self, error_msg: str, extra_kwargs: dict, recovery_substrings: list[str]
    ) -> None:
        """Each validation failure class returns {error, message, recovery} (isError=false)."""
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()
        writing.memory_store_fact = AsyncMock(side_effect=ValueError(error_msg))

        kwargs: dict[str, Any] = {
            "subject": "user",
            "predicate": "name",
            "content": "Alice",
            "entity_id": entity_uuid,
            **extra_kwargs,
        }
        with self._patch_routing(entity_uuid):
            result = await fact_tool(**kwargs)

        assert "error" in result
        assert "message" in result
        assert "recovery" in result
        for needle in recovery_substrings:
            assert needle in result["recovery"]

    # --- Structured dict shape ---

    async def test_structured_error_has_exactly_three_keys(self) -> None:
        """Structured error dict must have exactly error, message, and recovery keys."""
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()
        writing.memory_store_fact = AsyncMock(
            side_effect=ValueError("entity_id UUID('x') does not exist in entities table")
        )

        with self._patch_routing(entity_uuid):
            result = await fact_tool(
                subject="user",
                predicate="name",
                content="Alice",
                entity_id=entity_uuid,
            )

        assert set(result.keys()) == {"error", "message", "recovery"}

    async def test_error_and_message_contain_original_exception_text(self) -> None:
        """The error and message fields must contain the original exception text."""
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        original_msg = "entity_id UUID('abc') does not exist in entities table"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()
        writing.memory_store_fact = AsyncMock(side_effect=ValueError(original_msg))

        with self._patch_routing(entity_uuid):
            result = await fact_tool(
                subject="user",
                predicate="name",
                content="Alice",
                entity_id=entity_uuid,
            )

        assert result["error"] == original_msg
        assert result["message"] == original_msg

    async def test_recovery_is_a_non_empty_string(self) -> None:
        """The recovery field must be a non-empty string for all ValueError paths."""
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()
        writing.memory_store_fact = AsyncMock(
            side_effect=ValueError("entity_id UUID('x') does not exist in entities table")
        )

        with self._patch_routing(entity_uuid):
            result = await fact_tool(
                subject="user",
                predicate="name",
                content="Alice",
                entity_id=entity_uuid,
            )

        assert isinstance(result["recovery"], str)
        assert len(result["recovery"]) > 0

    async def test_successful_call_not_affected_by_error_handling(self) -> None:
        """When memory_store_fact succeeds, it returns the normal success dict."""
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()
        writing.memory_store_fact = AsyncMock(return_value={"id": "fact-abc"})

        with self._patch_routing(entity_uuid):
            result = await fact_tool(
                subject="user",
                predicate="name",
                content="Alice",
                entity_id=entity_uuid,
            )

        assert result == {"id": "fact-abc"}
        assert "error" not in result
        assert "recovery" not in result

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

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Tests — _infer_recovery_steps() helper
# ---------------------------------------------------------------------------


class TestInferRecoverySteps:
    """Unit tests for _infer_recovery_steps(), covering all 6 ValueError patterns."""

    # --- Scenario 1: Invalid entity_id ---

    def test_invalid_entity_id_recovery(self) -> None:
        """entity_id not found returns an entity-resolve recovery message."""
        exc = ValueError("entity_id UUID('abc') does not exist in entities table")
        recovery = _infer_recovery_steps(exc)
        assert "memory_entity_resolve" in recovery
        assert "entity_id" in recovery or "entity" in recovery.lower()

    def test_invalid_entity_id_mentions_create_option(self) -> None:
        """Recovery for missing entity_id mentions memory_entity_create as fallback."""
        exc = ValueError("entity_id UUID('abc') does not exist in entities table")
        recovery = _infer_recovery_steps(exc)
        assert "memory_entity_create" in recovery

    # --- Scenario 2: Edge predicate missing object_entity_id ---

    def test_edge_predicate_missing_object_entity_id_recovery(self) -> None:
        """Edge predicate violation returns recovery mentioning object_entity_id."""
        exc = ValueError(
            "Predicate 'parent_of' is registered as an edge predicate "
            "(is_edge=true) and requires object_entity_id to be set. "
            "Call memory_entity_resolve(identifier=<target_name>) to resolve "
            "the target entity, then retry with object_entity_id."
        )
        recovery = _infer_recovery_steps(exc)
        assert "object_entity_id" in recovery
        assert "memory_entity_resolve" in recovery

    def test_edge_predicate_recovery_mentions_target_entity(self) -> None:
        """Edge predicate recovery explains the need to resolve a target entity."""
        exc = ValueError(
            "Predicate 'knows' is registered as an edge predicate "
            "(is_edge=true) and requires object_entity_id to be set."
        )
        recovery = _infer_recovery_steps(exc)
        assert "target" in recovery.lower() or "entity" in recovery.lower()

    # --- Scenario 3: Temporal predicate missing valid_at ---

    def test_temporal_predicate_missing_valid_at_recovery(self) -> None:
        """Temporal predicate violation returns recovery mentioning valid_at."""
        exc = ValueError(
            "Predicate 'interaction' is registered as a temporal predicate "
            "(is_temporal=true) and requires valid_at to be set. "
            "Omitting valid_at would cause supersession to destroy previous records."
        )
        recovery = _infer_recovery_steps(exc)
        assert "valid_at" in recovery

    def test_temporal_predicate_recovery_mentions_iso8601(self) -> None:
        """Recovery for temporal predicate includes ISO-8601 format guidance."""
        exc = ValueError(
            "Predicate 'meal_breakfast' is registered as a temporal predicate "
            "(is_temporal=true) and requires valid_at to be set."
        )
        recovery = _infer_recovery_steps(exc)
        assert "ISO-8601" in recovery or "iso" in recovery.lower()

    # --- Scenario 4: Self-referencing edge ---

    def test_self_referencing_edge_recovery(self) -> None:
        """Self-referencing edge returns recovery explaining entities must differ."""
        exc = ValueError(
            "Self-referencing edges are not allowed: entity_id and object_entity_id must differ"
        )
        recovery = _infer_recovery_steps(exc)
        assert "different" in recovery.lower() or "differ" in recovery.lower()
        assert "object_entity_id" in recovery

    def test_self_referencing_edge_recovery_mentions_resolve(self) -> None:
        """Self-referencing edge recovery suggests resolving the correct target."""
        exc = ValueError(
            "Self-referencing edges are not allowed: entity_id and object_entity_id must differ"
        )
        recovery = _infer_recovery_steps(exc)
        assert "memory_entity_resolve" in recovery

    # --- Scenario 5: UUID in content without object_entity_id ---

    def test_embedded_uuid_in_content_recovery(self) -> None:
        """UUID in content returns recovery explaining to use object_entity_id."""
        exc = ValueError(
            "content contains an embedded UUID (550e8400-e29b-41d4-a716-446655440000) but "
            "object_entity_id is not set. If this fact describes a relationship between "
            "entities, pass the target entity's UUID as object_entity_id instead of "
            "embedding it in content."
        )
        recovery = _infer_recovery_steps(exc)
        assert "object_entity_id" in recovery
        assert "content" in recovery.lower() or "embed" in recovery.lower()

    def test_embedded_uuid_recovery_mentions_resolve(self) -> None:
        """UUID in content recovery mentions memory_entity_resolve."""
        exc = ValueError(
            "content contains an embedded UUID (abc-123) but object_entity_id is not set."
        )
        recovery = _infer_recovery_steps(exc)
        assert "memory_entity_resolve" in recovery

    # --- Scenario 6: Invalid permanence ---

    def test_invalid_permanence_recovery(self) -> None:
        """Invalid permanence returns recovery listing valid permanence values."""
        exc = ValueError(
            "Invalid permanence: 'forever'. Must be one of "
            "['ephemeral', 'permanent', 'stable', 'standard', 'volatile']"
        )
        recovery = _infer_recovery_steps(exc)
        assert "permanent" in recovery
        assert "stable" in recovery
        assert "standard" in recovery
        assert "volatile" in recovery
        assert "ephemeral" in recovery

    # --- Generic fallback ---

    def test_unknown_error_returns_generic_recovery(self) -> None:
        """An unrecognized ValueError message returns the generic fallback recovery."""
        exc = ValueError("Something completely unexpected happened in the database")
        recovery = _infer_recovery_steps(exc)
        assert recovery  # non-empty
        assert "memory_store_fact" in recovery or "memory_predicate_list" in recovery

    # --- Recovery strings are non-empty ---

    @pytest.mark.parametrize(
        "error_msg",
        [
            "entity_id UUID('abc') does not exist in entities table",
            (
                "Predicate 'parent_of' is registered as an edge predicate "
                "(is_edge=true) and requires object_entity_id to be set."
            ),
            (
                "Predicate 'interaction' is registered as a temporal predicate "
                "(is_temporal=true) and requires valid_at to be set."
            ),
            "Self-referencing edges are not allowed: entity_id and object_entity_id must differ",
            "content contains an embedded UUID (abc-123) but object_entity_id is not set.",
            (
                "Invalid permanence: 'forever'. Must be one of "
                "['ephemeral', 'permanent', 'stable', 'standard', 'volatile']"
            ),
        ],
    )
    def test_all_scenarios_return_non_empty_recovery(self, error_msg: str) -> None:
        """Every recognized error pattern must return a non-empty recovery string."""
        exc = ValueError(error_msg)
        recovery = _infer_recovery_steps(exc)
        assert isinstance(recovery, str)
        assert len(recovery) > 0


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
            await mod.register_tools(mcp=mcp, config=None, db=fake_db)

        mod._embedding_engine = MagicMock(name="embedding")
        return mod, registered_tools["memory_store_fact"], fake_db.pool, mock_writing

    def _patch_routing(self, entity_id: str):
        """Helper: patch routing context to provide a valid entity_id."""
        return patch(
            "butlers.modules.memory.get_current_runtime_session_routing_context",
            return_value={"source_entity_id": entity_id},
        )

    # --- Scenario 1: Invalid entity_id (entity does not exist) ---

    async def test_invalid_entity_id_returns_structured_error(self) -> None:
        """ValueError for missing entity returns structured dict, not raw exception.

        WHEN memory_store_fact is called and storage raises ValueError for a
        non-existent entity_id,
        THEN the MCP tool MUST return a structured dict with error, message, recovery.
        """
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()
        writing.memory_store_fact = AsyncMock(
            side_effect=ValueError(
                f"entity_id UUID('{entity_uuid}') does not exist in entities table"
            )
        )

        with self._patch_routing(entity_uuid):
            result = await fact_tool(
                subject="user",
                predicate="name",
                content="Alice",
                entity_id=entity_uuid,
            )

        assert "error" in result
        assert "message" in result
        assert "recovery" in result
        assert "memory_entity_resolve" in result["recovery"]

    # --- Scenario 2: Edge predicate missing object_entity_id ---

    async def test_edge_predicate_missing_object_returns_structured_error(self) -> None:
        """ValueError for edge predicate without object_entity_id returns structured dict.

        WHEN memory_store_fact is called with an edge predicate and no object_entity_id,
        THEN the MCP tool MUST return a structured dict with recovery mentioning
        memory_entity_resolve() and object_entity_id.
        """
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()
        writing.memory_store_fact = AsyncMock(
            side_effect=ValueError(
                "Predicate 'parent_of' is registered as an edge predicate "
                "(is_edge=true) and requires object_entity_id to be set. "
                "Call memory_entity_resolve(identifier=<target_name>) to resolve "
                "the target entity, then retry with object_entity_id."
            )
        )

        with self._patch_routing(entity_uuid):
            result = await fact_tool(
                subject="Alice",
                predicate="parent_of",
                content="Bob",
                entity_id=entity_uuid,
            )

        assert "error" in result
        assert "message" in result
        assert "recovery" in result
        assert "object_entity_id" in result["recovery"]
        assert "memory_entity_resolve" in result["recovery"]

    # --- Scenario 3: Temporal predicate missing valid_at ---

    async def test_temporal_predicate_missing_valid_at_returns_structured_error(self) -> None:
        """ValueError for temporal predicate without valid_at returns structured dict.

        WHEN memory_store_fact is called with a temporal predicate and no valid_at,
        THEN the MCP tool MUST return a structured dict with recovery explaining
        the valid_at ISO-8601 requirement.
        """
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()
        writing.memory_store_fact = AsyncMock(
            side_effect=ValueError(
                "Predicate 'interaction' is registered as a temporal predicate "
                "(is_temporal=true) and requires valid_at to be set. "
                "Omitting valid_at would cause supersession to destroy previous records "
                "for this predicate. Provide an ISO-8601 valid_at timestamp."
            )
        )

        with self._patch_routing(entity_uuid):
            result = await fact_tool(
                subject="Alice",
                predicate="interaction",
                content="had a phone call",
                entity_id=entity_uuid,
            )

        assert "error" in result
        assert "message" in result
        assert "recovery" in result
        assert "valid_at" in result["recovery"]

    # --- Scenario 4: Self-referencing edge ---

    async def test_self_referencing_edge_returns_structured_error(self) -> None:
        """ValueError for self-referencing edge returns structured dict.

        WHEN memory_store_fact is called with entity_id == object_entity_id,
        THEN the MCP tool MUST return a structured dict with recovery explaining
        that entity_id and object_entity_id must be different.
        """
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()
        writing.memory_store_fact = AsyncMock(
            side_effect=ValueError(
                "Self-referencing edges are not allowed: entity_id and object_entity_id must differ"
            )
        )

        with self._patch_routing(entity_uuid):
            result = await fact_tool(
                subject="Alice",
                predicate="knows",
                content="herself",
                entity_id=entity_uuid,
                object_entity_id=entity_uuid,
            )

        assert "error" in result
        assert "message" in result
        assert "recovery" in result
        assert "object_entity_id" in result["recovery"]

    # --- Scenario 5: UUID in content without object_entity_id ---

    async def test_uuid_in_content_returns_structured_error(self) -> None:
        """ValueError for UUID embedded in content returns structured dict.

        WHEN memory_store_fact is called with a UUID in content and no object_entity_id,
        THEN the MCP tool MUST return a structured dict with recovery explaining
        to use object_entity_id instead.
        """
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        embedded_uuid = "660e8400-e29b-41d4-a716-446655440001"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()
        writing.memory_store_fact = AsyncMock(
            side_effect=ValueError(
                f"content contains an embedded UUID ({embedded_uuid}) but "
                "object_entity_id is not set. If this fact describes a "
                "relationship between entities, pass the target entity's UUID "
                "as object_entity_id instead of embedding it in content."
            )
        )

        with self._patch_routing(entity_uuid):
            result = await fact_tool(
                subject="Alice",
                predicate="knows",
                content=f"knows entity {embedded_uuid}",
                entity_id=entity_uuid,
            )

        assert "error" in result
        assert "message" in result
        assert "recovery" in result
        assert "object_entity_id" in result["recovery"]
        assert "memory_entity_resolve" in result["recovery"]

    # --- Scenario 6: Invalid permanence ---

    async def test_invalid_permanence_returns_structured_error(self) -> None:
        """ValueError for invalid permanence returns structured dict.

        WHEN memory_store_fact is called with an invalid permanence value,
        THEN the MCP tool MUST return a structured dict with recovery listing
        the valid permanence values.
        """
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()
        writing.memory_store_fact = AsyncMock(
            side_effect=ValueError(
                "Invalid permanence: 'forever'. Must be one of "
                "['ephemeral', 'permanent', 'stable', 'standard', 'volatile']"
            )
        )

        with self._patch_routing(entity_uuid):
            result = await fact_tool(
                subject="user",
                predicate="name",
                content="Alice",
                entity_id=entity_uuid,
                permanence="forever",  # type: ignore[arg-type]
            )

        assert "error" in result
        assert "message" in result
        assert "recovery" in result
        # Must list at least some of the valid permanence values
        assert "permanent" in result["recovery"] or "stable" in result["recovery"]

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

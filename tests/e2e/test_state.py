"""E2E tests for KV state store — validates persistence, fidelity, and isolation.

Tests cover:
1. Cross-session persistence: write in one MCP client session, read in another
2. JSONB type fidelity: round-trip all JSON types through JSONB
3. State isolation between butlers: same key on different butlers has independent values
4. Prefix listing: list keys with prefix filter
5. Overwrite behavior: set key twice, get returns latest value
6. Delete behavior: set, delete, get returns null

All tests use MCP client tool calls (state_get, state_set, state_list, state_delete)
against live butler daemons.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastmcp import Client as MCPClient

if TYPE_CHECKING:
    from tests.e2e.conftest import ButlerEcosystem, CostTracker


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Scenario 1: Cross-Session Persistence
# ---------------------------------------------------------------------------


async def test_state_persists_across_sessions(
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
) -> None:
    """State written in one MCP client session should be readable in the next.

    Validates that the state store serves as persistent memory across
    ephemeral MCP client connections.
    """
    health = butler_ecosystem.butlers["health"]
    port = health.config.port
    url = f"http://localhost:{port}/sse"

    # Session 1: Write state
    async with MCPClient(url) as client:
        result = await client.call_tool(
            "state_set",
            {"key": "e2e-test-key", "value": {"weight_goal": 75, "unit": "kg"}},
        )
        assert result["status"] == "ok", "state_set should return ok status"

    # Session 2: Read state back (new client = new session)
    async with MCPClient(url) as client:
        result = await client.call_tool("state_get", {"key": "e2e-test-key"})
        assert result["key"] == "e2e-test-key", "key should match"
        value = result["value"]
        assert value is not None, "value should not be null"
        assert value["weight_goal"] == 75, "weight_goal should match"
        assert value["unit"] == "kg", "unit should match"

    # No LLM calls in this test
    cost_tracker.record(input_tokens=0, output_tokens=0)


# ---------------------------------------------------------------------------
# Scenario 2: JSONB Type Fidelity
# ---------------------------------------------------------------------------


async def test_jsonb_type_fidelity(
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
) -> None:
    """JSONB round-trip should preserve all JSON types exactly.

    Tests string, int, float, bool, null, empty object, empty array,
    nested structures, and unicode. Validates that JSONB storage
    preserves types (e.g., int 42 stays int, not float 42.0).
    """
    health = butler_ecosystem.butlers["health"]
    port = health.config.port
    url = f"http://localhost:{port}/sse"

    # Test all JSON types
    test_values = {
        "string": "hello",
        "integer": 42,
        "float": 3.14,
        "boolean_true": True,
        "boolean_false": False,
        "null_value": None,
        "empty_object": {},
        "empty_array": [],
        "nested": {"a": {"b": [1, 2, 3]}, "c": {"d": "deep"}},
        "unicode": "你好世界",
        "special_chars": 'line1\nline2\ttab "quote" \\backslash',
    }

    async with MCPClient(url) as client:
        for type_name, expected_value in test_values.items():
            key = f"e2e-fidelity-{type_name}"

            # Set value
            await client.call_tool("state_set", {"key": key, "value": expected_value})

            # Get value and verify round-trip
            result = await client.call_tool("state_get", {"key": key})
            retrieved_value = result["value"]

            assert retrieved_value == expected_value, (
                f"JSONB fidelity failed for {type_name}: "
                f"expected {expected_value!r} (type {type(expected_value).__name__}), "
                f"got {retrieved_value!r} (type {type(retrieved_value).__name__})"
            )

            # Extra type validation for strict type preservation
            if type_name == "integer":
                assert isinstance(retrieved_value, int), "Integer should stay int, not float"
                assert not isinstance(retrieved_value, bool), "Integer should not be bool"
            elif type_name == "float":
                assert isinstance(retrieved_value, float), "Float should stay float"
            elif type_name.startswith("boolean_"):
                assert isinstance(retrieved_value, bool), "Boolean should stay bool"
            elif type_name == "null_value":
                assert retrieved_value is None, "Null should stay None"

    # No LLM calls in this test
    cost_tracker.record(input_tokens=0, output_tokens=0)


# ---------------------------------------------------------------------------
# Scenario 3: State Isolation Between Butlers
# ---------------------------------------------------------------------------


async def test_state_isolation_between_butlers(
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
) -> None:
    """Same key on different butlers should have independent values.

    Validates that each butler's state store is in its own database
    and keys do not conflict across butlers.
    """
    health = butler_ecosystem.butlers["health"]
    relationship = butler_ecosystem.butlers["relationship"]

    health_port = health.config.port
    relationship_port = relationship.config.port

    health_url = f"http://localhost:{health_port}/sse"
    relationship_url = f"http://localhost:{relationship_port}/sse"

    # Set same key on both butlers with different values
    async with MCPClient(health_url) as client:
        await client.call_tool(
            "state_set",
            {"key": "shared-key-name", "value": {"source": "health", "data": 42}},
        )

    async with MCPClient(relationship_url) as client:
        await client.call_tool(
            "state_set",
            {"key": "shared-key-name", "value": {"source": "relationship", "data": 99}},
        )

    # Verify each butler's value is independent
    async with MCPClient(health_url) as client:
        result = await client.call_tool("state_get", {"key": "shared-key-name"})
        value = result["value"]
        assert value["source"] == "health", "Health butler should have its own value"
        assert value["data"] == 42, "Health butler data should match"

    async with MCPClient(relationship_url) as client:
        result = await client.call_tool("state_get", {"key": "shared-key-name"})
        value = result["value"]
        assert value["source"] == "relationship", "Relationship butler should have its own value"
        assert value["data"] == 99, "Relationship butler data should match"

    # No LLM calls in this test
    cost_tracker.record(input_tokens=0, output_tokens=0)


# ---------------------------------------------------------------------------
# Scenario 4: Prefix Listing
# ---------------------------------------------------------------------------


async def test_prefix_listing(
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
) -> None:
    """Prefix filtering should only return matching keys.

    Tests that state_list with a prefix parameter only returns keys
    starting with that prefix, supporting namespace-style organization.
    """
    health = butler_ecosystem.butlers["health"]
    port = health.config.port
    url = f"http://localhost:{port}/sse"

    async with MCPClient(url) as client:
        # Set keys with different prefixes
        await client.call_tool("state_set", {"key": "health:prefs", "value": {"theme": "dark"}})
        await client.call_tool("state_set", {"key": "health:goals", "value": {"weight": 75}})
        await client.call_tool("state_set", {"key": "health:history", "value": {"entries": 10}})
        await client.call_tool("state_set", {"key": "general:prefs", "value": {"lang": "en"}})
        await client.call_tool("state_set", {"key": "other:data", "value": {"foo": "bar"}})

        # List with "health:" prefix
        result = await client.call_tool("state_list", {"prefix": "health:"})
        keys = result["keys"]
        assert isinstance(keys, list), "keys should be a list"
        assert len(keys) == 3, "Should return exactly 3 keys with health: prefix"
        assert "health:prefs" in keys, "health:prefs should be in list"
        assert "health:goals" in keys, "health:goals should be in list"
        assert "health:history" in keys, "health:history should be in list"
        assert "general:prefs" not in keys, "general:prefs should not be in list"
        assert "other:data" not in keys, "other:data should not be in list"

        # List with "general:" prefix
        result = await client.call_tool("state_list", {"prefix": "general:"})
        keys = result["keys"]
        assert len(keys) == 1, "Should return exactly 1 key with general: prefix"
        assert "general:prefs" in keys, "general:prefs should be in list"

        # List with nonexistent prefix
        result = await client.call_tool("state_list", {"prefix": "nonexistent:"})
        keys = result["keys"]
        assert len(keys) == 0, "Should return empty list for nonexistent prefix"

        # List without prefix (all keys)
        result = await client.call_tool("state_list", {})
        keys = result["keys"]
        # Should return at least the keys we just created
        # (may include keys from other tests if running in parallel)
        assert "health:prefs" in keys, "Should include health:prefs"
        assert "general:prefs" in keys, "Should include general:prefs"

    # No LLM calls in this test
    cost_tracker.record(input_tokens=0, output_tokens=0)


# ---------------------------------------------------------------------------
# Scenario 5: Overwrite Behavior
# ---------------------------------------------------------------------------


async def test_overwrite_behavior(
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
) -> None:
    """Setting a key twice should overwrite the value.

    Validates that state_set upserts correctly and the latest value wins.
    """
    health = butler_ecosystem.butlers["health"]
    port = health.config.port
    url = f"http://localhost:{port}/sse"

    async with MCPClient(url) as client:
        # Set initial value
        await client.call_tool(
            "state_set", {"key": "e2e-overwrite", "value": {"version": 1, "data": "first"}}
        )

        # Verify initial value
        result = await client.call_tool("state_get", {"key": "e2e-overwrite"})
        value = result["value"]
        assert value["version"] == 1, "Initial version should be 1"
        assert value["data"] == "first", "Initial data should be 'first'"

        # Overwrite with new value
        await client.call_tool(
            "state_set", {"key": "e2e-overwrite", "value": {"version": 2, "data": "second"}}
        )

        # Verify overwritten value
        result = await client.call_tool("state_get", {"key": "e2e-overwrite"})
        value = result["value"]
        assert value["version"] == 2, "Overwritten version should be 2"
        assert value["data"] == "second", "Overwritten data should be 'second'"

    # No LLM calls in this test
    cost_tracker.record(input_tokens=0, output_tokens=0)


# ---------------------------------------------------------------------------
# Scenario 6: Delete Behavior
# ---------------------------------------------------------------------------


async def test_delete_behavior(
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
) -> None:
    """Deleting a key should make it return null on get.

    Validates that state_delete removes keys and subsequent get
    returns null (not an error).
    """
    health = butler_ecosystem.butlers["health"]
    port = health.config.port
    url = f"http://localhost:{port}/sse"

    async with MCPClient(url) as client:
        # Set a value
        await client.call_tool("state_set", {"key": "e2e-delete", "value": {"temp": "data"}})

        # Verify it exists
        result = await client.call_tool("state_get", {"key": "e2e-delete"})
        assert result["value"] is not None, "Value should exist before delete"

        # Delete the key
        await client.call_tool("state_delete", {"key": "e2e-delete"})

        # Verify it returns null after delete
        result = await client.call_tool("state_get", {"key": "e2e-delete"})
        assert result["value"] is None, "Value should be null after delete"

        # Delete a nonexistent key (should be no-op, not error)
        await client.call_tool("state_delete", {"key": "nonexistent-key"})

        # Verify nonexistent key still returns null
        result = await client.call_tool("state_get", {"key": "nonexistent-key"})
        assert result["value"] is None, "Nonexistent key should return null"

    # No LLM calls in this test
    cost_tracker.record(input_tokens=0, output_tokens=0)

"""Tests for the Memory module."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP as RuntimeFastMCP
from pydantic import BaseModel

from butlers.modules.base import Module
from butlers.modules.memory import MemoryModule, MemoryModuleConfig

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Module ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABC:
    """Verify MemoryModule satisfies the Module abstract base class."""

    def test_is_subclass_of_module(self):
        assert issubclass(MemoryModule, Module)

    def test_instantiates(self):
        mod = MemoryModule()
        assert isinstance(mod, Module)

    def test_name(self):
        mod = MemoryModule()
        assert mod.name == "memory"

    def test_config_schema(self):
        mod = MemoryModule()
        assert mod.config_schema is MemoryModuleConfig
        assert issubclass(mod.config_schema, BaseModel)

    def test_dependencies_empty(self):
        mod = MemoryModule()
        assert mod.dependencies == []

    def test_migration_revisions_memory_chain(self):
        mod = MemoryModule()
        assert mod.migration_revisions() == "memory"


# ---------------------------------------------------------------------------
# Lifecycle: on_startup / on_shutdown
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Verify startup and shutdown lifecycle hooks."""

    async def test_on_startup_stores_db(self):
        mod = MemoryModule()
        fake_db = MagicMock()
        await mod.on_startup(config=None, db=fake_db)
        assert mod._db is fake_db

    async def test_on_shutdown_clears_state(self):
        mod = MemoryModule()
        fake_db = MagicMock()
        await mod.on_startup(config=None, db=fake_db)
        mod._embedding_engine = MagicMock()  # simulate lazy load
        await mod.on_shutdown()
        assert mod._db is None
        assert mod._embedding_engine is None

    def test_get_pool_raises_when_uninitialised(self):
        mod = MemoryModule()
        with pytest.raises(RuntimeError, match="not initialised"):
            mod._get_pool()

    def test_get_pool_returns_db_pool(self):
        mod = MemoryModule()
        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        mod._db = fake_db
        assert mod._get_pool() is fake_db.pool


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

EXPECTED_TOOL_NAMES = {
    "memory_store_episode",
    "memory_store_fact",
    "memory_store_rule",
    "memory_search",
    "memory_recall",
    "memory_get",
    "memory_confirm",
    "memory_mark_helpful",
    "memory_mark_harmful",
    "memory_forget",
    "memory_stats",
    "memory_context",
    "memory_run_consolidation",
    "memory_run_episode_cleanup",
}


class TestRegisterTools:
    """Verify that register_tools creates the expected MCP tools."""

    async def _register_and_capture(self) -> dict[str, Any]:
        """Helper: register tools with a mock MCP and capture them."""
        mod = MemoryModule()
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
                "butlers.modules.memory": MagicMock(),
                "butlers.modules.memory.consolidation": MagicMock(),
                "butlers.modules.memory.tools": MagicMock(),
                "butlers.modules.memory.tools.writing": MagicMock(),
                "butlers.modules.memory.tools.reading": MagicMock(),
                "butlers.modules.memory.tools.feedback": MagicMock(),
                "butlers.modules.memory.tools.management": MagicMock(),
                "butlers.modules.memory.tools.context": MagicMock(),
            },
        ):
            await mod.register_tools(mcp=mcp, config=None, db=MagicMock())

        return registered_tools

    async def test_registers_fourteen_tools(self):
        registered = await self._register_and_capture()
        assert len(registered) == 14

    async def test_tool_names_match(self):
        registered = await self._register_and_capture()
        assert set(registered.keys()) == EXPECTED_TOOL_NAMES

    async def test_all_tools_are_async(self):
        registered = await self._register_and_capture()
        for tool_name, tool_fn in registered.items():
            assert asyncio.iscoroutinefunction(tool_fn), f"{tool_name} should be async"

    async def test_mcp_tool_called_fourteen_times(self):
        mod = MemoryModule()
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn

        with patch.dict(
            "sys.modules",
            {
                "butlers.modules.memory": MagicMock(),
                "butlers.modules.memory.consolidation": MagicMock(),
                "butlers.modules.memory.tools": MagicMock(),
                "butlers.modules.memory.tools.writing": MagicMock(),
                "butlers.modules.memory.tools.reading": MagicMock(),
                "butlers.modules.memory.tools.feedback": MagicMock(),
                "butlers.modules.memory.tools.management": MagicMock(),
                "butlers.modules.memory.tools.context": MagicMock(),
            },
        ):
            await mod.register_tools(mcp=mcp, config=None, db=MagicMock())

        assert mcp.tool.call_count == 14

    async def test_memory_store_fact_tool_description_and_schema_contract(self):
        """memory_store_fact metadata should document strict fields and tags shape."""
        mod = MemoryModule()
        runtime_mcp = RuntimeFastMCP("test-memory")
        fake_db = MagicMock()
        fake_db.pool = MagicMock()

        await mod.register_tools(mcp=runtime_mcp, config=None, db=fake_db)

        tools = await runtime_mcp.get_tools()
        fact_tool = tools["memory_store_fact"].model_dump()

        description = fact_tool["description"] or ""
        assert "required fields" in description.lower()
        assert '"subject": "user"' in description
        assert '"tags": [' in description
        assert "JSON array of strings" in description

        params = fact_tool["parameters"]
        permanence_prop = params["properties"]["permanence"]
        assert set(permanence_prop["enum"]) == {
            "permanent",
            "stable",
            "standard",
            "volatile",
            "ephemeral",
        }

        tags_prop = params["properties"]["tags"]
        tags_desc = tags_prop["description"]
        assert "JSON array of strings" in tags_desc
        assert tags_prop["anyOf"][0]["type"] == "array"


# ---------------------------------------------------------------------------
# Tool delegation — verify closures call underlying impls correctly
# ---------------------------------------------------------------------------


class TestToolDelegation:
    """Verify that MCP tool closures delegate to the correct functions."""

    async def _setup_and_register(self):
        """Register tools with mocked implementations and return them."""
        mod = MemoryModule()

        fake_db = MagicMock()
        fake_db.pool = MagicMock(name="fake_pool")

        # Create sub-module mocks with AsyncMock defaults for all functions
        mock_writing = MagicMock()
        mock_reading = MagicMock()
        mock_feedback = MagicMock()
        mock_management = MagicMock()
        mock_context = MagicMock()

        # Wire sub-mocks as attributes of the parent so that
        # ``from butlers.modules.memory.tools import writing`` resolves correctly.
        parent_mock = MagicMock()
        parent_mock.writing = mock_writing
        parent_mock.reading = mock_reading
        parent_mock.feedback = mock_feedback
        parent_mock.management = mock_management
        parent_mock.context = mock_context

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
                "butlers.modules.memory.tools.reading": mock_reading,
                "butlers.modules.memory.tools.feedback": mock_feedback,
                "butlers.modules.memory.tools.management": mock_management,
                "butlers.modules.memory.tools.context": mock_context,
            },
        ):
            await mod.register_tools(mcp=mcp, config=None, db=fake_db)

        return (
            mod,
            registered_tools,
            fake_db.pool,
            mock_writing,
            mock_reading,
            mock_feedback,
            mock_management,
            mock_context,
        )

    async def test_memory_store_episode_delegates(self):
        mod, tools, pool, writing, *_ = await self._setup_and_register()
        writing.memory_store_episode = AsyncMock(return_value={"id": "abc"})
        await tools["memory_store_episode"](content="test", butler="memory")
        writing.memory_store_episode.assert_called_once_with(
            pool, "test", "memory", session_id=None, importance=5.0
        )

    async def test_memory_store_fact_delegates(self):
        mod, tools, pool, writing, *_ = await self._setup_and_register()
        mod._embedding_engine = MagicMock(name="embedding")
        writing.memory_store_fact = AsyncMock(return_value={"id": "abc"})
        await tools["memory_store_fact"](subject="user", predicate="likes", content="coffee")
        writing.memory_store_fact.assert_called_once_with(
            pool,
            mod._embedding_engine,
            "user",
            "likes",
            "coffee",
            importance=5.0,
            permanence="standard",
            scope="global",
            tags=None,
        )

    async def test_memory_context_delegates(self):
        mod, tools, pool, _, _, _, _, context_mod = await self._setup_and_register()
        mod._embedding_engine = MagicMock(name="embedding")
        context_mod.memory_context = AsyncMock(return_value="# Memory Context\n")
        await tools["memory_context"](trigger_prompt="test prompt", butler="memory")
        context_mod.memory_context.assert_called_once_with(
            pool,
            mod._embedding_engine,
            "test prompt",
            "memory",
            token_budget=3000,
        )

    async def test_memory_search_delegates(self):
        mod, tools, pool, _, reading, *_ = await self._setup_and_register()
        mod._embedding_engine = MagicMock(name="embedding")
        reading.memory_search = AsyncMock(return_value=[])
        await tools["memory_search"](query="test query")
        reading.memory_search.assert_called_once_with(
            pool,
            mod._embedding_engine,
            "test query",
            types=None,
            scope=None,
            mode="hybrid",
            limit=10,
            min_confidence=0.2,
        )

    async def test_memory_confirm_delegates(self):
        mod, tools, pool, _, _, feedback, *_ = await self._setup_and_register()
        feedback.memory_confirm = AsyncMock(return_value={"confirmed": True})
        await tools["memory_confirm"](memory_type="fact", memory_id="abc-123")
        feedback.memory_confirm.assert_called_once_with(pool, "fact", "abc-123")

    async def test_memory_forget_delegates(self):
        mod, tools, pool, _, _, _, management, _ = await self._setup_and_register()
        management.memory_forget = AsyncMock(return_value={"forgotten": True})
        await tools["memory_forget"](memory_type="fact", memory_id="abc-123")
        management.memory_forget.assert_called_once_with(pool, "fact", "abc-123")

    async def test_memory_stats_delegates(self):
        mod, tools, pool, _, _, _, management, _ = await self._setup_and_register()
        management.memory_stats = AsyncMock(return_value={})
        await tools["memory_stats"]()
        management.memory_stats.assert_called_once_with(pool, scope=None)


# ---------------------------------------------------------------------------
# Registry discovery
# ---------------------------------------------------------------------------


class TestRegistryDiscovery:
    """Verify MemoryModule is found by default_registry()."""

    def test_memory_in_default_registry(self):
        from butlers.modules.registry import default_registry

        registry = default_registry()
        assert "memory" in registry.available_modules

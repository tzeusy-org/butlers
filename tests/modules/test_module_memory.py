"""Tests for the Memory module."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP as RuntimeFastMCP
from pydantic import BaseModel

from butlers.modules.base import Module
from butlers.modules.memory import MemoryModule, MemoryModuleConfig
from tests.modules.memory._test_helpers import make_embedding_engine_mock

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
    "memory_entity_create",
    "memory_entity_get",
    "memory_entity_update",
    "memory_entity_neighbors",
    "memory_entity_resolve",
    "memory_entity_merge",
    "memory_predicate_list",
    "memory_predicate_search",
    "memory_catalog_search",
    "memory_set_preference",
    "memory_get_preferences",
    # admin: re-embedding migration tools (added in bu-jt6ey / bu-a6zpb)
    "memory_reembed",
    "memory_reembed_pending_count",
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
                "butlers.modules.memory.tools.entities": MagicMock(),
                "butlers.modules.memory.tools.preferences": MagicMock(),
            },
        ):
            await mod.register_tools(
                mcp=mcp, config=None, db=MagicMock(), butler_name="test-butler"
            )

        return registered_tools

    async def _register_with_mock_entities(
        self, entities: MagicMock
    ) -> tuple[dict[str, Any], MagicMock]:
        """Register memory tools with a mocked entities module."""
        mod = MemoryModule()
        mcp = MagicMock()
        fake_db = MagicMock()
        fake_db.pool = MagicMock(name="fake_pool")
        registered_tools: dict[str, Any] = {}
        tools = MagicMock()
        tools.context = MagicMock()
        tools.entities = entities
        tools.feedback = MagicMock()
        tools.management = MagicMock()
        tools.preferences = MagicMock()
        tools.reading = MagicMock()
        tools.writing = MagicMock()

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool

        with patch.dict(
            "sys.modules",
            {
                "butlers.modules.memory.consolidation": MagicMock(),
                "butlers.modules.memory.reembedding": MagicMock(),
                "butlers.modules.memory.tools": tools,
                "butlers.modules.memory.tools.writing": tools.writing,
                "butlers.modules.memory.tools.reading": tools.reading,
                "butlers.modules.memory.tools.feedback": tools.feedback,
                "butlers.modules.memory.tools.management": tools.management,
                "butlers.modules.memory.tools.context": tools.context,
                "butlers.modules.memory.tools.entities": entities,
                "butlers.modules.memory.tools.preferences": tools.preferences,
            },
        ):
            await mod.register_tools(mcp=mcp, config=None, db=fake_db, butler_name="education")

        return registered_tools, fake_db

    async def test_tool_names_match(self):
        registered = await self._register_and_capture()
        # Exact name set subsumes the count contract.
        assert set(registered.keys()) == EXPECTED_TOOL_NAMES

    async def test_all_tools_are_async(self):
        registered = await self._register_and_capture()
        for tool_name, tool_fn in registered.items():
            assert asyncio.iscoroutinefunction(tool_fn), f"{tool_name} should be async"

    async def test_memory_entity_create_duplicate_returns_existing_entity_id(self):
        entities = MagicMock()
        entities.entity_create = AsyncMock(
            side_effect=ValueError(
                "Entity with canonical_name='Existing Learner' and entity_type='person' "
                "already exists."
            )
        )
        entities.entity_find_by_canonical = AsyncMock(
            return_value={"id": "550e8400-e29b-41d4-a716-446655440000"}
        )
        registered_tools, fake_db = await self._register_with_mock_entities(entities)

        result = await registered_tools["memory_entity_create"](
            canonical_name="Existing Learner",
            entity_type="person",
        )

        assert result == {"entity_id": "550e8400-e29b-41d4-a716-446655440000"}
        entities.entity_find_by_canonical.assert_awaited_once_with(
            fake_db.pool,
            "Existing Learner",
            "person",
        )

    async def test_memory_entity_create_validation_errors_still_raise(self):
        entities = MagicMock()
        entities.entity_create = AsyncMock(side_effect=ValueError("Invalid entity_type 'ghost'"))
        entities.entity_find_by_canonical = AsyncMock()
        registered_tools, _fake_db = await self._register_with_mock_entities(entities)

        with pytest.raises(ValueError, match="Invalid entity_type"):
            await registered_tools["memory_entity_create"](
                canonical_name="Ghost",
                entity_type="ghost",
            )

        entities.entity_find_by_canonical.assert_not_awaited()

    async def test_memory_store_fact_tool_description_and_schema_contract(self):
        """memory_store_fact metadata should document strict fields and tags shape."""
        mod = MemoryModule()
        runtime_mcp = RuntimeFastMCP("test-memory")
        fake_db = MagicMock()
        fake_db.pool = MagicMock()

        await mod.register_tools(
            mcp=runtime_mcp, config=None, db=fake_db, butler_name="test-butler"
        )

        get_tools = getattr(runtime_mcp, "get_tools", None)
        if callable(get_tools):
            tools = await get_tools()
            fact_tool = tools["memory_store_fact"].model_dump()
        else:
            fact_tool = (await runtime_mcp.get_tool("memory_store_fact")).model_dump()

        description = fact_tool["description"] or ""
        assert "required fields" in description.lower()
        assert '"subject": "Owner"' in description
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
        assert "do not pass a single string value" in tags_desc.lower()
        assert tags_prop["anyOf"][0]["type"] == "array"

    async def test_memory_search_tool_description_and_schema_contract(self):
        """memory_search metadata should document strict type list and mode enum."""
        mod = MemoryModule()
        runtime_mcp = RuntimeFastMCP("test-memory")
        fake_db = MagicMock()
        fake_db.pool = MagicMock()

        await mod.register_tools(
            mcp=runtime_mcp, config=None, db=fake_db, butler_name="test-butler"
        )

        get_tools = getattr(runtime_mcp, "get_tools", None)
        if callable(get_tools):
            tools = await get_tools()
            search_tool = tools["memory_search"].model_dump()
        else:
            search_tool = (await runtime_mcp.get_tool("memory_search")).model_dump()

        description = search_tool["description"] or ""
        assert "types" in description.lower()
        assert 'types="facts"' in description
        assert "invalid" in description.lower()
        assert '"types": ["fact"]' in description

        params = search_tool["parameters"]
        mode_prop = params["properties"]["mode"]
        assert set(mode_prop["enum"]) == {"hybrid", "semantic", "keyword"}

        types_prop = params["properties"]["types"]
        types_desc = types_prop["description"]
        assert "Do not pass a single string" in types_desc
        array_variant = next(
            variant for variant in types_prop["anyOf"] if variant.get("type") == "array"
        )
        assert set(array_variant["items"]["enum"]) == {"episode", "fact", "rule"}


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
        mock_entities = MagicMock()

        # Wire sub-mocks as attributes of the parent so that
        # ``from butlers.modules.memory.tools import writing`` resolves correctly.
        parent_mock = MagicMock()
        parent_mock.writing = mock_writing
        parent_mock.reading = mock_reading
        parent_mock.feedback = mock_feedback
        parent_mock.management = mock_management
        parent_mock.context = mock_context
        parent_mock.entities = mock_entities

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
                "butlers.modules.memory.tools.entities": mock_entities,
            },
        ):
            await mod.register_tools(mcp=mcp, config=None, db=fake_db, butler_name="test-butler")

        return (
            mod,
            registered_tools,
            fake_db.pool,
            mock_writing,
            mock_reading,
            mock_feedback,
            mock_management,
            mock_context,
            mock_entities,
        )

    async def test_memory_store_fact_delegates(self):
        mod, tools, pool, writing, *_ = await self._setup_and_register()
        mod._embedding_engine = make_embedding_engine_mock(mod._config.embedding_model)
        writing.memory_store_fact = AsyncMock(return_value={"id": "abc"})
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"
        await tools["memory_store_fact"](
            subject="user", predicate="likes", content="coffee", entity_id=entity_uuid
        )
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
            entity_id=entity_uuid,
            object_entity_id=None,
            valid_at=None,
            idempotency_key=None,
            request_context=None,
            retention_class="operational",
            sensitivity="normal",
            enable_shared_catalog=False,
            source_schema=None,
        )

    @pytest.mark.parametrize(
        ("valid_at", "retention_class"),
        [
            ("2026-03-15T10:00:00Z", "long_term"),
            ("2025-01-01T00:00:00Z", "ephemeral"),
        ],
    )
    async def test_memory_store_fact_forwards_valid_at_and_retention_class(
        self, valid_at, retention_class
    ):
        """Non-default valid_at and a custom retention_class must reach writing verbatim.

        Guards against a tool-layer regression that hardcodes/ignores these
        caller-supplied values (e.g. always sending valid_at=None or
        retention_class='operational').
        """
        mod, tools, pool, writing, *_ = await self._setup_and_register()
        mod._embedding_engine = make_embedding_engine_mock(mod._config.embedding_model)
        writing.memory_store_fact = AsyncMock(return_value={"id": "abc"})
        entity_uuid = "550e8400-e29b-41d4-a716-446655440000"

        await tools["memory_store_fact"](
            subject="user",
            predicate="visited",
            content="Paris",
            entity_id=entity_uuid,
            valid_at=valid_at,
            retention_class=retention_class,
        )

        _, kwargs = writing.memory_store_fact.call_args
        assert kwargs["valid_at"] == valid_at
        assert kwargs["retention_class"] == retention_class

    async def test_memory_search_delegates(self):
        mod, tools, pool, _, reading, *_ = await self._setup_and_register()
        mod._embedding_engine = make_embedding_engine_mock(mod._config.embedding_model)
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
            filters=None,
        )


# ---------------------------------------------------------------------------
# Sender entity_id fallback in memory_store_fact
# ---------------------------------------------------------------------------


class TestMemoryStoreFactSenderEntityIdFallback:
    """Verify memory_store_fact uses sender entity_id from routing context as default."""

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
        mock_writing.memory_store_fact = AsyncMock(return_value={"id": "xyz"})
        return mod, registered_tools["memory_store_fact"], fake_db.pool, mock_writing

    async def test_no_entity_id_and_no_routing_ctx_rejects(self):
        """When no routing context exists and no entity_id, the call is rejected."""
        from unittest.mock import patch as _patch

        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()

        with _patch(
            "butlers.modules.memory.get_current_runtime_session_routing_context",
            return_value=None,
        ):
            result = await fact_tool(subject="user", predicate="likes", content="coffee")

        assert result["error"] == "entity_id is required"
        assert "memory_entity_resolve" in result["message"]
        assert result["subject"] == "user"
        assert result["predicate"] == "likes"
        writing.memory_store_fact.assert_not_called()

    async def test_no_entity_id_with_routing_ctx_uses_sender_entity(self):
        """When routing context has source_entity_id, it is used as entity_id fallback."""
        from unittest.mock import patch as _patch

        sender_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()

        with _patch(
            "butlers.modules.memory.get_current_runtime_session_routing_context",
            return_value={"source_entity_id": sender_uuid},
        ):
            await fact_tool(subject="user", predicate="likes", content="coffee")

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
            entity_id=sender_uuid,
            object_entity_id=None,
            valid_at=None,
            idempotency_key=None,
            request_context=None,
            retention_class="operational",
            sensitivity="normal",
            enable_shared_catalog=False,
            source_schema=None,
        )

    async def test_explicit_entity_id_takes_precedence_over_routing_ctx(self):
        """When caller passes entity_id explicitly, routing context is not used."""
        from unittest.mock import patch as _patch

        sender_uuid = "550e8400-e29b-41d4-a716-446655440000"
        explicit_uuid = "660e8400-e29b-41d4-a716-446655440001"
        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()

        with _patch(
            "butlers.modules.memory.get_current_runtime_session_routing_context",
            return_value={"source_entity_id": sender_uuid},
        ):
            await fact_tool(
                subject="user",
                predicate="likes",
                content="coffee",
                entity_id=explicit_uuid,
            )

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
            entity_id=explicit_uuid,
            object_entity_id=None,
            valid_at=None,
            idempotency_key=None,
            request_context=None,
            retention_class="operational",
            sensitivity="normal",
            enable_shared_catalog=False,
            source_schema=None,
        )

    async def test_routing_ctx_missing_source_entity_id_key_rejects(self):
        """When routing context exists but lacks source_entity_id, the call is rejected."""
        from unittest.mock import patch as _patch

        mod, fact_tool, pool, writing = await self._setup_and_get_fact_tool()

        with _patch(
            "butlers.modules.memory.get_current_runtime_session_routing_context",
            return_value={"source_contact_id": "contact-123"},
        ):
            result = await fact_tool(subject="user", predicate="likes", content="coffee")

        assert result["error"] == "entity_id is required"
        assert result["subject"] == "user"
        writing.memory_store_fact.assert_not_called()


# ---------------------------------------------------------------------------
# Registry discovery
# ---------------------------------------------------------------------------


class TestToolGroups:
    """Tool group filtering registers only requested groups."""

    async def test_all_groups_when_none(self):
        """No groups config registers all expected tools."""
        mod = MemoryModule()
        mcp = RuntimeFastMCP("test")
        config = MemoryModuleConfig()  # groups=None (default)
        await mod.register_tools(mcp, config, MagicMock(), "test-butler")
        tools = await mcp.list_tools()
        assert len(tools) == len(EXPECTED_TOOL_NAMES)

    async def test_core_only(self):
        """groups=['core'] registers only the 8 core tools."""
        mod = MemoryModule()
        mcp = RuntimeFastMCP("test")
        config = MemoryModuleConfig(groups=["core"])
        await mod.register_tools(mcp, config, MagicMock(), "test-butler")
        tools = await mcp.list_tools()
        tool_names = {t.name for t in tools}
        assert len(tools) == 8
        assert "memory_search" in tool_names
        assert "memory_store_fact" in tool_names
        assert "memory_context" in tool_names
        # Not in core:
        assert "memory_entity_create" not in tool_names
        assert "memory_stats" not in tool_names

    async def test_core_plus_entity(self):
        """groups=['core', 'entity'] registers 15 tools."""
        mod = MemoryModule()
        mcp = RuntimeFastMCP("test")
        config = MemoryModuleConfig(groups=["core", "entity"])
        await mod.register_tools(mcp, config, MagicMock(), "test-butler")
        tools = await mcp.list_tools()
        tool_names = {t.name for t in tools}
        assert len(tools) == 15  # 8 core + 7 entity
        assert "memory_entity_create" in tool_names
        assert "memory_catalog_search" in tool_names
        assert "memory_stats" not in tool_names  # admin

    async def test_empty_groups_registers_all(self):
        """groups=[] is treated as 'no filter' — registers all expected tools."""
        mod = MemoryModule()
        mcp = RuntimeFastMCP("test")
        config = MemoryModuleConfig(groups=[])
        await mod.register_tools(mcp, config, MagicMock(), "test-butler")
        tools = await mcp.list_tools()
        assert len(tools) == len(EXPECTED_TOOL_NAMES)


class TestEmbeddingModelConfig:
    """Verify ``embedding_model`` round-trips through MemoryModuleConfig.

    The ``memory_access`` core tool reads ``embedding_model`` from the
    validated module config to surface the active embedding model to the
    dashboard.  This contract requires the field to exist on
    ``MemoryModuleConfig`` with a sensible default and to round-trip cleanly
    through ``model_validate`` (the same path the daemon uses when loading
    raw toml dicts).
    """

    def test_default_value_matches_engine_model(self):
        """Default embedding_model matches the model the EmbeddingEngine loads."""
        cfg = MemoryModuleConfig()
        assert cfg.embedding_model == "all-MiniLM-L6-v2"

    def test_field_round_trips_through_model_validate(self):
        """Custom embedding_model survives the toml -> dict -> validate flow."""
        # Simulates what daemon._validate_module_configs() does with a raw
        # ``[modules.memory]`` dict produced by butlers.config.
        raw_from_toml = {"embedding_model": "text-embedding-3-small"}
        cfg = MemoryModuleConfig.model_validate(raw_from_toml)
        assert cfg.embedding_model == "text-embedding-3-small"

    def test_model_dump_emits_embedding_model(self):
        """model_dump round-trips the field so DB-backed loaders see it."""
        cfg = MemoryModuleConfig(embedding_model="custom-model")
        dumped = cfg.model_dump()
        assert dumped["embedding_model"] == "custom-model"
        # Re-validating the dump yields an equivalent config — the round-trip
        # any DB-backed config loader would perform.
        round_tripped = MemoryModuleConfig.model_validate(dumped)
        assert round_tripped.embedding_model == "custom-model"

    def test_default_round_trips_through_model_dump(self):
        """When toml omits embedding_model, the default flows through model_dump."""
        cfg = MemoryModuleConfig.model_validate({})
        dumped = cfg.model_dump()
        assert dumped["embedding_model"] == "all-MiniLM-L6-v2"


class TestGetEmbeddingEngineSingleton:
    """Verify get_embedding_engine() caches by model name and produces a fresh
    instance for a new model name."""

    def test_same_model_returns_same_instance(self):
        """Calling get_embedding_engine() twice with the same model name yields
        the identical cached object."""
        from butlers.modules.memory.tools._helpers import get_embedding_engine

        with patch("butlers.modules.memory.tools._helpers.EmbeddingEngine") as MockEng:
            MockEng.return_value = MagicMock(name="engine-a")
            from butlers.modules.memory.tools import _helpers

            # Clear cache to get a clean slate for this test.
            saved = dict(_helpers._embedding_engines)
            _helpers._embedding_engines.clear()
            try:
                e1 = get_embedding_engine("model-x")
                e2 = get_embedding_engine("model-x")
                assert e1 is e2
                MockEng.assert_called_once_with("model-x")
            finally:
                _helpers._embedding_engines.clear()
                _helpers._embedding_engines.update(saved)

    async def test_concurrent_same_model_builds_single_instance(self):
        """Concurrent same-model calls do not race duplicate engine construction."""
        from butlers.modules.memory.tools import _helpers
        from butlers.modules.memory.tools._helpers import get_embedding_engine

        first_check_entered = threading.Event()
        second_check_entered = threading.Event()
        check_count = 0
        check_count_lock = threading.Lock()

        class RaceDict(dict):
            def __contains__(self, key):
                nonlocal check_count
                present = super().__contains__(key)
                if key == "model-x":
                    with check_count_lock:
                        check_count += 1
                        call_number = check_count
                    if call_number == 1:
                        first_check_entered.set()
                        second_check_entered.wait(timeout=1)
                    elif call_number == 2:
                        second_check_entered.set()
                return present

        with patch(
            "butlers.modules.memory.tools._helpers.EmbeddingEngine",
            side_effect=lambda model_name: MagicMock(name=f"engine-{model_name}"),
        ) as MockEng:
            saved = _helpers._embedding_engines
            _helpers._embedding_engines = RaceDict()
            try:
                loop = asyncio.get_running_loop()
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    first = loop.run_in_executor(executor, get_embedding_engine, "model-x")
                    assert await asyncio.to_thread(first_check_entered.wait, 1)
                    second = loop.run_in_executor(executor, get_embedding_engine, "model-x")
                    e1, e2 = await asyncio.gather(first, second)

                assert e1 is e2
                MockEng.assert_called_once_with("model-x")
            finally:
                _helpers._embedding_engines = saved

    def test_different_model_returns_different_instance(self):
        """A new model name produces a fresh EmbeddingEngine, not the cached one."""
        from butlers.modules.memory.tools._helpers import get_embedding_engine

        with patch("butlers.modules.memory.tools._helpers.EmbeddingEngine") as MockEng:
            eng_a = MagicMock(name="engine-a")
            eng_b = MagicMock(name="engine-b")
            MockEng.side_effect = [eng_a, eng_b]

            from butlers.modules.memory.tools import _helpers

            saved = dict(_helpers._embedding_engines)
            _helpers._embedding_engines.clear()
            try:
                e1 = get_embedding_engine("model-x")
                e2 = get_embedding_engine("model-y")
                assert e1 is not e2
                assert e1 is eng_a
                assert e2 is eng_b
            finally:
                _helpers._embedding_engines.clear()
                _helpers._embedding_engines.update(saved)

    def test_default_model_is_minilm(self):
        """Default model name is all-MiniLM-L6-v2."""
        from butlers.modules.memory.tools._helpers import _DEFAULT_EMBEDDING_MODEL

        assert _DEFAULT_EMBEDDING_MODEL == "all-MiniLM-L6-v2"


class TestModuleEmbeddingEngineWiring:
    """Verify MemoryModule._get_embedding_engine() uses the configured model
    and that a model change invalidates the cached engine reference."""

    def test_uses_configured_model(self):
        """_get_embedding_engine() calls get_embedding_engine with the configured model."""
        mod = MemoryModule()
        cfg = MemoryModuleConfig(embedding_model="custom-test-model")
        mod._config = cfg

        with patch("butlers.modules.memory.tools.get_embedding_engine") as mock_ge:
            fake_engine = MagicMock(name="custom-engine")
            fake_engine._model_name = "custom-test-model"
            mock_ge.return_value = fake_engine

            result = mod._get_embedding_engine()
            mock_ge.assert_called_once_with("custom-test-model")
            assert result is fake_engine

    def test_model_change_clears_cached_engine(self):
        """When embedding_model changes, _get_embedding_engine() drops the old
        cached engine reference so the next call rebuilds it."""
        mod = MemoryModule()
        cfg_a = MemoryModuleConfig(embedding_model="model-a")
        mod._config = cfg_a

        old_engine = MagicMock(name="engine-a")
        old_engine._model_name = "model-a"
        mod._embedding_engine = old_engine

        # Now change the config to a different model.
        cfg_b = MemoryModuleConfig(embedding_model="model-b")
        mod._config = cfg_b

        with patch("butlers.modules.memory.tools.get_embedding_engine") as mock_ge:
            new_engine = MagicMock(name="engine-b")
            new_engine._model_name = "model-b"
            mock_ge.return_value = new_engine

            result = mod._get_embedding_engine()
            mock_ge.assert_called_once_with("model-b")
            assert result is new_engine
            # The cached reference is now the new engine.
            assert mod._embedding_engine is new_engine

    def test_same_model_reuses_cached_engine(self):
        """When model has not changed, _get_embedding_engine() returns the
        existing cached engine without calling get_embedding_engine again."""
        mod = MemoryModule()
        cfg = MemoryModuleConfig(embedding_model="model-a")
        mod._config = cfg

        cached_engine = MagicMock(name="engine-a")
        cached_engine._model_name = "model-a"
        mod._embedding_engine = cached_engine

        with patch("butlers.modules.memory.tools.get_embedding_engine") as mock_ge:
            result = mod._get_embedding_engine()
            mock_ge.assert_not_called()
            assert result is cached_engine


class TestRegistryDiscovery:
    """Verify MemoryModule is found by default_registry()."""

    def test_memory_in_default_registry(self):
        from butlers.modules.registry import default_registry

        registry = default_registry()
        assert "memory" in registry.available_modules

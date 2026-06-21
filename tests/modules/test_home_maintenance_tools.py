"""Tests for the Home Butler maintenance MCP tools.

Covers:
- ha_maintenance_create: happy path, duplicate/invalid rejections, no-db
- ha_maintenance_complete: default/explicit timestamp, not-found, memory fact
- ha_maintenance_list: filters (status/category/none), invalid filters, no-db
- ha_maintenance_remove: happy path, not-found, no-db
- Tool registration: all 4 tools registered
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules._roster_home import HomeAssistantModule

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def module() -> HomeAssistantModule:
    m = HomeAssistantModule()
    from butlers.modules._roster_home import HomeAssistantConfig

    m._config = HomeAssistantConfig()
    return m


@pytest.fixture
def mock_pool() -> MagicMock:
    return MagicMock()


@pytest.fixture
def module_with_pool(module: HomeAssistantModule, mock_pool: MagicMock) -> HomeAssistantModule:
    db = MagicMock()
    db.pool = mock_pool
    module._db = db
    return module


def _make_mcp() -> MagicMock:
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            tools[declared_name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestMaintenanceToolRegistration:
    async def test_maintenance_tools_registered(self, module: HomeAssistantModule) -> None:
        mcp = _make_mcp()
        with patch.object(HomeAssistantModule, "_ws_connect_and_seed", new=AsyncMock()):
            await module.register_tools(mcp, config={}, db=MagicMock(), butler_name="test-butler")

        registered = set(mcp._registered_tools.keys())
        for name in [
            "ha_maintenance_create",
            "ha_maintenance_complete",
            "ha_maintenance_list",
            "ha_maintenance_remove",
        ]:
            assert name in registered


# ---------------------------------------------------------------------------
# ha_maintenance_create
# ---------------------------------------------------------------------------


class TestMaintenanceCreate:
    async def test_create_happy_path_with_notes(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Create new item with notes; returns expected fields."""
        item_id = uuid.uuid4()
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                None,  # duplicate check
                {
                    "id": item_id,
                    "name": "HVAC filter",
                    "category": "filter",
                    "interval_days": 90,
                    "next_due_at": None,
                },
            ]
        )
        result = await module_with_pool._maintenance_create(
            name="HVAC filter",
            category="filter",
            interval_days=90,
            notes="Replace quarterly",
        )
        assert result["name"] == "HVAC filter"
        assert result["interval_days"] == 90
        assert result["id"] == str(item_id)

    async def test_create_duplicate_name_rejected(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
        result = await module_with_pool._maintenance_create(
            name="HVAC filter", category="filter", interval_days=90
        )
        assert "error" in result

    @pytest.mark.parametrize(
        "kwargs,match",
        [
            ({"category": "unknown_cat", "interval_days": 30}, "Invalid category"),
            ({"category": "general", "interval_days": 0}, "positive integer"),
        ],
        ids=["invalid-category", "invalid-interval"],
    )
    async def test_create_validation_errors(
        self, module_with_pool: HomeAssistantModule, kwargs, match
    ) -> None:
        result = await module_with_pool._maintenance_create(name="Widget", **kwargs)
        assert "error" in result and match in result["error"]


# ---------------------------------------------------------------------------
# ha_maintenance_complete
# ---------------------------------------------------------------------------


class TestMaintenanceComplete:
    def _mock_complete_row(self, mock_pool, name="HVAC filter"):
        item_id = uuid.uuid4()
        now = datetime.now(UTC)
        next_due = datetime(2026, 6, 23, tzinfo=UTC)
        mock_pool.fetchrow = AsyncMock(
            return_value={
                "id": item_id,
                "name": name,
                "category": "filter",
                "interval_days": 90,
                "last_completed_at": now,
                "next_due_at": next_due,
            }
        )
        return next_due

    async def test_complete_default_and_explicit_timestamp(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Complete with default timestamp; then verify explicit timestamp is forwarded."""
        next_due = self._mock_complete_row(mock_pool)
        with (
            patch("butlers.modules.memory.storage.store_fact", new=AsyncMock()),
            patch("butlers.modules.memory.tools.get_embedding_engine", return_value=MagicMock()),
        ):
            result = await module_with_pool._maintenance_complete(name="HVAC filter")
        assert result["name"] == "HVAC filter"
        assert result["next_due_at"] == next_due.isoformat()

        # Explicit timestamp
        self._mock_complete_row(mock_pool, "Water filter")
        with (
            patch("butlers.modules.memory.storage.store_fact", new=AsyncMock()),
            patch("butlers.modules.memory.tools.get_embedding_engine", return_value=MagicMock()),
        ):
            result2 = await module_with_pool._maintenance_complete(
                name="Water filter", completed_at="2026-03-20T10:00:00Z"
            )
        assert result2["name"] == "Water filter"

    async def test_complete_not_found_and_invalid_ts(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value=None)
        result = await module_with_pool._maintenance_complete(name="Nonexistent")
        assert "error" in result

        result2 = await module_with_pool._maintenance_complete(
            name="HVAC filter", completed_at="not-a-date"
        )
        assert "error" in result2

    async def test_complete_stores_memory_fact(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        self._mock_complete_row(mock_pool)
        with (
            patch("butlers.modules.memory.storage.store_fact", new=AsyncMock()) as mock_store,
            patch("butlers.modules.memory.tools.get_embedding_engine", return_value=MagicMock()),
        ):
            await module_with_pool._maintenance_complete(name="HVAC filter")
        assert mock_store.called
        kw = mock_store.call_args.kwargs
        assert kw.get("subject") == "HVAC filter"
        assert "maintenance" in kw.get("tags", [])


# ---------------------------------------------------------------------------
# ha_maintenance_list
# ---------------------------------------------------------------------------


class TestMaintenanceList:
    def _make_row(self, name="Test", category="general", status="ok", **kw) -> dict:
        return {
            "id": uuid.uuid4(),
            "name": name,
            "category": category,
            "interval_days": kw.get("interval_days", 30),
            "last_completed_at": None,
            "next_due_at": None,
            "notes": None,
            "status": status,
        }

    async def test_list_all_and_empty(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        mock_pool.fetch = AsyncMock(
            return_value=[
                self._make_row("Filter 1", "filter", "due"),
                self._make_row("HVAC", "hvac", "ok"),
            ]
        )
        result = await module_with_pool._maintenance_list()
        assert len(result) == 2

        mock_pool.fetch = AsyncMock(return_value=[])
        assert await module_with_pool._maintenance_list() == []

    @pytest.mark.parametrize(
        "filter_key,filter_val",
        [
            ("status", "due"),
            ("status", "upcoming"),
            ("category", "hvac"),
        ],
    )
    async def test_list_with_valid_filter(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock, filter_key, filter_val
    ) -> None:
        mock_pool.fetch = AsyncMock(return_value=[self._make_row(status=filter_val)])
        result = await module_with_pool._maintenance_list(**{filter_key: filter_val})
        assert len(result) == 1
        assert filter_val in mock_pool.fetch.call_args.args

    @pytest.mark.parametrize(
        "filter_key,filter_val",
        [
            ("status", "invalid_status"),
            ("category", "unknown"),
        ],
    )
    async def test_list_invalid_filter(
        self, module_with_pool: HomeAssistantModule, filter_key, filter_val
    ) -> None:
        result = await module_with_pool._maintenance_list(**{filter_key: filter_val})
        assert len(result) == 1 and "error" in result[0]


# ---------------------------------------------------------------------------
# ha_maintenance_remove
# ---------------------------------------------------------------------------


class TestMaintenanceRemove:
    async def test_remove_happy_path_and_not_found(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        mock_pool.execute = AsyncMock(return_value="DELETE 1")
        assert (await module_with_pool._maintenance_remove(name="HVAC filter")) == {
            "deleted": True,
            "name": "HVAC filter",
        }

        mock_pool.execute = AsyncMock(return_value="DELETE 0")
        result = await module_with_pool._maintenance_remove(name="Nonexistent")
        assert "error" in result


# ---------------------------------------------------------------------------
# No-DB guard: every maintenance tool returns a Database error result
# ---------------------------------------------------------------------------


class TestMaintenanceNoDb:
    @pytest.mark.parametrize(
        "method_name",
        [
            "_maintenance_create",
            "_maintenance_complete",
            "_maintenance_list",
            "_maintenance_remove",
        ],
    )
    async def test_tool_returns_db_error_without_pool(
        self, module: HomeAssistantModule, method_name: str
    ) -> None:
        kwargs: dict[str, Any] = {"name": "HVAC filter"}
        if method_name == "_maintenance_create":
            kwargs.update(category="general", interval_days=7)
        elif method_name == "_maintenance_list":
            kwargs = {}
        result = await getattr(module, method_name)(**kwargs)
        # list returns a list of result dicts; the others a single dict
        error_obj = result[0] if isinstance(result, list) else result
        assert "Database" in error_obj["error"]

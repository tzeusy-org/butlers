"""Tests for the Home Butler maintenance MCP tools.

Covers:
- ha_maintenance_create: happy path, duplicate name rejection, invalid category,
  invalid interval_days, no-db fallback
- ha_maintenance_complete: happy path (default timestamp), explicit timestamp,
  not-found error, invalid timestamp, memory fact stored
- ha_maintenance_list: no filter, category filter, status filter (due/upcoming/ok),
  invalid filter values, empty result, no-db fallback
- ha_maintenance_remove: happy path, not-found error, no-db fallback
- Tool registration: all 4 tools appear in mcp._registered_tools
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
    """Create a fresh HomeAssistantModule with a minimal config."""
    m = HomeAssistantModule()
    from butlers.modules._roster_home import HomeAssistantConfig

    m._config = HomeAssistantConfig()
    return m


@pytest.fixture
def mock_pool() -> MagicMock:
    """Async-capable mock pool."""
    pool = MagicMock()
    return pool


@pytest.fixture
def module_with_pool(module: HomeAssistantModule, mock_pool: MagicMock) -> HomeAssistantModule:
    """Module wired to a mock pool via a mock db."""
    db = MagicMock()
    db.pool = mock_pool
    module._db = db
    return module


def _make_mcp() -> MagicMock:
    """Build a mock MCP server that captures registered tools."""
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
    """All 4 maintenance tools must be registered when register_tools is called."""

    async def test_maintenance_tools_registered(self, module: HomeAssistantModule) -> None:
        mcp = _make_mcp()
        with patch.object(HomeAssistantModule, "_ws_connect_and_seed", new=AsyncMock()):
            await module.register_tools(mcp, config={}, db=MagicMock())

        registered = set(mcp._registered_tools.keys())
        assert "ha_maintenance_create" in registered
        assert "ha_maintenance_complete" in registered
        assert "ha_maintenance_list" in registered
        assert "ha_maintenance_remove" in registered


# ---------------------------------------------------------------------------
# ha_maintenance_create
# ---------------------------------------------------------------------------


class TestMaintenanceCreate:
    """Tests for _maintenance_create."""

    async def test_create_happy_path(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Create a new item; returns id, name, category, interval_days, next_due_at."""
        item_id = uuid.uuid4()
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                None,  # duplicate check → not found
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
            name="HVAC filter", category="filter", interval_days=90
        )

        assert result["name"] == "HVAC filter"
        assert result["category"] == "filter"
        assert result["interval_days"] == 90
        assert result["next_due_at"] is None
        assert result["id"] == str(item_id)

    async def test_create_duplicate_name_rejected(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Duplicate name returns error without inserting."""
        mock_pool.fetchrow = AsyncMock(
            return_value={"id": uuid.uuid4()}  # existing row found
        )

        result = await module_with_pool._maintenance_create(
            name="HVAC filter", category="filter", interval_days=90
        )

        assert "error" in result
        assert "already exists" in result["error"]
        assert "hint" in result

    async def test_create_invalid_category(self, module_with_pool: HomeAssistantModule) -> None:
        """Invalid category returns error without touching DB."""
        result = await module_with_pool._maintenance_create(
            name="Widget", category="unknown_cat", interval_days=30
        )

        assert "error" in result
        assert "Invalid category" in result["error"]

    async def test_create_invalid_interval_days(
        self, module_with_pool: HomeAssistantModule
    ) -> None:
        """Non-positive interval_days returns error without touching DB."""
        result = await module_with_pool._maintenance_create(
            name="Widget", category="general", interval_days=0
        )

        assert "error" in result
        assert "positive integer" in result["error"]

    async def test_create_no_db(self, module: HomeAssistantModule) -> None:
        """Returns error when DB is not available."""
        result = await module._maintenance_create(
            name="Widget", category="general", interval_days=7
        )

        assert "error" in result
        assert "Database" in result["error"]

    async def test_create_with_notes(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Notes are passed through to the INSERT."""
        item_id = uuid.uuid4()
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                None,  # duplicate check
                {
                    "id": item_id,
                    "name": "Dryer vent",
                    "category": "appliance",
                    "interval_days": 365,
                    "next_due_at": None,
                },
            ]
        )

        result = await module_with_pool._maintenance_create(
            name="Dryer vent",
            category="appliance",
            interval_days=365,
            notes="Clean annually to prevent fire hazard",
        )

        assert result["name"] == "Dryer vent"
        # Verify notes were passed to the INSERT call (args: query, name, cat, days, notes)
        insert_call = mock_pool.fetchrow.call_args_list[1]
        assert any("Clean annually" in str(a) for a in insert_call.args)


# ---------------------------------------------------------------------------
# ha_maintenance_complete
# ---------------------------------------------------------------------------


class TestMaintenanceComplete:
    """Tests for _maintenance_complete."""

    async def test_complete_happy_path_default_timestamp(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Complete with default timestamp; next_due_at is recomputed."""
        item_id = uuid.uuid4()
        now = datetime.now(UTC)
        next_due = datetime(2026, 6, 23, tzinfo=UTC)

        mock_pool.fetchrow = AsyncMock(
            return_value={
                "id": item_id,
                "name": "HVAC filter",
                "category": "filter",
                "interval_days": 90,
                "last_completed_at": now,
                "next_due_at": next_due,
            }
        )

        with patch("butlers.modules.memory.storage.store_fact", new=AsyncMock()):
            result = await module_with_pool._maintenance_complete(name="HVAC filter")

        assert result["name"] == "HVAC filter"
        assert result["last_completed_at"] is not None
        assert result["next_due_at"] == next_due.isoformat()

    async def test_complete_explicit_timestamp(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Complete with explicit completed_at timestamp."""
        item_id = uuid.uuid4()
        ts = datetime(2026, 3, 20, 10, 0, 0, tzinfo=UTC)
        next_due = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)

        mock_pool.fetchrow = AsyncMock(
            return_value={
                "id": item_id,
                "name": "Water filter",
                "category": "filter",
                "interval_days": 90,
                "last_completed_at": ts,
                "next_due_at": next_due,
            }
        )

        with patch("butlers.modules.memory.storage.store_fact", new=AsyncMock()):
            result = await module_with_pool._maintenance_complete(
                name="Water filter", completed_at="2026-03-20T10:00:00Z"
            )

        assert result["name"] == "Water filter"
        call_args = mock_pool.fetchrow.call_args
        passed_ts: datetime = call_args.args[2]
        assert passed_ts.year == 2026
        assert passed_ts.month == 3
        assert passed_ts.day == 20

    async def test_complete_not_found(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Returns error when item name does not exist."""
        mock_pool.fetchrow = AsyncMock(return_value=None)

        result = await module_with_pool._maintenance_complete(name="Nonexistent item")

        assert "error" in result
        assert "Nonexistent item" in result["error"]

    async def test_complete_invalid_timestamp(self, module_with_pool: HomeAssistantModule) -> None:
        """Returns error for unparseable completed_at."""
        result = await module_with_pool._maintenance_complete(
            name="HVAC filter", completed_at="not-a-date"
        )

        assert "error" in result
        assert "Invalid completed_at" in result["error"]

    async def test_complete_no_db(self, module: HomeAssistantModule) -> None:
        """Returns error when DB is not available."""
        result = await module._maintenance_complete(name="HVAC filter")

        assert "error" in result
        assert "Database" in result["error"]

    async def test_complete_stores_memory_fact(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Completion stores a memory fact with maintenance tags."""
        item_id = uuid.uuid4()
        now = datetime.now(UTC)
        next_due = datetime(2026, 6, 23, tzinfo=UTC)

        mock_pool.fetchrow = AsyncMock(
            return_value={
                "id": item_id,
                "name": "HVAC filter",
                "category": "filter",
                "interval_days": 90,
                "last_completed_at": now,
                "next_due_at": next_due,
            }
        )

        with patch("butlers.modules.memory.storage.store_fact", new=AsyncMock()) as mock_store:
            await module_with_pool._maintenance_complete(name="HVAC filter")

        assert mock_store.called
        call_kwargs = mock_store.call_args
        # store_fact(pool, subject=..., predicate=..., content=..., ...)
        assert call_kwargs.kwargs.get("subject") == "HVAC filter"
        assert call_kwargs.kwargs.get("predicate") == "device_issue"
        assert "maintenance" in call_kwargs.kwargs.get("tags", [])
        assert "filter" in call_kwargs.kwargs.get("tags", [])


# ---------------------------------------------------------------------------
# ha_maintenance_list
# ---------------------------------------------------------------------------


class TestMaintenanceList:
    """Tests for _maintenance_list."""

    def _make_row(
        self,
        name: str = "Test item",
        category: str = "general",
        interval_days: int = 30,
        next_due_at: datetime | None = None,
        last_completed_at: datetime | None = None,
        status: str = "ok",
        notes: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": uuid.uuid4(),
            "name": name,
            "category": category,
            "interval_days": interval_days,
            "last_completed_at": last_completed_at,
            "next_due_at": next_due_at,
            "notes": notes,
            "status": status,
        }

    async def test_list_all_items(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Returns all items when no filters applied."""
        rows = [
            self._make_row("Filter 1", "filter", status="due"),
            self._make_row("HVAC service", "hvac", status="ok"),
        ]
        mock_pool.fetch = AsyncMock(return_value=rows)

        result = await module_with_pool._maintenance_list()

        assert len(result) == 2
        assert result[0]["name"] == "Filter 1"
        assert result[1]["name"] == "HVAC service"

    async def test_list_with_status_filter(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Status filter excludes items with different status."""
        rows = [
            self._make_row("Filter 1", "filter", status="due"),
            self._make_row("HVAC service", "hvac", status="ok"),
        ]
        mock_pool.fetch = AsyncMock(return_value=rows)

        result = await module_with_pool._maintenance_list(status="due")

        assert len(result) == 1
        assert result[0]["name"] == "Filter 1"
        assert result[0]["status"] == "due"

    async def test_list_with_category_filter(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Category filter is passed to DB query; only matching rows returned."""
        rows = [self._make_row("HVAC service", "hvac", status="ok")]
        mock_pool.fetch = AsyncMock(return_value=rows)

        result = await module_with_pool._maintenance_list(category="hvac")

        assert len(result) == 1
        # Verify category was passed to the fetch call
        fetch_call_args = mock_pool.fetch.call_args.args
        assert "hvac" in fetch_call_args

    async def test_list_invalid_status_filter(self, module_with_pool: HomeAssistantModule) -> None:
        """Invalid status filter returns error without DB call."""
        result = await module_with_pool._maintenance_list(status="invalid_status")

        assert len(result) == 1
        assert "error" in result[0]
        assert "invalid_status" in result[0]["error"]

    async def test_list_invalid_category_filter(
        self, module_with_pool: HomeAssistantModule
    ) -> None:
        """Invalid category filter returns error without DB call."""
        result = await module_with_pool._maintenance_list(category="unknown")

        assert len(result) == 1
        assert "error" in result[0]
        assert "unknown" in result[0]["error"]

    async def test_list_empty(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Empty table returns empty list."""
        mock_pool.fetch = AsyncMock(return_value=[])

        result = await module_with_pool._maintenance_list()

        assert result == []

    async def test_list_no_db(self, module: HomeAssistantModule) -> None:
        """Returns error list when DB is not available."""
        result = await module._maintenance_list()

        assert len(result) == 1
        assert "error" in result[0]
        assert "Database" in result[0]["error"]

    async def test_list_upcoming_status(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Upcoming status filter returns only upcoming items."""
        rows = [
            self._make_row("Water filter", "filter", status="upcoming"),
            self._make_row("HVAC service", "hvac", status="ok"),
        ]
        mock_pool.fetch = AsyncMock(return_value=rows)

        result = await module_with_pool._maintenance_list(status="upcoming")

        assert len(result) == 1
        assert result[0]["name"] == "Water filter"
        assert result[0]["status"] == "upcoming"


# ---------------------------------------------------------------------------
# ha_maintenance_remove
# ---------------------------------------------------------------------------


class TestMaintenanceRemove:
    """Tests for _maintenance_remove."""

    async def test_remove_happy_path(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Deleting an existing item returns deleted=True."""
        mock_pool.execute = AsyncMock(return_value="DELETE 1")

        result = await module_with_pool._maintenance_remove(name="HVAC filter")

        assert result == {"deleted": True, "name": "HVAC filter"}

    async def test_remove_not_found(
        self, module_with_pool: HomeAssistantModule, mock_pool: MagicMock
    ) -> None:
        """Returns error when no row with that name exists."""
        mock_pool.execute = AsyncMock(return_value="DELETE 0")

        result = await module_with_pool._maintenance_remove(name="Nonexistent")

        assert "error" in result
        assert "Nonexistent" in result["error"]
        assert "hint" in result

    async def test_remove_no_db(self, module: HomeAssistantModule) -> None:
        """Returns error when DB is not available."""
        result = await module._maintenance_remove(name="HVAC filter")

        assert "error" in result
        assert "Database" in result["error"]

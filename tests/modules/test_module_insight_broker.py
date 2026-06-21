"""Tests for InsightBrokerModule — registration, tool wiring, config parsing.

Covers bu-q1q3:
1. Module ABC compliance (name, dependencies, config_schema, lifecycle)
2. Registry discovery via roster/switchboard/modules/__init__.py
3. propose_insight_candidate MCP tool is registered and callable
4. butler.toml contains [modules.insight_broker] and insight-delivery-cycle schedule
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_mcp() -> MagicMock:
    """Create a mock MCP server that captures registered tools by name."""
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


@pytest.fixture
def module():
    """Return a fresh InsightBrokerModule instance.

    The module lives in roster/switchboard/modules/ and is loaded dynamically
    by the registry under ``butlers.modules._roster_switchboard``.
    """
    # Trigger registry discovery so the roster module is registered in sys.modules.
    from butlers.modules.registry import default_registry  # noqa: PLC0415

    default_registry()
    import sys  # noqa: PLC0415

    insight_broker_mod = sys.modules["butlers.modules._roster_switchboard.insight_broker"]
    return insight_broker_mod.InsightBrokerModule()


# ---------------------------------------------------------------------------
# Category 1: Module ABC compliance
# ---------------------------------------------------------------------------


class TestInsightBrokerModuleABC:
    """Verify InsightBrokerModule implements the Module ABC correctly."""

    def test_module_contract(self, module):
        """InsightBrokerModule satisfies Module ABC: name, dependencies, revisions."""
        from pydantic import BaseModel

        assert module.name == "insight_broker"
        assert module.dependencies == []
        assert issubclass(module.config_schema, BaseModel)
        assert module.migration_revisions() is None

    @pytest.mark.asyncio
    async def test_lifecycle_db(self, module):
        """on_startup stores DB; on_shutdown clears it; pool accessible after startup."""
        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        await module.on_startup(config={}, db=fake_db)
        assert module._db is fake_db
        assert module._get_pool() is fake_db.pool
        await module.on_shutdown()
        assert module._db is None

    def test_get_pool_raises_when_not_initialised(self, module):
        with pytest.raises(RuntimeError, match="InsightBrokerModule not initialised"):
            module._get_pool()


# ---------------------------------------------------------------------------
# Category 2: Registry discovery
# ---------------------------------------------------------------------------


class TestInsightBrokerRegistryDiscovery:
    """Verify InsightBrokerModule is discovered by the module registry."""

    def test_default_registry_includes_insight_broker(self):
        """default_registry() discovers InsightBrokerModule via roster scan."""
        from butlers.modules.registry import default_registry

        registry = default_registry()
        assert "insight_broker" in registry.available_modules

    def test_registry_can_load_insight_broker_from_config(self):
        """ModuleRegistry.load_from_config succeeds with insight_broker config."""
        from butlers.modules.registry import default_registry

        registry = default_registry()
        modules = registry.load_from_config({"insight_broker": {}})
        names = [m.name for m in modules]
        assert "insight_broker" in names


# ---------------------------------------------------------------------------
# Category 3: MCP tool registration
# ---------------------------------------------------------------------------


class TestProposeInsightCandidateTool:
    """Verify propose_insight_candidate is registered and callable."""

    @pytest.mark.asyncio
    async def test_registers_propose_insight_candidate_tool(self, module, mock_mcp):
        """register_tools creates the propose_insight_candidate MCP tool."""
        import asyncio

        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        await module.register_tools(mcp=mock_mcp, config={}, db=fake_db, butler_name="test-butler")
        assert "propose_insight_candidate" in mock_mcp._registered_tools
        tool_fn = mock_mcp._registered_tools["propose_insight_candidate"]
        assert callable(tool_fn)
        assert asyncio.iscoroutinefunction(tool_fn)


# ---------------------------------------------------------------------------
# Category 4: butler.toml config verification
# ---------------------------------------------------------------------------


class TestSwitchboardButlerToml:
    """Verify butler.toml correctly declares insight_broker and schedule."""

    @pytest.fixture
    def switchboard_config(self):
        from butlers.config import load_config

        roster_dir = Path(__file__).resolve().parent.parent.parent / "roster" / "switchboard"
        return load_config(roster_dir)

    def test_insight_broker_module_declared(self, switchboard_config):
        """[modules.insight_broker] is present in butler.toml."""
        assert "insight_broker" in switchboard_config.modules

    def test_insight_delivery_cycle_schedule_fully_wired(self, switchboard_config):
        """insight-delivery-cycle is declared as a job dispatch with cron '0 8 * * *'."""
        from butlers.config import ScheduleDispatchMode

        schedule_names = [s.name for s in switchboard_config.schedules]
        assert "insight-delivery-cycle" in schedule_names

        schedule = next(
            s for s in switchboard_config.schedules if s.name == "insight-delivery-cycle"
        )
        assert schedule.cron == "0 8 * * *"
        assert schedule.dispatch_mode == ScheduleDispatchMode.JOB
        assert schedule.job_name == "insight_delivery_cycle"

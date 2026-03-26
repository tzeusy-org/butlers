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
from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_name_is_insight_broker(self, module):
        assert module.name == "insight_broker"

    def test_dependencies_is_empty(self, module):
        assert module.dependencies == []

    def test_config_schema_is_pydantic_model(self, module):
        from pydantic import BaseModel

        assert issubclass(module.config_schema, BaseModel)

    def test_migration_revisions_is_none(self, module):
        assert module.migration_revisions() is None

    @pytest.mark.asyncio
    async def test_on_startup_stores_db(self, module):
        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        await module.on_startup(config={}, db=fake_db)
        assert module._db is fake_db

    @pytest.mark.asyncio
    async def test_on_shutdown_clears_db(self, module):
        fake_db = MagicMock()
        await module.on_startup(config={}, db=fake_db)
        await module.on_shutdown()
        assert module._db is None

    def test_get_pool_raises_when_not_initialised(self, module):
        with pytest.raises(RuntimeError, match="InsightBrokerModule not initialised"):
            module._get_pool()

    @pytest.mark.asyncio
    async def test_get_pool_returns_pool_after_startup(self, module):
        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        await module.on_startup(config={}, db=fake_db)
        assert module._get_pool() is fake_db.pool


# ---------------------------------------------------------------------------
# Category 2: Registry discovery
# ---------------------------------------------------------------------------


class TestInsightBrokerRegistryDiscovery:
    """Verify InsightBrokerModule is discovered by the module registry."""

    def test_module_is_importable(self):
        """InsightBrokerModule is loadable via the registry's roster scanner."""
        import sys  # noqa: PLC0415

        from butlers.modules.registry import default_registry  # noqa: PLC0415

        default_registry()
        assert "butlers.modules._roster_switchboard.insight_broker" in sys.modules
        mod = sys.modules["butlers.modules._roster_switchboard.insight_broker"]
        assert hasattr(mod, "InsightBrokerModule")

    def test_module_exported_from_roster_package(self):
        """InsightBrokerModule is importable from the _roster_switchboard synthetic module."""
        import sys  # noqa: PLC0415

        from butlers.modules.registry import default_registry  # noqa: PLC0415

        default_registry()
        roster_mod = sys.modules["butlers.modules._roster_switchboard"]
        assert hasattr(roster_mod, "InsightBrokerModule")

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
        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        await module.register_tools(mcp=mock_mcp, config={}, db=fake_db)
        assert "propose_insight_candidate" in mock_mcp._registered_tools

    @pytest.mark.asyncio
    async def test_registered_tool_is_callable(self, module, mock_mcp):
        """The registered propose_insight_candidate is an async callable."""
        import asyncio

        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        await module.register_tools(mcp=mock_mcp, config={}, db=fake_db)
        tool_fn = mock_mcp._registered_tools["propose_insight_candidate"]
        assert callable(tool_fn)
        assert asyncio.iscoroutinefunction(tool_fn)

    @pytest.mark.asyncio
    async def test_tool_delegates_to_broker(self, module, mock_mcp):
        """Calling the tool delegates to propose_insight_candidate in broker."""
        from datetime import UTC, datetime, timedelta

        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        accepted = {"status": "accepted", "reason": "candidate queued for delivery cycle"}

        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            new=AsyncMock(return_value=accepted),
        ) as mock_propose:
            await module.register_tools(mcp=mock_mcp, config={}, db=fake_db)
            tool_fn = mock_mcp._registered_tools["propose_insight_candidate"]
            future_dt = (datetime.now(UTC) + timedelta(days=7)).isoformat()
            result = await tool_fn(
                origin_butler="lifestyle",
                priority=75,
                category="birthday",
                dedup_key="birthday:entity-123:2026",
                message="Alice's birthday is in 3 days",
                expires_at=future_dt,
            )

        assert result == accepted
        mock_propose.assert_called_once()
        call_kwargs = mock_propose.call_args.kwargs
        assert call_kwargs["origin_butler"] == "lifestyle"
        assert call_kwargs["priority"] == 75
        assert call_kwargs["category"] == "birthday"

    @pytest.mark.asyncio
    async def test_tool_passes_optional_args(self, module, mock_mcp):
        """Optional args (cooldown_days, channel, metadata) are forwarded."""
        from datetime import UTC, datetime, timedelta

        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        accepted = {"status": "accepted", "reason": "candidate queued for delivery cycle"}

        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            new=AsyncMock(return_value=accepted),
        ) as mock_propose:
            await module.register_tools(mcp=mock_mcp, config={}, db=fake_db)
            tool_fn = mock_mcp._registered_tools["propose_insight_candidate"]
            future_dt = (datetime.now(UTC) + timedelta(days=7)).isoformat()
            await tool_fn(
                origin_butler="finance",
                priority=55,
                category="spending",
                dedup_key="finance:spending:overage:2026-w13",
                message="You spent 20% over budget this week",
                expires_at=future_dt,
                cooldown_days=3,
                channel="telegram",
                metadata={"amount_over": 150},
            )

        call_kwargs = mock_propose.call_args.kwargs
        assert call_kwargs["cooldown_days"] == 3
        assert call_kwargs["channel"] == "telegram"
        assert call_kwargs["metadata"] == {"amount_over": 150}


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

    def test_insight_delivery_cycle_schedule_present(self, switchboard_config):
        """insight-delivery-cycle schedule is declared in butler.toml."""
        schedule_names = [s.name for s in switchboard_config.schedules]
        assert "insight-delivery-cycle" in schedule_names

    def test_insight_delivery_cycle_cron(self, switchboard_config):
        """insight-delivery-cycle uses cron '0 8 * * *'."""
        schedule = next(
            s for s in switchboard_config.schedules if s.name == "insight-delivery-cycle"
        )
        assert schedule.cron == "0 8 * * *"

    def test_insight_delivery_cycle_dispatch_mode(self, switchboard_config):
        """insight-delivery-cycle dispatch_mode is 'job'."""
        from butlers.config import ScheduleDispatchMode

        schedule = next(
            s for s in switchboard_config.schedules if s.name == "insight-delivery-cycle"
        )
        assert schedule.dispatch_mode == ScheduleDispatchMode.JOB

    def test_insight_delivery_cycle_job_name(self, switchboard_config):
        """insight-delivery-cycle job_name is 'insight_delivery_cycle'."""
        schedule = next(
            s for s in switchboard_config.schedules if s.name == "insight-delivery-cycle"
        )
        assert schedule.job_name == "insight_delivery_cycle"

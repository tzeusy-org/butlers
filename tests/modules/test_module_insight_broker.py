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
from fastmcp import FastMCP

from butlers.testing.mcp import registered_tools

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_mcp() -> FastMCP:
    return FastMCP("remaining4-insight-tools")


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
        assert {
            "propose_insight_candidate",
            "insight_mark_useful",
            "insight_snooze",
            "insight_mute",
        } <= (await registered_tools(mock_mcp)).keys()
        tool_fn = (await registered_tools(mock_mcp))["propose_insight_candidate"]
        assert callable(tool_fn)
        assert asyncio.iscoroutinefunction(tool_fn)

    @pytest.mark.asyncio
    async def test_feedback_tools_call_the_shared_server_attributed_behavior(
        self, module, mock_mcp
    ):
        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        with patch(
            "butlers.tools.switchboard.insight.broker.record_insight_feedback",
            new=AsyncMock(return_value={"status": "recorded"}),
        ) as feedback:
            await module.register_tools(
                mcp=mock_mcp, config={}, db=fake_db, butler_name="switchboard"
            )
            await (await registered_tools(mock_mcp))["insight_mark_useful"]("insight-1")
            await (await registered_tools(mock_mcp))["insight_snooze"](
                "insight-2", "2026-09-20T00:00:00Z"
            )
            await (await registered_tools(mock_mcp))["insight_mute"]("insight-3")

        assert [call.kwargs["verdict"] for call in feedback.await_args_list] == [
            "useful",
            "not_now",
            "never",
        ]
        assert {call.kwargs["actor"] for call in feedback.await_args_list} == {"owner"}


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
        """insight-delivery-cycle is a windowed hold-until-active job dispatch
        (bu-ep4ks.9 slice 5) with cron '15,45 6-11 * * *'."""
        from butlers.config import ScheduleDispatchMode

        schedule_names = [s.name for s in switchboard_config.schedules]
        assert "insight-delivery-cycle" in schedule_names

        schedule = next(
            s for s in switchboard_config.schedules if s.name == "insight-delivery-cycle"
        )
        assert schedule.cron == "15,45 6-11 * * *"
        assert schedule.dispatch_mode == ScheduleDispatchMode.JOB
        assert schedule.job_name == "insight_delivery_cycle"


# ---------------------------------------------------------------------------
# Category 5: server-derived origin (bu-svqpzo.7)
# ---------------------------------------------------------------------------

_CANDIDATE: dict[str, Any] = {
    "priority": 60,
    "category": "bill",
    "dedup_key": "bill:hsbc:2026-10",
    "message": "synthetic insight",
    "expires_at": "2026-11-01T00:00:00Z",
}


class TestProposeInsightCandidateOrigin:
    """A session cannot choose which butler an insight candidate claims to come from."""

    @pytest.fixture
    async def server(self, module):
        from fastmcp import FastMCP

        mcp = FastMCP("origin-test")
        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            new=AsyncMock(return_value={"status": "accepted", "reason": "queued"}),
        ) as propose:
            await module.register_tools(mcp=mcp, config={}, db=fake_db, butler_name="switchboard")
            yield mcp, propose

    async def test_schema_has_no_origin_and_the_call_stamps_the_registering_butler(self, server):
        mcp, propose = server
        tool = await mcp.get_tool("propose_insight_candidate")
        assert "origin_butler" not in tool.parameters["properties"]

        result = await mcp.call_tool("propose_insight_candidate", _CANDIDATE)

        assert result.structured_content["status"] == "accepted"
        propose.assert_awaited_once()
        assert propose.await_args.kwargs["origin_butler"] == "switchboard"

    async def test_a_supplied_origin_never_reaches_the_shared_function(self, server):
        mcp, propose = server
        try:
            await mcp.call_tool(
                "propose_insight_candidate", {**_CANDIDATE, "origin_butler": "finance"}
            )
        except Exception:  # noqa: BLE001 - validation failure is an acceptable outcome
            pass

        origins = [call.kwargs["origin_butler"] for call in propose.await_args_list]
        assert "finance" not in origins
        assert set(origins) <= {"switchboard"}

    async def test_foreign_butler_scoped_key_is_refused_and_cross_butler_key_is_accepted(
        self, server
    ):
        mcp, propose = server

        refused = await mcp.call_tool(
            "propose_insight_candidate", {**_CANDIDATE, "dedup_key": "finance:bill:hsbc:2026-10"}
        )
        assert refused.structured_content["status"] == "error"
        propose.assert_not_awaited()

        own = await mcp.call_tool(
            "propose_insight_candidate",
            {**_CANDIDATE, "dedup_key": "switchboard:bill:hsbc:2026-10"},
        )
        cross = await mcp.call_tool("propose_insight_candidate", _CANDIDATE)
        assert own.structured_content["status"] == "accepted"
        assert cross.structured_content["status"] == "accepted"
        assert propose.await_count == 2

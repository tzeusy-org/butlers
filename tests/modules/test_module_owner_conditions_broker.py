"""Tests for OwnerConditionsBrokerModule — registration, tool wiring, config parsing.

Mirrors tests/modules/test_module_insight_broker.py's four categories for
the owner condition ledger's MCP doorway (bu-ep4ks.6):
1. Module ABC compliance (name, dependencies, config_schema, lifecycle)
2. Registry discovery via roster/switchboard/modules/__init__.py
3. reconcile_owner_condition MCP tool is registered and callable
4. butler.toml declares [modules.owner_conditions_broker]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_mcp() -> MagicMock:
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
    from butlers.modules.registry import default_registry  # noqa: PLC0415

    default_registry()
    import sys  # noqa: PLC0415

    mod = sys.modules["butlers.modules._roster_switchboard.owner_conditions_broker"]
    return mod.OwnerConditionsBrokerModule()


class TestOwnerConditionsBrokerModuleABC:
    def test_module_contract(self, module):
        from pydantic import BaseModel

        assert module.name == "owner_conditions_broker"
        assert module.dependencies == []
        assert issubclass(module.config_schema, BaseModel)
        assert module.migration_revisions() is None

    @pytest.mark.asyncio
    async def test_lifecycle_db(self, module):
        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        await module.on_startup(config={}, db=fake_db)
        assert module._db is fake_db
        assert module._get_pool() is fake_db.pool
        await module.on_shutdown()
        assert module._db is None

    def test_get_pool_raises_when_not_initialised(self, module):
        with pytest.raises(RuntimeError, match="OwnerConditionsBrokerModule not initialised"):
            module._get_pool()


class TestOwnerConditionsBrokerRegistryDiscovery:
    def test_default_registry_includes_owner_conditions_broker(self):
        from butlers.modules.registry import default_registry

        registry = default_registry()
        assert "owner_conditions_broker" in registry.available_modules

    def test_registry_can_load_owner_conditions_broker_from_config(self):
        from butlers.modules.registry import default_registry

        registry = default_registry()
        modules = registry.load_from_config({"owner_conditions_broker": {}})
        names = [m.name for m in modules]
        assert "owner_conditions_broker" in names


class TestReconcileOwnerConditionTool:
    @pytest.mark.asyncio
    async def test_registers_reconcile_owner_condition_tool(self, module, mock_mcp):
        import asyncio

        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        await module.register_tools(mcp=mock_mcp, config={}, db=fake_db, butler_name="test-butler")
        assert "reconcile_owner_condition" in mock_mcp._registered_tools
        tool_fn = mock_mcp._registered_tools["reconcile_owner_condition"]
        assert callable(tool_fn)
        assert asyncio.iscoroutinefunction(tool_fn)

    @pytest.mark.asyncio
    async def test_rejects_observation_missing_fingerprint(self, module, mock_mcp):
        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        await module.register_tools(mcp=mock_mcp, config={}, db=fake_db, butler_name="test-butler")
        tool_fn = mock_mcp._registered_tools["reconcile_owner_condition"]

        result = await tool_fn(
            source="finance:bill-overdue",
            observations=[{"summary": "no fingerprint here"}],
            snapshot_complete=True,
        )
        assert result["status"] == "error"
        assert "fingerprint" in result["reason"]

    @pytest.mark.asyncio
    async def test_rejects_invalid_source_before_touching_pool(self, module, mock_mcp):
        fake_db = MagicMock()
        fake_db.pool = AsyncMock()
        await module.register_tools(mcp=mock_mcp, config={}, db=fake_db, butler_name="test-butler")
        tool_fn = mock_mcp._registered_tools["reconcile_owner_condition"]

        result = await tool_fn(source="", observations=[], snapshot_complete=True)
        assert result["status"] == "error"
        fake_db.pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_delegates_to_reconcile_snapshot(self, module, mock_mcp, monkeypatch):
        """Successful calls translate transitions into JSON-serializable dicts."""
        from butlers.core import owner_conditions as owner_conditions_module
        from butlers.core.condition_ledger import ConditionTransition

        fake_transition = ConditionTransition(
            condition_id="00000000-0000-0000-0000-000000000000",
            source="finance:bill-overdue",
            fingerprint="fp-1",
            episode=1,
            state="open",
            transition="opened",
            escalation_level="L0",
            next_reescalate_at=None,
        )

        async def _fake_reconcile(pool, **kwargs):
            return [fake_transition]

        monkeypatch.setattr(owner_conditions_module, "reconcile_snapshot", _fake_reconcile)

        fake_db = MagicMock()
        fake_db.pool = MagicMock()
        await module.register_tools(mcp=mock_mcp, config={}, db=fake_db, butler_name="test-butler")
        tool_fn = mock_mcp._registered_tools["reconcile_owner_condition"]

        result = await tool_fn(
            source="finance:bill-overdue",
            observations=[{"fingerprint": "fp-1", "summary": "Utility Co overdue"}],
            snapshot_complete=True,
        )
        assert result["status"] == "accepted"
        assert result["transitions"] == [
            {
                "fingerprint": "fp-1",
                "episode": 1,
                "state": "open",
                "transition": "opened",
                "escalation_level": "L0",
                "recovered_after_s": None,
            }
        ]


class TestSwitchboardButlerTomlOwnerConditions:
    @pytest.fixture
    def switchboard_config(self):
        from butlers.config import load_config

        roster_dir = Path(__file__).resolve().parent.parent.parent / "roster" / "switchboard"
        return load_config(roster_dir)

    def test_owner_conditions_broker_module_declared(self, switchboard_config):
        assert "owner_conditions_broker" in switchboard_config.modules

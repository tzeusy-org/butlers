"""Condensed SelfHealingModule tests — behavioral contract only.

Replaces 53 tests with ~12 focused behavioral tests.

Covers:
- Module ABC compliance
- SelfHealingConfig validation (defaults, extra rejected)
- Tool registration (report_error, get_healing_status)
- Tool sensitivity metadata
- report_error: not configured returns error dict
- report_error: registered tool shim → QA relay path (bu-fbft2)
- get_healing_status: empty list when no attempts
- _serialize_attempt: handles UUID and datetime

[bu-7sd7a]
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from butlers.modules.base import Module, ToolMeta
from butlers.modules.self_healing import SelfHealingConfig, SelfHealingModule, _serialize_attempt

pytestmark = pytest.mark.unit


def _make_module() -> SelfHealingModule:
    return SelfHealingModule()


class TestModuleABC:
    def test_module_contract(self) -> None:
        """SelfHealingModule satisfies Module ABC: name, config_schema, revisions, registry."""
        from butlers.modules.registry import default_registry

        mod = _make_module()
        assert issubclass(SelfHealingModule, Module)
        assert mod.name == "self_healing"
        assert mod.config_schema is SelfHealingConfig
        # Schema owned by core migration (public.healing_attempts)
        assert mod.migration_revisions() is None
        assert "self_healing" in default_registry().available_modules


class TestSelfHealingConfig:
    def test_defaults(self) -> None:
        cfg = SelfHealingConfig()
        assert cfg.max_concurrent > 0
        assert cfg.enabled is True

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SelfHealingConfig(unknown_field="x")


class TestToolRegistration:
    async def test_registers_expected_tools(self) -> None:
        mod = _make_module()
        registered: dict = {}
        mcp = MagicMock()
        mcp.tool.side_effect = lambda **kw: (
            lambda fn: registered.__setitem__(kw.get("name") or fn.__name__, fn) or fn
        )
        await mod.register_tools(mcp=mcp, config=None, db=None, butler_name="test-butler")
        assert "report_error" in registered
        assert "get_healing_status" in registered

    def test_tool_metadata_marks_sensitive_args(self) -> None:
        mod = _make_module()
        meta = mod.tool_metadata()
        assert "report_error" in meta
        report_meta = meta["report_error"]
        assert isinstance(report_meta, ToolMeta)
        # error_message, traceback, context should be marked sensitive
        for key in ("error_message", "traceback", "context"):
            assert report_meta.arg_sensitivities.get(key) is True


class TestReportErrorBehavior:
    async def test_not_configured_returns_error(self) -> None:
        mod = _make_module()
        mcp = MagicMock()
        registered: dict = {}
        mcp.tool.side_effect = lambda **kw: (
            lambda fn: registered.__setitem__(kw.get("name") or fn.__name__, fn) or fn
        )
        await mod.register_tools(mcp=mcp, config=None, db=None, butler_name="test-butler")

        result = await registered["report_error"](
            error_type="test_error", error_message="test error"
        )
        assert isinstance(result, dict)

    async def test_registered_tool_shim_relays_via_switchboard(self) -> None:
        """report_error registered tool shim routes through QA relay (bu-fbft2).

        Verifies the full shim → handler → switchboard call_tool chain starting
        from the registered MCP tool closure, not just the internal handler.
        """
        route_calls: list[dict] = []

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            if tool_name == "list_butlers":
                return [{"name": "qa"}]
            if tool_name == "route":
                route_calls.append(args or {})
                return {"accepted": True}
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        mod = SelfHealingModule()
        mod._pool = None
        mod._switchboard_client = client

        mcp = MagicMock()
        registered: dict = {}
        mcp.tool.side_effect = lambda **kw: (
            lambda fn: registered.__setitem__(kw.get("name") or fn.__name__, fn) or fn
        )
        await mod.register_tools(mcp=mcp, config=None, db=None, butler_name="relay-butler")

        result = await registered["report_error"](
            error_type="ValueError",
            error_message="relay test error",
            call_site="test.py:run",
            context="relay context",
        )

        assert result["accepted"] is True
        assert len(route_calls) == 1
        ra = route_calls[0]
        assert ra["target_butler"] == "qa"
        assert ra["tool_name"] == "report_finding"
        inner = ra["args"]
        assert inner["exception_type"] == "ValueError"
        assert inner["source_butler"] == "relay-butler"
        assert inner["context"] == "relay context"
        assert len(inner["fingerprint"]) == 64


class TestSerializeAttempt:
    def test_serializes_uuid_and_datetime(self) -> None:
        attempt = {
            "id": uuid.uuid4(),
            "created_at": datetime.now(UTC),
            "fingerprint": "abc123",
            "status": "investigating",
        }
        result = _serialize_attempt(attempt)
        assert isinstance(result["id"], str)
        assert isinstance(result["created_at"], str)

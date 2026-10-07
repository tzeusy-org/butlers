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

import asyncio
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from butlers.core.spawn_hooks import clear_spawner
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
        assert cfg.model_dump() == {
            "enabled": True,
            "severity_threshold": 2,
            "max_concurrent": 2,
            "cooldown_minutes": 60,
            "circuit_breaker_threshold": 5,
            "timeout_minutes": 30,
        }

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
        assert set(registered) == {"report_error", "get_healing_status"}

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
        """Actual MCP closures/serialization and QA buffer; routing is synthetic. REQ-core-modules-003"""
        from butlers.config import ButlerConfig
        from butlers.core.qa.sources.butler_reports import ButlerReportsSource
        from butlers.daemon import ButlerDaemon, LocalSwitchboardClient
        from butlers.modules.qa import QaModule

        qa = QaModule()
        qa._butler_reports_source = ButlerReportsSource()
        qa_mcp = FastMCP("qa")
        await qa.register_tools(qa_mcp, None, None, "qa")
        switchboard = FastMCP("switchboard")
        route_calls = []
        wait_after_accept = False

        @switchboard.tool()
        async def list_butlers() -> list[dict]:
            return [{"name": "qa"}]

        @switchboard.tool()
        async def route(
            target_butler: str, tool_name: str, args: dict, allow_stale: bool = False
        ) -> dict:
            assert target_butler == "qa" and tool_name == "report_finding" and allow_stale
            route_calls.append(args)
            async with Client(qa_mcp) as target:
                response = (await target.call_tool(tool_name, args)).data
            if wait_after_accept:
                await asyncio.Event().wait()
            return {"result": response}

        for identity in ("relay-butler", "switchboard", "qa"):
            mod = SelfHealingModule()
            mcp = FastMCP(identity)
            await mod.register_tools(mcp, None, None, identity)
            if identity == "switchboard":
                # Exercise the actual owned daemon wiring seam, without an external client.
                daemon = ButlerDaemon(Path("/synthetic"))
                daemon.config = ButlerConfig(name="switchboard", port=18999)
                daemon.spawner = MagicMock()
                daemon.mcp = switchboard
                daemon._modules = [mod]
                daemon._wire_module_runtime()
                assert isinstance(mod._switchboard_client, LocalSwitchboardClient)
            else:
                # Per-call real in-process MCP transport, never a canned CallToolResult.
                mod.wire_runtime(MagicMock(), "/synthetic", LocalSwitchboardClient(switchboard))
            try:
                async with Client(mcp) as source:
                    report = {
                        "error_type": "ValueError",
                        "error_message": "synthetic relay",
                        "call_site": "test.py:run",
                        "context": "synthetic reasoning",
                    }
                    accepted = (await source.call_tool("report_error", report)).data
                    assert accepted["accepted"] is True and "attempt_id" not in accepted
                    received = await qa._butler_reports_source.discover(lookback_minutes=15)
                    assert len(received) == 1 and received[0].source_butler == identity
                    assert received[0].fingerprint == accepted["fingerprint"]
                    assert received[0].source_session_trigger_source is None
                    assert route_calls[-1]["context"] == "synthetic reasoning"
                    assert route_calls[-1]["severity"] == received[0].severity
                    # Genuine registered QA rejection, not an invented response dict.
                    buffer = qa._butler_reports_source
                    qa._butler_reports_source = None
                    refused = (await source.call_tool("report_error", report)).data
                    assert refused["accepted"] is False and refused["reason"] == "relay_failed"
                    qa._butler_reports_source = buffer
                    assert await buffer.discover(lookback_minutes=15) == []
                    if identity == "switchboard":
                        # Timeout AFTER reception is uncertain; retain the buffered positive.
                        wait_after_accept = True
                        before = len(route_calls)
                        started = time.monotonic()
                        timed = (await source.call_tool("report_error", report)).data
                        assert timed["accepted"] is False and timed["reason"] == "relay_timeout"
                        assert time.monotonic() - started < 2.5
                        assert len(route_calls) == before + 1
                        assert len(await buffer.discover(lookback_minutes=15)) == 1
                        wait_after_accept = False
            finally:
                clear_spawner()


class TestRelayLifecycle:
    async def test_retry_tool_is_absent_with_surviving_tool_positive(self) -> None:
        mod = _make_module()
        mcp = FastMCP("relay-only")
        await mod.register_tools(mcp, None, None, "relay-butler")
        async with Client(mcp) as client:
            assert {t.name for t in await client.list_tools()} == {
                "report_error",
                "get_healing_status",
            }
            assert (await client.call_tool("get_healing_status", {})).data["attempts"] == []
            with pytest.raises(ToolError):
                await client.call_tool("retry_healing", {"attempt_id": str(uuid.uuid4())})

    async def test_startup_shutdown_do_not_recover_or_reap(self, monkeypatch) -> None:
        recovery = AsyncMock(return_value=0)
        reaper = AsyncMock(return_value=[])
        for name, counter in (
            ("recover_stale_attempts", recovery),
            ("reap_stale_worktrees", reaper),
        ):
            monkeypatch.setattr(f"butlers.modules.self_healing.{name}", counter, raising=False)
            await counter()
            counter.reset_mock()
        pool = MagicMock()
        mod = _make_module()
        await mod.on_startup(SelfHealingConfig(), MagicMock(pool=pool))
        await mod.on_shutdown()
        assert mod._pool is pool
        recovery.assert_not_awaited()
        reaper.assert_not_awaited()

    async def test_disabled_and_legacy_keys_cannot_enable_dispatch(self) -> None:
        """REQ-core-spawner-007: accepted legacy keys cannot supply dispatch authority."""
        route_calls = []

        class RelayClient:
            async def call_tool(self, name, arguments):
                if name == "list_butlers":
                    return [{"name": "qa"}]
                route_calls.append(arguments)
                return {"result": {"accepted": True}}

        # Extreme legacy thresholds remain accepted, inert compatibility input.
        cfg = SelfHealingConfig(
            severity_threshold=-1,
            max_concurrent=0,
            cooldown_minutes=999,
            circuit_breaker_threshold=0,
            timeout_minutes=0,
        )
        mod = _make_module()
        await mod.on_startup(cfg, None)
        mcp = FastMCP("legacy-config-relay")
        await mod.register_tools(mcp, cfg, None, "relay-butler")
        mod.wire_runtime(MagicMock(), "/synthetic", RelayClient())
        async with Client(mcp) as client:
            args = {"error_type": "ValueError", "error_message": "synthetic report"}
            assert (await client.call_tool("report_error", args)).data["accepted"] is True
            mod._config.enabled = False
            refused = (await client.call_tool("report_error", args)).data
            assert refused["accepted"] is False and refused["reason"] == "disabled"
        assert len(route_calls) == 1
        assert set(route_calls[0]["args"]) == {
            "fingerprint",
            "exception_type",
            "call_site",
            "severity",
            "event_summary",
            "source_butler",
        }


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

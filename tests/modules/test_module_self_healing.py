"""Core QA relay tests — behavioral contract only.

Replaces 53 tests with ~12 focused behavioral tests.

Covers:
- Retired module cannot be rediscovered
- Tool registration (report_error, get_healing_status)
- Tool sensitivity metadata
- report_error: unavailable QA returns an honest refusal
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

from butlers.core.qa.relay import QaRelay, _serialize_attempt
from butlers.core.spawn_hooks import clear_spawner
from butlers.modules.base import ToolMeta

pytestmark = pytest.mark.unit


def _make_module() -> QaRelay:
    return QaRelay("test-butler", None, lambda: None)


class TestModuleABC:
    def test_module_contract(self) -> None:
        """QA relay is core; discovery cannot re-enable the retired module."""
        from butlers.modules.registry import default_registry

        assert "self_healing" not in default_registry().available_modules


class TestToolRegistration:
    async def test_registers_expected_tools(self) -> None:
        mod = _make_module()
        registered: dict = {}
        mcp = MagicMock()
        mcp.tool.side_effect = lambda **kw: (
            lambda fn: registered.__setitem__(kw.get("name") or fn.__name__, fn) or fn
        )
        mod.register_tools(mcp)
        assert "report_error" in registered
        assert "get_healing_status" in registered
        assert set(registered) == {"report_error", "get_healing_status"}

    def test_tool_metadata_marks_sensitive_args(self) -> None:
        from butlers.daemon import ButlerDaemon

        daemon = ButlerDaemon(Path("/synthetic"))
        report_meta = daemon._collect_tool_metadata()["report_error"]
        assert isinstance(report_meta, ToolMeta)
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
        mod.register_tools(mcp)

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
            mcp = switchboard if identity == "switchboard" else FastMCP(identity)
            daemon = ButlerDaemon(Path("/synthetic"))
            daemon.config = ButlerConfig(name=identity, port=18999)
            daemon.db = MagicMock(pool=None)
            daemon.mcp = mcp
            daemon.switchboard_client = (
                None if identity == "switchboard" else LocalSwitchboardClient(switchboard)
            )
            daemon._register_core_tools()
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
        mod.register_tools(mcp)
        async with Client(mcp) as client:
            assert {t.name for t in await client.list_tools()} == {
                "report_error",
                "get_healing_status",
            }
            assert (await client.call_tool("get_healing_status", {})).data["attempts"] == []
            with pytest.raises(ToolError):
                await client.call_tool("retry_healing", {"attempt_id": str(uuid.uuid4())})

    async def test_startup_shutdown_do_not_recover_or_reap(self, monkeypatch) -> None:
        """Core construction/registration owns no investigation lifecycle or pool."""
        recovery, reaper = AsyncMock(), AsyncMock()
        monkeypatch.setattr("butlers.core.healing.tracking.recover_stale_attempts", recovery)
        monkeypatch.setattr("butlers.core.healing.worktree.reap_stale_worktrees", reaper)
        pool = MagicMock()
        relay = QaRelay("general", pool, lambda: None)
        relay.register_tools(FastMCP("no-recovery"))
        assert relay._pool is pool
        recovery.assert_not_awaited()
        reaper.assert_not_awaited()
        pool.close.assert_not_called()


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


async def test_core_qa_registration_without_module_or_core_groups():
    """Real daemon registry and MCP execution remain available without opt-in."""
    from butlers.config import ButlerConfig, RuntimeSeedConfig
    from butlers.daemon import ButlerDaemon

    daemon = ButlerDaemon(Path("/synthetic"))
    daemon.config = ButlerConfig(
        name="general", port=18999, runtime_seed=RuntimeSeedConfig(core_groups=())
    )
    daemon.db = MagicMock(pool=None)
    daemon.mcp = FastMCP("core-qa-without-module")
    daemon._modules = []
    daemon._register_core_tools()
    async with Client(daemon.mcp) as client:
        names = {t.name for t in await client.list_tools()}
        assert {"report_error", "get_healing_status"} <= names
        result = (
            await client.call_tool(
                "report_error", {"error_type": "ValueError", "error_message": "synthetic report"}
            )
        ).data
        assert result["accepted"] is False
        assert result["reason"] == "qa_unavailable"
    assert daemon._tool_module_map["report_error"] == "core"
    assert daemon._collect_tool_metadata()["report_error"].arg_sensitivities == {
        "error_message": True,
        "traceback": True,
        "context": True,
    }

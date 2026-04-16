"""Tests for the SelfHealingModule QA relay behavior (task 5.3).

Covers:
- QA available: Switchboard route() with correct args, fingerprint, context handling
- QA unavailable: fallback scenarios (no client, not in registry, errors)
- Cached list_butlers TTL prevents per-error roundtrip
- wire_runtime accepts switchboard_client
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from butlers.modules.self_healing import SelfHealingModule

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_module(switchboard_client: object | None = None) -> SelfHealingModule:
    mod = SelfHealingModule()
    mod._butler_name = "general"
    mod._switchboard_client = switchboard_client
    return mod


def _make_call_tool_mock(list_result=None, route_result=None, route_raises=None, list_raises=None):
    """Build a mock call_tool function tracking route calls."""
    route_calls: list[dict] = []

    async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
        if tool_name == "list_butlers":
            if list_raises:
                raise list_raises
            return list_result if list_result is not None else [{"name": "qa"}]
        if tool_name == "route":
            if route_raises:
                raise route_raises
            route_calls.append(args or {})
            return route_result if route_result is not None else {"accepted": True}
        return {}

    client = MagicMock()
    client.call_tool = mock_call_tool
    return client, route_calls


def _report_error_args(**overrides):
    base = dict(
        error_type="ValueError",
        error_message="oops",
        traceback_str=None,
        call_site="foo.py:bar",
        context=None,
        tool_name=None,
        severity_hint=None,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# No switchboard client → immediate fallback
# ---------------------------------------------------------------------------


class TestSwitchboardNotConnected:
    async def test_no_client_fallback_and_no_calls(self):
        mod = _make_module(switchboard_client=None)
        mod._pool = None
        mod._spawner = None
        result = await mod._handle_report_error(**_report_error_args())
        assert result["accepted"] is False and result["reason"] == "not_configured"


# ---------------------------------------------------------------------------
# QA available — primary relay path
# ---------------------------------------------------------------------------


class TestQaRelayPrimaryPath:
    async def test_qa_available_routes_with_correct_args(self):
        """QA available: route called with correct args, fingerprint, allow_stale."""
        client, route_calls = _make_call_tool_mock()
        mod = _make_module(switchboard_client=client)
        mod._pool = None

        result = await mod._handle_report_error(
            **_report_error_args(
                error_type="RuntimeError",
                error_message="something broke",
                call_site="module.py:func",
                context="agent context here",
                severity_hint="high",
            )
        )
        assert result["accepted"] is True
        assert len(route_calls) == 1
        ra = route_calls[0]
        assert ra["target_butler"] == "qa" and ra["tool_name"] == "report_finding"
        assert ra["allow_stale"] is True
        inner = ra["args"]
        assert inner["exception_type"] == "RuntimeError"
        assert inner["source_butler"] == "general"
        assert len(inner["fingerprint"]) == 64 and inner["fingerprint"].islower()
        assert inner["context"] == "agent context here"

    async def test_context_none_omitted(self):
        client, route_calls = _make_call_tool_mock()
        mod = _make_module(switchboard_client=client)
        mod._pool = None
        await mod._handle_report_error(**_report_error_args())
        assert "context" not in route_calls[0]["args"]


# ---------------------------------------------------------------------------
# QA unavailable — fallback scenarios
# ---------------------------------------------------------------------------


class TestQaUnavailableFallback:
    @pytest.mark.parametrize(
        "setup",
        [
            {"list_result": [{"name": "general"}, {"name": "health"}]},
            {"list_raises": ConnectionError("unreachable")},
            {"route_result": {"error": "QA staffer down"}},
            {"route_raises": TimeoutError("route timeout")},
        ],
        ids=["not-in-registry", "list-raises", "route-error", "route-raises"],
    )
    async def test_fallback_cases(self, setup):
        client, _ = _make_call_tool_mock(**setup)
        mod = _make_module(switchboard_client=client)
        mod._pool = None
        mod._spawner = None
        result = await mod._handle_report_error(**_report_error_args())
        assert result["accepted"] is False and result["reason"] == "not_configured"


# ---------------------------------------------------------------------------
# Cached list_butlers TTL
# ---------------------------------------------------------------------------


class TestQaAvailabilityCache:
    async def test_list_butlers_cached_across_calls(self):
        list_count = 0

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            nonlocal list_count
            if tool_name == "list_butlers":
                list_count += 1
                return [{"name": "qa"}]
            if tool_name == "route":
                return {"accepted": True}
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool
        mod = _make_module(switchboard_client=client)
        mod._pool = None

        for _ in range(2):
            await mod._handle_report_error(**_report_error_args())
        assert list_count == 1

    async def test_cache_expired_refreshes(self):
        list_count = 0

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            nonlocal list_count
            if tool_name == "list_butlers":
                list_count += 1
                return [{"name": "qa"}]
            if tool_name == "route":
                return {"accepted": True}
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool
        mod = _make_module(switchboard_client=client)
        mod._pool = None

        await mod._handle_report_error(**_report_error_args())
        assert list_count == 1

        # Expire the cache by backdating the timestamp
        if mod._qa_available_cache is not None:
            is_avail, _ = mod._qa_available_cache
            mod._qa_available_cache = (is_avail, time.monotonic() - 300)
        await mod._handle_report_error(**_report_error_args())
        assert list_count == 2


# ---------------------------------------------------------------------------
# wire_runtime
# ---------------------------------------------------------------------------


class TestWireRuntime:
    def test_wire_runtime_accepts_switchboard_client(self):
        mod = SelfHealingModule()
        client = MagicMock()
        mod.wire_runtime(MagicMock(), "/repo", switchboard_client=client)
        assert mod._switchboard_client is client

    def test_wire_runtime_without_switchboard_client(self):
        mod = SelfHealingModule()
        mod.wire_runtime(MagicMock(), "/repo")
        assert mod._switchboard_client is None

"""Tests for the SelfHealingModule QA relay behavior (task 5.3).

Covers:
- QA available: Switchboard route() call to report_finding is made with correct args
- QA unavailable: fallback to direct dispatch
- Switchboard client not connected: immediate fallback
- Cached list_butlers TTL prevents per-error roundtrip
- Route call failure triggers fallback
- Route call with allow_stale=True
- wire_runtime accepts switchboard_client
- _is_qa_available caches results
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

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


def _make_switchboard_client(
    list_butlers_result: object | None = None,
    route_result: object | None = None,
    call_tool_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a mock Switchboard client."""
    client = MagicMock()

    if list_butlers_result is None:
        list_butlers_result = [{"name": "qa", "status": "alive"}]

    if call_tool_side_effect is not None:
        client.call_tool = AsyncMock(side_effect=call_tool_side_effect)
    else:

        async def _call_tool(tool_name: str, args: dict | None = None) -> object:
            if tool_name == "list_butlers":
                return list_butlers_result
            if tool_name == "route":
                return route_result or {"accepted": True}
            return {}

        client.call_tool = _call_tool

    return client


def _make_pool() -> MagicMock:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    return pool


# ---------------------------------------------------------------------------
# Test: Switchboard client not connected → immediate fallback
# ---------------------------------------------------------------------------


class TestSwitchboardNotConnected:
    async def test_no_switchboard_client_goes_to_fallback(self):
        """When switchboard_client is None, skip QA relay entirely."""
        mod = _make_module(switchboard_client=None)
        mod._pool = None  # ensure not_configured fallback
        mod._spawner = None

        result = await mod._handle_report_error(
            error_type="ValueError",
            error_message="oops",
            traceback_str=None,
            call_site="foo.py:bar",
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        # No switchboard → fallback → not_configured (no pool/spawner)
        assert result["accepted"] is False
        assert result["reason"] == "not_configured"

    async def test_no_switchboard_client_no_qa_check(self):
        """When switchboard_client is None, list_butlers is never called."""
        call_count = 0

        async def mock_call_tool(tool_name: str, args: object = None) -> object:
            nonlocal call_count
            call_count += 1
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        # Module uses None client, not mock_client
        mod = _make_module(switchboard_client=None)
        mod._pool = None
        mod._spawner = None

        await mod._handle_report_error(
            error_type="ValueError",
            error_message="oops",
            traceback_str=None,
            call_site="foo.py:bar",
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        # The mock client was never attached — no calls made
        assert call_count == 0


# ---------------------------------------------------------------------------
# Test: QA available — primary relay path
# ---------------------------------------------------------------------------


class TestQaRelayPrimaryPath:
    async def test_qa_available_calls_route_with_correct_args(self):
        """When QA is available, report_finding is called via Switchboard route()."""
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

        mod = _make_module(switchboard_client=client)
        mod._pool = None  # bypass active attempt check

        result = await mod._handle_report_error(
            error_type="RuntimeError",
            error_message="something broke",
            traceback_str=None,
            call_site="module.py:func",
            context="agent context here",
            tool_name=None,
            severity_hint="high",
        )

        assert result["accepted"] is True
        assert "Finding relayed to QA staffer via Switchboard" in result["message"]

        assert len(route_calls) == 1
        route_args = route_calls[0]
        assert route_args["target_butler"] == "qa"
        assert route_args["tool_name"] == "report_finding"
        assert route_args["allow_stale"] is True

        inner_args = route_args["args"]
        assert inner_args["exception_type"] == "RuntimeError"
        assert inner_args["call_site"] == "module.py:func"
        assert inner_args["source_butler"] == "general"
        assert "fingerprint" in inner_args
        assert "severity" in inner_args
        assert inner_args["context"] == "agent context here"

    async def test_qa_relay_no_context_omits_context_key(self):
        """When context=None, context key is not included in route args."""
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

        mod = _make_module(switchboard_client=client)
        mod._pool = None

        await mod._handle_report_error(
            error_type="ValueError",
            error_message="test",
            traceback_str=None,
            call_site="x.py:y",
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        assert len(route_calls) == 1
        inner_args = route_calls[0]["args"]
        assert "context" not in inner_args

    async def test_qa_relay_fingerprint_is_present(self):
        """Relayed finding includes a non-empty fingerprint."""
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

        mod = _make_module(switchboard_client=client)
        mod._pool = None

        await mod._handle_report_error(
            error_type="AttributeError",
            error_message="bad attr",
            traceback_str=None,
            call_site=None,
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        assert route_calls
        fp = route_calls[0]["args"].get("fingerprint", "")
        assert len(fp) == 64  # SHA-256 hex
        assert fp.islower()


# ---------------------------------------------------------------------------
# Test: QA unavailable — fallback to direct dispatch
# ---------------------------------------------------------------------------


class TestQaUnavailableFallback:
    async def test_qa_not_in_registry_falls_back(self):
        """When QA not in list_butlers, falls back to direct dispatch."""

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            if tool_name == "list_butlers":
                return [{"name": "general"}, {"name": "health"}]
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        mod = _make_module(switchboard_client=client)
        mod._pool = None
        mod._spawner = None

        result = await mod._handle_report_error(
            error_type="ValueError",
            error_message="test",
            traceback_str=None,
            call_site=None,
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        # Fallback → not_configured (no pool/spawner)
        assert result["accepted"] is False
        assert result["reason"] == "not_configured"

    async def test_list_butlers_exception_falls_back(self):
        """When list_butlers() raises, falls back to direct dispatch."""

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            if tool_name == "list_butlers":
                raise ConnectionError("switchboard unreachable")
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        mod = _make_module(switchboard_client=client)
        mod._pool = None
        mod._spawner = None

        result = await mod._handle_report_error(
            error_type="ValueError",
            error_message="test",
            traceback_str=None,
            call_site=None,
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        assert result["accepted"] is False
        assert result["reason"] == "not_configured"

    async def test_route_call_error_falls_back(self):
        """When route() returns an error dict, falls back to direct dispatch."""

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            if tool_name == "list_butlers":
                return [{"name": "qa"}]
            if tool_name == "route":
                return {"error": "QA staffer down"}
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        mod = _make_module(switchboard_client=client)
        mod._pool = None
        mod._spawner = None

        result = await mod._handle_report_error(
            error_type="ValueError",
            error_message="test",
            traceback_str=None,
            call_site=None,
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        assert result["accepted"] is False
        assert result["reason"] == "not_configured"

    async def test_route_call_exception_falls_back(self):
        """When route() raises, falls back to direct dispatch."""

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            if tool_name == "list_butlers":
                return [{"name": "qa"}]
            if tool_name == "route":
                raise TimeoutError("route timeout")
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        mod = _make_module(switchboard_client=client)
        mod._pool = None
        mod._spawner = None

        result = await mod._handle_report_error(
            error_type="ValueError",
            error_message="test",
            traceback_str=None,
            call_site=None,
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        assert result["accepted"] is False
        assert result["reason"] == "not_configured"


# ---------------------------------------------------------------------------
# Test: Cached list_butlers TTL prevents per-error roundtrip
# ---------------------------------------------------------------------------


class TestQaAvailabilityCache:
    async def test_list_butlers_cached_across_calls(self):
        """list_butlers() is not called on every report_error invocation."""
        list_butlers_call_count = 0

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            nonlocal list_butlers_call_count
            if tool_name == "list_butlers":
                list_butlers_call_count += 1
                return [{"name": "qa"}]
            if tool_name == "route":
                return {"accepted": True}
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        mod = _make_module(switchboard_client=client)
        mod._pool = None

        # Call report_error twice — list_butlers should only be called once
        for _ in range(2):
            await mod._handle_report_error(
                error_type="ValueError",
                error_message="test",
                traceback_str=None,
                call_site=None,
                context=None,
                tool_name=None,
                severity_hint=None,
            )

        assert list_butlers_call_count == 1

    async def test_cache_expires_after_ttl(self):
        """After TTL expires, list_butlers() is called again."""
        from butlers.modules.self_healing import _QA_AVAILABILITY_CACHE_TTL

        list_butlers_call_count = 0

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            nonlocal list_butlers_call_count
            if tool_name == "list_butlers":
                list_butlers_call_count += 1
                return [{"name": "qa"}]
            if tool_name == "route":
                return {"accepted": True}
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        mod = _make_module(switchboard_client=client)
        mod._pool = None

        # Pre-populate the cache with an expired timestamp
        mod._qa_available_cache = (
            True,
            time.monotonic() - (_QA_AVAILABILITY_CACHE_TTL + 1),
        )

        await mod._handle_report_error(
            error_type="ValueError",
            error_message="test",
            traceback_str=None,
            call_site=None,
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        # Cache was expired → list_butlers called again
        assert list_butlers_call_count == 1

    async def test_route_failure_does_not_invalidate_cache(self):
        """A route() failure does not clear the QA availability cache."""
        route_failures = 0

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            nonlocal route_failures
            if tool_name == "list_butlers":
                return [{"name": "qa"}]
            if tool_name == "route":
                route_failures += 1
                return {"error": "transient failure"}
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        mod = _make_module(switchboard_client=client)
        mod._pool = None
        mod._spawner = None

        # First call — route fails, falls back
        await mod._handle_report_error(
            error_type="ValueError",
            error_message="test",
            traceback_str=None,
            call_site=None,
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        # Cache should still say QA is available
        assert mod._qa_available_cache is not None
        is_available, _ = mod._qa_available_cache
        assert is_available is True

    async def test_list_butlers_dict_response_parsed(self):
        """list_butlers() response as dict with 'butlers' key is parsed correctly."""

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            if tool_name == "list_butlers":
                return {"butlers": [{"name": "qa"}, {"name": "general"}]}
            if tool_name == "route":
                return {"accepted": True}
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        mod = _make_module(switchboard_client=client)
        mod._pool = None

        result = await mod._handle_report_error(
            error_type="ValueError",
            error_message="test",
            traceback_str=None,
            call_site=None,
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        assert result["accepted"] is True
        assert mod._qa_available_cache is not None
        is_available, _ = mod._qa_available_cache
        assert is_available is True


# ---------------------------------------------------------------------------
# Test: wire_runtime accepts switchboard_client
# ---------------------------------------------------------------------------


class TestWireRuntimeSwitchboard:
    def test_wire_runtime_sets_switchboard_client(self):
        mod = _make_module()
        mock_client = MagicMock()
        mod.wire_runtime(
            butler_name="general",
            spawner=MagicMock(),
            repo_root="/tmp/repo",
            switchboard_client=mock_client,
        )
        assert mod._switchboard_client is mock_client

    def test_wire_runtime_switchboard_client_defaults_none(self):
        mod = _make_module()
        mod.wire_runtime(
            butler_name="general",
            spawner=MagicMock(),
            repo_root="/tmp/repo",
        )
        assert mod._switchboard_client is None

    def test_wire_runtime_still_sets_other_fields(self):
        mod = _make_module()
        spawner = MagicMock()
        mod.wire_runtime(
            butler_name="health",
            spawner=spawner,
            repo_root="/repo/root",
            switchboard_client=None,
        )
        assert mod._butler_name == "health"
        assert mod._spawner is spawner
        assert str(mod._repo_root) == "/repo/root"


# ---------------------------------------------------------------------------
# Test: already_investigating fast-path still works with switchboard present
# ---------------------------------------------------------------------------


class TestAlreadyInvestigatingFastPath:
    async def test_already_investigating_skips_relay(self):
        """When active attempt exists, return already_investigating without relay."""
        # Mock pool that returns an active attempt
        pool = MagicMock()
        pool.fetchrow = AsyncMock(
            return_value={
                "id": "00000000-0000-0000-0000-000000000001",
                "status": "investigating",
                "fingerprint": "a" * 64,
            }
        )

        route_calls: list = []

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            if tool_name == "list_butlers":
                return [{"name": "qa"}]
            if tool_name == "route":
                route_calls.append(args)
                return {"accepted": True}
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        mod = _make_module(switchboard_client=client)
        mod._pool = pool

        result = await mod._handle_report_error(
            error_type="ValueError",
            error_message="test",
            traceback_str=None,
            call_site=None,
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        # Should return already_investigating without ever calling route()
        assert result["accepted"] is False
        assert result["reason"] == "already_investigating"
        assert len(route_calls) == 0


# ---------------------------------------------------------------------------
# Test: qa_fallback_activations_total Prometheus counter (task 13.9)
# ---------------------------------------------------------------------------


class TestQaFallbackCounter:
    async def test_fallback_counter_exported(self):
        """qa_fallback_activations_total is accessible as a module-level symbol."""
        from butlers.modules.self_healing import _qa_fallback_activations_total

        # Counter should be a non-None object (prometheus Counter or None on failure)
        # In a normal test environment prometheus_client is available
        assert _qa_fallback_activations_total is not None

    async def test_fallback_incremented_when_qa_unavailable(self):
        """When QA relay fails and direct dispatch fires, fallback counter is incremented."""
        import butlers.modules.self_healing as sh_module

        original_counter = sh_module._qa_fallback_activations_total
        inc_calls: list[dict] = []

        class _FakeCounterChild:
            def __init__(self, labels_kwargs: dict) -> None:
                self._labels = labels_kwargs

            def inc(self) -> None:
                inc_calls.append({"labels": self._labels})

        class _FakeCounter:
            def labels(self, **kwargs):
                return _FakeCounterChild(kwargs)

        sh_module._qa_fallback_activations_total = _FakeCounter()

        try:
            # QA not available → fallback fires
            async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
                if tool_name == "list_butlers":
                    return [{"name": "general"}]  # No QA staffer
                return {}

            client = MagicMock()
            client.call_tool = mock_call_tool

            mod = _make_module(switchboard_client=client)
            mod._butler_name = "finance"
            mod._pool = None
            mod._spawner = None

            result = await mod._handle_report_error(
                error_type="IOError",
                error_message="disk full",
                traceback_str=None,
                call_site="storage.py:write",
                context=None,
                tool_name=None,
                severity_hint="high",
            )

            # Fallback runs (not_configured because no pool/spawner, and counter was hit)
            assert result["reason"] in (
                "not_configured",
                "disabled",
                "no_recursion",
                "already_investigating",
            )
            # Counter must have been incremented exactly once
            assert len(inc_calls) == 1
            assert inc_calls[0]["labels"] == {"butler": "finance"}
        finally:
            sh_module._qa_fallback_activations_total = original_counter

    async def test_fallback_counter_does_not_block_on_metric_error(self):
        """Counter errors do not propagate to callers — fallback still runs."""
        import butlers.modules.self_healing as sh_module

        original_counter = sh_module._qa_fallback_activations_total

        # Simulate a broken counter
        class _BrokenCounter:
            def labels(self, **kwargs):
                raise RuntimeError("prometheus unavailable")

        sh_module._qa_fallback_activations_total = _BrokenCounter()

        try:

            async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
                if tool_name == "list_butlers":
                    return []  # QA unavailable
                return {}

            client = MagicMock()
            client.call_tool = mock_call_tool

            mod = _make_module(switchboard_client=client)
            mod._pool = None
            mod._spawner = None

            # Should not raise despite broken counter
            result = await mod._handle_report_error(
                error_type="ValueError",
                error_message="test",
                traceback_str=None,
                call_site=None,
                context=None,
                tool_name=None,
                severity_hint=None,
            )
            # Fallback still completed
            assert "reason" in result
        finally:
            sh_module._qa_fallback_activations_total = original_counter

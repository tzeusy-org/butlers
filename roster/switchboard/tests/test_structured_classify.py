"""Tests for the structured tool-use classification fast lane (bu-qvnce.12
slice 3, bu-evus6).

Covers:
- mcp_server=None short-circuits to None without touching the pool.
- A resolved non-"api" runtime_type returns None immediately (CLI fallback).
- A quota-exhausted resolved entry returns None (no failover hunt).
- A valid tool_use decision is executed in-process via mcp_server.get_tool
  and returns a StructuredClassificationResult with the tool's real result
  attached — no MCP round trip, downstream shape unchanged.
- Schema-invalid tool_calls retry once, then fall back to None.
- An eligible adapter exception fails over to the next same-tier candidate;
  landing on a non-api candidate stops the loop and returns None.
- An ineligible adapter exception returns None immediately (no failover hunt).
- Token usage is recorded with purpose="classification" (not "discretion").
- The module reuses the shared failover classifier rather than forking one.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.model_routing import QuotaStatus
from butlers.tools.switchboard.routing import structured_classify as sc

pytestmark = pytest.mark.unit

_MODULE = "butlers.tools.switchboard.routing.structured_classify"


def _catalog(runtime_type: str = "api", tier: str = "cheap") -> tuple:
    return (runtime_type, "claude-haiku-4-5-20251001", [], uuid.uuid4(), 30, tier)


def _allowed_quota() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)


def _blocked_quota() -> QuotaStatus:
    return QuotaStatus(allowed=False, usage_24h=100, limit_24h=100, usage_30d=0, limit_30d=None)


def _route_call(butler: str = "health") -> dict:
    return {
        "id": "tu_1",
        "name": "route_to_butler",
        "input": {"butler": butler, "prompt": "help with this"},
    }


def _make_adapter(side_effect: list) -> MagicMock:
    adapter = MagicMock()
    adapter.invoke_structured = AsyncMock(side_effect=side_effect)
    adapter.last_process_info = None
    return adapter


class _FakeTool:
    def __init__(self, fn):
        self.fn = fn


def _make_mcp_server(tool_results: dict[str, dict]) -> MagicMock:
    """Build a fake FastMCP-like object exposing get_tool(name) -> tool.fn(**kw)."""
    mcp = MagicMock()

    def get_tool(name):
        async def _fn(**kwargs):
            return tool_results.get(name, {"status": "ok"})

        return _FakeTool(_fn)

    mcp.get_tool = MagicMock(side_effect=get_tool)
    return mcp


async def test_none_mcp_server_short_circuits_without_touching_pool() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=AssertionError("pool should not be queried"))
    result = await sc.try_structured_classification(
        pool, mcp_server=None, prompt="hi", include_bug_report=False
    )
    assert result is None


async def test_non_api_runtime_returns_none_without_calling_adapter() -> None:
    pool = MagicMock()
    mcp = _make_mcp_server({})
    with (
        patch(
            f"{_MODULE}.resolve_model_with_effective_tier",
            AsyncMock(return_value=_catalog(runtime_type="opencode")),
        ),
        patch(f"{_MODULE}.create_adapter") as mock_create_adapter,
    ):
        result = await sc.try_structured_classification(
            pool, mcp_server=mcp, prompt="hi", include_bug_report=False
        )
    assert result is None
    mock_create_adapter.assert_not_called()


async def test_quota_exhausted_returns_none_without_failover_hunt() -> None:
    pool = MagicMock()
    mcp = _make_mcp_server({})
    with (
        patch(
            f"{_MODULE}.resolve_model_with_effective_tier",
            AsyncMock(return_value=_catalog()),
        ),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_blocked_quota())),
        patch(f"{_MODULE}.next_same_tier_candidate") as mock_next,
    ):
        result = await sc.try_structured_classification(
            pool, mcp_server=mcp, prompt="hi", include_bug_report=False
        )
    assert result is None
    mock_next.assert_not_called()


async def test_no_catalog_entry_returns_none() -> None:
    pool = MagicMock()
    mcp = _make_mcp_server({})
    with patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=None)):
        result = await sc.try_structured_classification(
            pool, mcp_server=mcp, prompt="hi", include_bug_report=False
        )
    assert result is None


async def test_valid_decision_executes_in_process_and_returns_result() -> None:
    pool = MagicMock()
    adapter = _make_adapter(
        side_effect=[
            (
                [_route_call("health")],
                "routing to health",
                {"input_tokens": 10, "output_tokens": 5},
            )
        ]
    )
    mcp = _make_mcp_server({"route_to_butler": {"status": "accepted", "butler": "health"}})

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=_catalog())),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch(f"{_MODULE}.create_adapter", return_value=adapter),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()) as mock_record,
    ):
        result = await sc.try_structured_classification(
            pool,
            mcp_server=mcp,
            prompt="I have a headache",
            include_bug_report=False,
            butler_name="switchboard",
        )

    assert result is not None
    assert result.model == "claude-haiku-4-5-20251001"
    assert result.tool_calls == [
        {
            "id": "tu_1",
            "name": "route_to_butler",
            "input": {"butler": "health", "prompt": "help with this"},
            "result": {"status": "accepted", "butler": "health"},
        }
    ]
    mock_record.assert_awaited_once()
    _, kwargs = mock_record.call_args
    assert kwargs["purpose"] == "classification"
    assert kwargs["session_id"] is None
    assert kwargs["butler_name"] == "switchboard"


async def test_valid_decision_executes_sync_tool_fn_without_typeerror() -> None:
    """Regression guard: a registered tool whose ``.fn`` is a plain sync
    callable (not a coroutine function) must still execute correctly —
    ``_execute_tool_call`` must not blindly ``await fn(**kwargs)``, which
    would raise ``TypeError: object dict can't be used in 'await' expression``
    for a sync function.
    """
    pool = MagicMock()
    adapter = _make_adapter(
        side_effect=[
            (
                [_route_call("health")],
                "routing to health",
                {"input_tokens": 10, "output_tokens": 5},
            )
        ]
    )

    mcp = MagicMock()

    def _sync_fn(**kwargs):
        return {"status": "accepted", "butler": "health"}

    mcp.get_tool = MagicMock(side_effect=lambda name: _FakeTool(_sync_fn))

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=_catalog())),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch(f"{_MODULE}.create_adapter", return_value=adapter),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        result = await sc.try_structured_classification(
            pool,
            mcp_server=mcp,
            prompt="I have a headache",
            include_bug_report=False,
            butler_name="switchboard",
        )

    assert result is not None
    assert result.tool_calls[0]["result"] == {"status": "accepted", "butler": "health"}


async def test_include_bug_report_offers_both_tools() -> None:
    pool = MagicMock()
    captured_tools = {}

    async def _invoke_structured(*, tools, **kwargs):
        captured_tools["tools"] = tools
        return ([_route_call("health")], None, {"input_tokens": 1, "output_tokens": 1})

    adapter = MagicMock()
    adapter.invoke_structured = AsyncMock(side_effect=_invoke_structured)
    mcp = _make_mcp_server({})

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=_catalog())),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch(f"{_MODULE}.create_adapter", return_value=adapter),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        await sc.try_structured_classification(
            pool, mcp_server=mcp, prompt="hi", include_bug_report=True
        )

    names = {t["name"] for t in captured_tools["tools"]}
    assert names == {"route_to_butler", "file_bug_report"}


async def test_schema_invalid_retries_once_then_falls_back_to_none() -> None:
    pool = MagicMock()
    # Both attempts return schema-invalid output (missing "prompt").
    invalid_call = {"id": "tu_1", "name": "route_to_butler", "input": {"butler": "health"}}
    adapter = _make_adapter(
        side_effect=[
            ([invalid_call], None, {"input_tokens": 1, "output_tokens": 1}),
            ([invalid_call], None, {"input_tokens": 1, "output_tokens": 1}),
        ]
    )
    mcp = _make_mcp_server({})

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=_catalog())),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch(f"{_MODULE}.create_adapter", return_value=adapter),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        result = await sc.try_structured_classification(
            pool, mcp_server=mcp, prompt="hi", include_bug_report=False
        )

    assert result is None
    assert adapter.invoke_structured.await_count == 2


async def test_schema_invalid_then_valid_retry_succeeds() -> None:
    pool = MagicMock()
    invalid_call = {"id": "tu_1", "name": "route_to_butler", "input": {"butler": "health"}}
    valid_call = _route_call("health")
    adapter = _make_adapter(
        side_effect=[
            ([invalid_call], None, {"input_tokens": 1, "output_tokens": 1}),
            ([valid_call], None, {"input_tokens": 1, "output_tokens": 1}),
        ]
    )
    mcp = _make_mcp_server({"route_to_butler": {"status": "accepted", "butler": "health"}})

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=_catalog())),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch(f"{_MODULE}.create_adapter", return_value=adapter),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        result = await sc.try_structured_classification(
            pool, mcp_server=mcp, prompt="hi", include_bug_report=False
        )

    assert result is not None
    assert result.tool_calls[0]["input"] == {"butler": "health", "prompt": "help with this"}


async def test_eligible_exception_fails_over_to_next_api_candidate() -> None:
    """Same runtime_type ("api") means the adapter is cached and reused across
    same-tier failover attempts (mirrors DiscretionDispatcher's
    ``_get_or_create_adapter``) — only the resolved ``model_id`` changes
    between attempts, passed per-call to ``invoke_structured``.
    """
    pool = MagicMock()
    adapter = _make_adapter(
        side_effect=[
            RuntimeError("connection error"),
            ([_route_call("health")], None, {"input_tokens": 2, "output_tokens": 2}),
        ]
    )
    mcp = _make_mcp_server({"route_to_butler": {"status": "accepted", "butler": "health"}})

    next_candidate = ("api", "claude-haiku-fallback", [], uuid.uuid4(), 30)

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=_catalog())),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch(f"{_MODULE}.create_adapter", return_value=adapter),
        patch(f"{_MODULE}.next_same_tier_candidate", AsyncMock(return_value=next_candidate)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        result = await sc.try_structured_classification(
            pool, mcp_server=mcp, prompt="hi", include_bug_report=False
        )

    assert result is not None
    assert result.model == "claude-haiku-fallback"
    assert adapter.invoke_structured.await_count == 2


async def test_failover_landing_on_non_api_candidate_returns_none() -> None:
    pool = MagicMock()
    adapter = _make_adapter(side_effect=[RuntimeError("connection error")])
    non_api_candidate = ("opencode", "some-model", [], uuid.uuid4(), 30)

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=_catalog())),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch(f"{_MODULE}.create_adapter", return_value=adapter),
        patch(f"{_MODULE}.next_same_tier_candidate", AsyncMock(return_value=non_api_candidate)),
    ):
        result = await sc.try_structured_classification(
            pool, mcp_server=_make_mcp_server({}), prompt="hi", include_bug_report=False
        )

    assert result is None


async def test_ineligible_exception_returns_none_without_failover_hunt() -> None:
    """A business/validation error (e.g. ValueError) is not failover-eligible."""
    pool = MagicMock()
    adapter = _make_adapter(side_effect=[ValueError("bad input")])

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=_catalog())),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch(f"{_MODULE}.create_adapter", return_value=adapter),
        patch(f"{_MODULE}.next_same_tier_candidate") as mock_next,
    ):
        result = await sc.try_structured_classification(
            pool, mcp_server=_make_mcp_server({}), prompt="hi", include_bug_report=False
        )

    assert result is None
    mock_next.assert_not_called()


async def test_failover_exhausted_returns_none() -> None:
    pool = MagicMock()
    adapter = _make_adapter(side_effect=[RuntimeError("connection error")])

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=_catalog())),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch(f"{_MODULE}.create_adapter", return_value=adapter),
        patch(f"{_MODULE}.next_same_tier_candidate", AsyncMock(return_value=None)),
    ):
        result = await sc.try_structured_classification(
            pool, mcp_server=_make_mcp_server({}), prompt="hi", include_bug_report=False
        )

    assert result is None


def test_reuses_shared_failover_classifier() -> None:
    """Must import and call the SAME classifier Spawner._run() /
    DiscretionDispatcher.call() use, not a forked/duplicate implementation.
    """
    from butlers.core import failover_classifier

    assert sc.classify_failover_eligibility is failover_classifier.classify_failover_eligibility


class TestValidateToolCall:
    def test_route_to_butler_requires_butler_and_prompt(self):
        assert sc._validate_tool_call(_route_call("health")) is True
        assert (
            sc._validate_tool_call({"name": "route_to_butler", "input": {"butler": "health"}})
            is False
        )
        assert (
            sc._validate_tool_call({"name": "route_to_butler", "input": {"prompt": "x"}}) is False
        )

    def test_route_to_butler_rejects_bad_complexity(self):
        call = {
            "name": "route_to_butler",
            "input": {"butler": "health", "prompt": "x", "complexity": "invalid"},
        }
        assert sc._validate_tool_call(call) is False

    def test_route_to_butler_accepts_canonical_complexity(self):
        """The classification schema only ever offers canonical tiers (bu-h3cwc)."""
        for tier in ("reasoning", "workhorse", "cheap", "specialty", "local", "legacy"):
            call = {
                "name": "route_to_butler",
                "input": {"butler": "health", "prompt": "x", "complexity": tier},
            }
            assert sc._validate_tool_call(call) is True

    def test_route_to_butler_rejects_retired_complexity_vocabulary(self):
        """Retired pre-core_092 values (e.g. "medium") are schema-invalid here — the
        forced tool-use schema's enum only lists canonical tiers, so a model
        emitting the old vocabulary triggers the existing retry/CLI-fallback
        path rather than dispatching with a stale value (bu-h3cwc)."""
        for legacy in ("trivial", "medium", "high", "extra_high", "discretion", "self_healing"):
            call = {
                "name": "route_to_butler",
                "input": {"butler": "health", "prompt": "x", "complexity": legacy},
            }
            assert sc._validate_tool_call(call) is False

    def test_file_bug_report_requires_summary(self):
        assert (
            sc._validate_tool_call({"name": "file_bug_report", "input": {"summary": "broken"}})
            is True
        )
        assert sc._validate_tool_call({"name": "file_bug_report", "input": {}}) is False

    def test_file_bug_report_rejects_out_of_range_severity(self):
        call = {"name": "file_bug_report", "input": {"summary": "x", "severity": 9}}
        assert sc._validate_tool_call(call) is False

    def test_unknown_tool_name_rejected(self):
        assert sc._validate_tool_call({"name": "shell_exec", "input": {}}) is False

    def test_empty_tool_calls_list_invalid(self):
        assert sc._validate_tool_calls([]) is False

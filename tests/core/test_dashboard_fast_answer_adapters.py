"""Provider selection and actual registered target boundary; no provider or SQL calls."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastmcp import Client, FastMCP

from butlers.core import dashboard_fast_answer_adapters as adapters
from butlers.core.dashboard_fast_answer_catalog import validate_catalog_evidence
from butlers.core.dashboard_fast_answer_policy import FAST_ANSWER_CONCIERGE_TOOLS_V1
from butlers.core.dashboard_fast_answer_runtime import InvalidAdmission, source_envelope
from butlers.core.dashboard_fast_answer_store import FastAnswerFence
from butlers.modules.dashboard_read import DashboardReadModule

pytestmark = pytest.mark.unit


class _AdapterStore:
    def __init__(self):
        self.pool = object()
        self.attribution = []

    async def control(self, fence, action, data):
        self.attribution.append((fence, action, deepcopy(data)))
        return {"outcome": "accepted"}


async def test_exact_both_cheap_api_candidates_and_quota_are_resolved_before_any_call(monkeypatch):
    # REQ-dashboard-fast-answer-006: no static fallback, tier fallthrough or CLI.
    store = _AdapterStore()
    deps = adapters.RegisteredFastAnswerDependencies(SimpleNamespace(), store)
    fence = FastAnswerFence(uuid4(), uuid4(), uuid4(), 1)
    entry = uuid4()
    valid = ("api", "stub-model", [], entry, 47, "cheap")
    resolver = AsyncMock(return_value=valid)
    quota = AsyncMock(return_value=SimpleNamespace(allowed=True))
    monkeypatch.setattr(adapters, "resolve_model_with_effective_tier", resolver)
    monkeypatch.setattr(adapters, "check_token_quota", quota)
    models = await deps.models(fence)
    assert len(models) == 2
    assert [item.purpose for item in models] == [
        "dashboard_fast_answer_classification",
        "dashboard_fast_answer_phrasing",
    ]
    assert all(item.timeout == 47 and item.entry_id == entry for item in models)
    assert resolver.await_count == quota.await_count == 2
    assert all(call.kwargs["allow_tier_fallthrough"] is False for call in resolver.await_args_list)
    assert all(call.kwargs["quota_aware"] is True for call in resolver.await_args_list)
    assert store.attribution == [] and deps._adapter is None
    for bad in (
        None,
        ("claude", *valid[1:]),
        (*valid[:3], None, 47, "cheap"),
        (*valid[:4], True, "cheap"),
        (*valid[:5], "workhorse"),
    ):
        resolver.reset_mock()
        resolver.side_effect = [valid, bad]
        assert await deps.models(fence) is None
        assert resolver.await_count == 2
        assert deps._adapter is None
    resolver.side_effect = None
    quota.return_value = SimpleNamespace(allowed=False)
    assert await deps.models(fence) is None


async def test_registered_concierge_handler_validation_state_and_route_fences(monkeypatch):
    # REQ-dashboard-fast-answer-004: real FastMCP/tool validation and owning query.
    from importlib import import_module

    routing = import_module("butlers.tools.switchboard.routing.route")

    target_pool = SimpleNamespace(
        fetch=AsyncMock(
            return_value=[
                {
                    "butler": "general",
                    "sessions_24h": 2,
                    "running_count": 1,
                    "failed_24h": 0,
                    "last_session_at": None,
                }
            ]
        )
    )
    mcp = FastMCP("concierge-boundary")
    module = DashboardReadModule()
    await module.register_tools(mcp, {}, SimpleNamespace(pool=target_pool), "concierge")
    state = {"dashboard_read": {"health": "active", "enabled": True}}

    @mcp.tool(name="module.states")
    async def module_states():
        return state

    @mcp.tool()
    async def dashboard_read_future_write():
        raise AssertionError("presentation must never grant this call")

    endpoint = "http://localhost:9999/mcp"
    resolver = AsyncMock(return_value=({"endpoint_url": endpoint}, None))
    monkeypatch.setattr(routing, "receiver_route_cutover_enabled", lambda: False)
    monkeypatch.setattr(routing, "resolve_routing_target", resolver)
    pool = SimpleNamespace(execute=AsyncMock())
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert set(FAST_ANSWER_CONCIERGE_TOOLS_V1) <= set(tools)
        target = adapters.ConciergeTarget(pool, endpoint, client, tools, True)
        result = await target.read("dashboard_read_fleet_status", {})
        assert result["butlers"][0]["sessions_24h"] == 2
        assert source_envelope(result)["ref"] == "concierge.v_fleet_sessions"
        assert target_pool.fetch.await_count == 1
        assert resolver.await_args.args[1] == "concierge"
        for name, arguments in (
            ("dashboard_read_future_write", {}),
            ("dashboard_read_butler_detail", {}),
            ("dashboard_read_fleet_status", {"caller_authority": True}),
        ):
            with pytest.raises(RuntimeError):
                await target.read(name, arguments)
            assert target_pool.fetch.await_count == 1
        state["dashboard_read"]["enabled"] = False
        with pytest.raises(RuntimeError, match="module disabled"):
            await target.read("dashboard_read_fleet_status", {})
        assert target_pool.fetch.await_count == 1
        state["dashboard_read"]["enabled"] = True
        resolver.return_value = ({"endpoint_url": "http://localhost:9998/mcp"}, None)
        with pytest.raises(RuntimeError):
            await target.read("dashboard_read_fleet_status", {})
        assert target_pool.fetch.await_count == 1
        resolver.return_value = ({"endpoint_url": endpoint}, None)
        assert (await target.read("dashboard_read_fleet_status", {}))["butlers"]
        assert target_pool.fetch.await_count == 2


async def test_api_classifier_exposes_only_schema_and_phraser_is_tool_free(monkeypatch):
    # REQ-dashboard-fast-answer-001 / REQ-dashboard-fast-answer-006.
    store = _AdapterStore()
    deps = adapters.RegisteredFastAnswerDependencies(SimpleNamespace(), store)
    fence = FastAnswerFence(uuid4(), uuid4(), uuid4(), 1)
    models = tuple(
        adapters._Model("stub", uuid4(), 31, purpose, fence)
        for purpose in ("dashboard_fast_answer_classification", "dashboard_fast_answer_phrasing")
    )
    admission = {
        "lane": "D",
        "scope": "system",
        "selected_owner": "concierge",
        "question": "How is the fleet?",
        "reads": [],
    }
    api = SimpleNamespace(
        invoke_structured=AsyncMock(
            return_value=(
                [{"name": "dashboard_fast_admission", "input": admission}],
                None,
                {"input_tokens": 3, "output_tokens": 2},
            )
        ),
        invoke=AsyncMock(return_value=("Healthy.", [], {"input_tokens": 3, "output_tokens": 2})),
    )
    deps._adapter = api
    usage = AsyncMock()
    monkeypatch.setattr(adapters, "record_token_usage", usage)
    catalog = validate_catalog_evidence([], eligible_owners=frozenset({"concierge"}))
    target = SimpleNamespace(tools={})
    assert await deps.classify(models, "question", catalog, target) == admission
    kwargs = api.invoke_structured.await_args.kwargs
    assert [tool["name"] for tool in kwargs["tools"]] == ["dashboard_fast_admission"]
    assert kwargs["timeout"] == 31 and kwargs["model"] == "stub"
    assert "cannot_answer" not in str(kwargs["tools"])
    assert await deps.phrase(models, "question", []) == "Healthy."
    assert api.invoke.await_args.kwargs["mcp_servers"] == {}
    assert [event[2]["purpose"] for event in store.attribution] == [
        models[0].purpose,
        models[0].purpose,
        models[1].purpose,
        models[1].purpose,
    ]
    assert usage.await_count == 2
    api.invoke_structured.return_value = ([{"name": "conversation_reply", "input": {}}], None, None)
    with pytest.raises(InvalidAdmission):
        await deps.classify(models, "question", catalog, target)
    api.invoke.return_value = ("fake answer", [{"name": "write", "input": {}}], None)
    with pytest.raises(RuntimeError):
        await deps.phrase(models, "question", [])
    for source in (
        {},
        {"kind": "view", "ref": "x", "as_of": "unknown"},
        {"kind": "view", "ref": "x", "as_of": "2026-10-12"},
    ):
        with pytest.raises(ValueError):
            source_envelope({"source": source})

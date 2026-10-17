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
    attempts = [data for _, _, data in store.attribution]
    assert [data["outcome"] for data in attempts] == ["started", "received"] * 2
    assert all(type(data["duration_ms"]) is int and data["duration_ms"] >= 0 for data in attempts)
    assert all(
        set(data)
        == {
            "purpose",
            "requested_tier",
            "effective_tier",
            "runtime_type",
            "resolution_source",
            "model",
            "catalog_entry_id",
            "execution_timeout",
            "outcome",
            "duration_ms",
        }
        for data in attempts
    )
    assert all(fence_value == fence for fence_value, _, _ in store.attribution)
    api.invoke_structured.return_value = ([{"name": "conversation_reply", "input": {}}], None, None)
    with pytest.raises(InvalidAdmission):
        await deps.classify(models, "question", catalog, target)
    api.invoke.return_value = ("fake answer", [{"name": "write", "input": {}}], None)
    with pytest.raises(RuntimeError):
        await deps.phrase(models, "question", [])
    clock = [10.0]
    monkeypatch.setattr(adapters, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def settled_provider(**kwargs):
        clock[0] += 0.25
        return ([{"name": "dashboard_fast_admission", "input": admission}], None, None)

    api.invoke_structured.side_effect = settled_provider
    assert await deps.classify(models, "question", catalog, target) == admission
    assert store.attribution[-1][2]["duration_ms"] == 250

    async def failed_provider(**kwargs):
        clock[0] += 0.5
        raise RuntimeError("synthetic provider failure")

    api.invoke_structured.side_effect = failed_provider
    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        await deps.classify(models, "question", catalog, target)
    assert store.attribution[-1][2]["outcome"] == "failed"
    assert store.attribution[-1][2]["duration_ms"] == 500
    assert store.attribution[-1][0] == fence
    for source in (
        {},
        {"kind": "view", "ref": "x", "as_of": "unknown"},
        {"kind": "view", "ref": "x", "as_of": "2026-10-12"},
    ):
        with pytest.raises(ValueError):
            source_envelope({"source": source})


async def test_fast_factory_disables_sdk_retries_without_changing_shared_adapter(monkeypatch):
    # REQ-dashboard-fast-answer-006: transport failure is one attributed attempt.
    # This exercises the real factory, ApiAdapter, installed SDK and HTTP transport.
    import anthropic
    import httpx

    from butlers.core.dashboard_fast_answer_catalog import CatalogOutcome
    from butlers.core.runtimes.api import ApiAdapter

    real_client = anthropic.AsyncAnthropic
    monkeypatch.setattr(adapters, "record_token_usage", AsyncMock())

    class Credentials:
        async def load(self, key):
            assert key == "cli-auth/claude"
            return "local-test-placeholder"

    async def run_case(phase, response_kind, *, ordinary=False):
        transmissions, clients = [], []

        def respond(request):
            transmissions.append(request.method)
            if response_kind == "connection":
                raise httpx.ConnectError("synthetic connection failure", request=request)
            if response_kind in ("429", "500"):
                return httpx.Response(
                    int(response_kind),
                    headers={"retry-after-ms": "1"},
                    json={
                        "type": "error",
                        "error": {"type": "rate_limit_error", "message": "synthetic failure"},
                    },
                )
            name = (
                "unrecognized_admission"
                if response_kind == "invalid-then-valid" and len(transmissions) == 1
                else "dashboard_fast_admission"
            )
            content = (
                [
                    {
                        "type": "tool_use",
                        "id": "tool-stub",
                        "name": name,
                        "input": {
                            "lane": "D",
                            "scope": "system",
                            "selected_owner": "concierge",
                            "question": "synthetic",
                            "reads": [],
                        },
                    }
                ]
                if phase == "classification"
                else [{"type": "text", "text": "synthetic answer"}]
            )
            return httpx.Response(
                200,
                json={
                    "id": "msg-stub",
                    "type": "message",
                    "role": "assistant",
                    "model": "stub",
                    "content": content,
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        def sdk_factory(**kwargs):
            client = real_client(
                **kwargs,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
            )
            clients.append(client)
            return client

        monkeypatch.setattr(anthropic, "AsyncAnthropic", sdk_factory)
        credentials = Credentials()
        store = _AdapterStore()
        deps = adapters.RegisteredFastAnswerDependencies(
            SimpleNamespace(_credential_store=credentials), store
        )
        fence = FastAnswerFence(uuid4(), uuid4(), uuid4(), 1)
        models = tuple(
            adapters._Model("stub", uuid4(), 4, purpose, fence)
            for purpose in (
                "dashboard_fast_answer_classification",
                "dashboard_fast_answer_phrasing",
            )
        )

        async def call():
            if ordinary:
                return await ApiAdapter(credential_store=credentials).invoke(
                    "synthetic", "synthetic", {}, {}, model="stub", timeout=4
                )
            if phase == "classification":
                return await deps.classify(
                    models, "private operand", CatalogOutcome("no_match"), SimpleNamespace(tools={})
                )
            return await deps.phrase(models, "private operand", [])

        try:
            if response_kind in ("429", "500", "connection"):
                with pytest.raises(RuntimeError):
                    await call()
                expected_outcomes = ["started", "failed"]
            elif response_kind == "invalid-then-valid":
                with pytest.raises(InvalidAdmission):
                    await call()
                assert await call()
                expected_outcomes = ["started", "received"] * 2
            else:
                assert await call()
                expected_outcomes = ["started", "received"]
            expected_sends = 3 if ordinary else 2 if response_kind == "invalid-then-valid" else 1
            assert len(transmissions) == expected_sends
            assert transmissions == ["POST"] * expected_sends
            # Fast configuration never mutates the base client's ordinary SDK default.
            assert len(clients) == 1 and clients[0].max_retries == 2
            if not ordinary:
                assert [data["outcome"] for _, _, data in store.attribution] == expected_outcomes
                assert all(record_fence == fence for record_fence, _, _ in store.attribution)
                assert all(
                    data["purpose"] == models[0 if phase == "classification" else 1].purpose
                    and type(data["duration_ms"]) is int
                    and data["duration_ms"] >= 0
                    and "private operand" not in str(data)
                    for _, _, data in store.attribution
                )
                assert isinstance(deps._api(), ApiAdapter) and deps._api() is deps._api()
        finally:
            for client in clients:
                await client.close()

    for phase in ("classification", "phrasing"):
        for response_kind in ("healthy", "429", "500", "connection"):
            await run_case(phase, response_kind)
    await run_case("classification", "invalid-then-valid")
    await run_case("phrasing", "429", ordinary=True)

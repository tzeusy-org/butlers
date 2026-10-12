"""Software lifetime controls; these fakes do not prove SQL races or ACLs."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastmcp import Client, FastMCP

from butlers.config import ButlerType
from butlers.core import memory_hooks
from butlers.core.dashboard_fast_answer_policy import FAST_ANSWER_CONCIERGE_TOOLS_V1
from butlers.core.dashboard_fast_answer_runtime import DashboardFastAnswerRuntime, InvalidAdmission
from butlers.core_tools._base import ToolContext
from butlers.core_tools._conversation_reply import register_conversation_reply_tool
from butlers.core_tools._sessions import register_session_tools

pytestmark = pytest.mark.unit


class _Store:
    def __init__(self):
        self.events = []
        self.stopped = False
        self.lost = False
        self.state = "active"
        self.lane = None
        self.reply = None

    async def register(self, message, request, session, owner):
        self.events.append(("register", session))
        return {"outcome": "stopped" if self.stopped else "registered", "lease_generation": 1}

    async def control(self, fence, action, data=None):
        self.events.append((action, deepcopy(data)))
        if self.lost:
            return {"outcome": "fenced"}
        if self.stopped and action not in {"cancelled", "uncertain"}:
            return {"outcome": "stopped"}
        if action in {"fallback", "failed", "cancelled", "uncertain"}:
            self.state = "pending_reconciliation" if action == "uncertain" else action
        if action == "reply":
            if self.reply is not None and self.reply != data:
                return {"outcome": "conflict"}
            self.reply = deepcopy(data)
            self.state = "completed"
        return {"outcome": self.state if self.state != "active" else "accepted"}

    async def intent(self, message, lane):
        if self.lane is not None and self.lane != lane:
            raise ValueError("intent conflict")
        self.lane = lane

    async def reconcile(self, owner):
        self.events.append(("sweep", owner))
        return []


class _Dependencies:
    def __init__(self, store):
        self.store = store
        self.calls = []
        self.fence = None
        self.runtime = None
        self.models_available = True
        self.classify_results = []
        self.hold_phase = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.source = {
            "kind": "view",
            "ref": "concierge.v_fleet_sessions",
            "as_of": "2026-10-12T00:00:00Z",
        }
        self.fail_phase = None
        self.admission = {
            "lane": "D",
            "scope": "system",
            "selected_owner": "concierge",
            "question": "How is the fleet?",
            "reads": [{"name": "dashboard_read_fleet_status"}],
        }
        self.tools = {name: None for name in FAST_ANSWER_CONCIERGE_TOOLS_V1}
        self.enabled = True

    async def _phase(self, phase):
        self.calls.append(phase)
        if self.hold_phase == phase:
            self.entered.set()
            await self.release.wait()
        if self.fail_phase == phase:
            raise RuntimeError("private error operand")

    async def models(self, fence):
        await self._phase("models")
        self.fence = fence
        return object() if self.models_available else None

    async def target(self):
        await self._phase("target")
        return self

    async def classify(self, models, prompt, catalog, target):
        await self._phase("classify")
        if self.classify_results:
            result = self.classify_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return self.admission

    async def read(self, name, arguments):
        await self._phase("read")
        return {"status": "healthy", "source": self.source}

    async def phrase(self, models, question, results):
        await self._phase("phrase")
        return "The fleet is healthy."

    async def reply(self, fence, payload):
        await self._phase("reply")
        return await self.runtime.registered_reply(
            {
                "message_id": str(fence.message_id),
                "session_id": str(fence.session_id),
                "owner_instance_id": str(fence.owner_instance_id),
                "lease_generation": fence.lease_generation,
            },
            payload["conversation_id"],
            payload["message"],
            payload["citations"],
        )

    async def decline(self, question):
        await self._phase("decline")
        return {"status": "ok"}


@pytest.fixture
def harness():
    async def catalog(query):
        return [
            {
                "id": "entry",
                "source_butler": "concierge",
                "source_schema": "concierge",
                "source_table": "sessions",
                "source_id": "source",
                "rrf_score": 0.02,
                "semantic_rank": 1,
                "keyword_rank": 1,
            }
        ]

    memory_hooks.register_fast_answer_catalog("switchboard", catalog)
    store = _Store()
    dependencies = _Dependencies(store)
    runtime = DashboardFastAnswerRuntime(
        store, dependencies, heartbeat_seconds=0.02, sweep_seconds=0.02
    )
    dependencies.runtime = runtime
    try:
        yield runtime, store, dependencies
    finally:
        memory_hooks.unregister_fast_answer_catalog("switchboard", catalog)


async def _run(runtime):
    return await runtime.run(
        message_id=uuid4(), request_id=uuid4(), conversation_id=uuid4(), prompt="q"
    )


@pytest.mark.asyncio
async def test_shared_runtime_success_and_whole_admission_before_reads(harness):
    # REQ-dashboard-fast-answer-001 / REQ-dashboard-fast-answer-002 / REQ-dashboard-fast-answer-006.
    runtime, store, dependencies = harness
    result = await _run(runtime)
    assert result.outcome == "completed" and result.handled
    assert store.events[0][0] == "register"
    assert dependencies.calls == ["models", "target", "classify", "read", "phrase", "reply"]
    assert store.lane == "answer"
    assert store.reply["sources"] == [dependencies.source]
    assert not runtime.owns(result.session_id)
    assert len([event for event in store.events if event[0] == "reply"]) == 1


@pytest.mark.asyncio
async def test_only_schema_invalid_classification_retries_and_provider_failure_does_not(harness):
    # REQ-dashboard-fast-answer-006: one same-candidate retry; no provider failover.
    runtime, store, dependencies = harness
    dependencies.classify_results = [InvalidAdmission("bad schema"), dependencies.admission]
    assert (await _run(runtime)).outcome == "completed"
    assert dependencies.calls.count("classify") == 2
    assert dependencies.calls.count("models") == 1
    store.state = "active"
    dependencies.calls.clear()
    dependencies.fail_phase = "classify"
    assert not (await _run(runtime)).handled
    assert dependencies.calls == ["models", "target", "classify"]
    assert store.state == "fallback"


@pytest.mark.asyncio
async def test_missing_models_and_invalid_whole_plan_preserve_cli_continuation(
    harness, monkeypatch
):
    # REQ-dashboard-fast-answer-001 / REQ-dashboard-fast-answer-004.
    runtime, store, dependencies = harness
    registration = store.register
    monkeypatch.setattr(store, "register", AsyncMock(side_effect=RuntimeError("store unavailable")))
    unavailable = await _run(runtime)
    assert not unavailable.handled and unavailable.outcome == "registration_unavailable"
    assert unavailable.session_id is None and dependencies.calls == []
    monkeypatch.setattr(store, "register", registration)
    dependencies.models_available = False
    assert not (await _run(runtime)).handled
    assert dependencies.calls == ["models"]
    dependencies.models_available = True
    for reads in (
        [{"name": "dashboard_read_fleet_status"}, {"name": "notify"}],
        [{"name": "dashboard_read_future"}],
        [{"name": "dashboard_read_fleet_status"}] * 4,
    ):
        store.state = "active"
        dependencies.calls.clear()
        dependencies.admission["reads"] = reads
        assert not (await _run(runtime)).handled
        assert "read" not in dependencies.calls and "reply" not in dependencies.calls
    for scope in ("domain", "none"):
        store.state = "active"
        dependencies.admission["scope"] = scope
        dependencies.admission["reads"] = []
        assert not (await _run(runtime)).handled


@pytest.mark.asyncio
async def test_stop_and_lost_fence_never_start_later_phases(harness):
    # REQ-dashboard-fast-answer-002: provider cancellation can settle; uncertain transport cannot.
    runtime, store, dependencies = harness
    server = FastMCP("registered-stop-control")
    spawner = SimpleNamespace(cancel_session_and_wait=AsyncMock(return_value=False))
    context = ToolContext(
        daemon=SimpleNamespace(_dashboard_fast_answer_runtime=runtime),
        pool=None,
        spawner=spawner,
        butler_name="switchboard",
        butler_type=ButlerType.STAFFER,
        is_switchboard=True,
        is_messenger=False,
        route_metrics=None,
    )
    register_session_tools(context, server, lambda group, **kwargs: server.tool(**kwargs))
    for phase, expected in (
        ("classify", "cancelled"),
        ("read", "pending_reconciliation"),
        ("phrase", "cancelled"),
        ("reply", "pending_reconciliation"),
    ):
        store.state = "active"
        store.stopped = False
        store.lane = None
        dependencies.calls.clear()
        dependencies.entered.clear()
        dependencies.hold_phase = phase
        task = asyncio.create_task(_run(runtime))
        await dependencies.entered.wait()
        session = next(iter(runtime._live))
        store.stopped = True
        async with Client(server) as client:
            stopped = await client.call_tool("cancel_session", {"session_id": str(session)})
        cancelled = stopped.data["cancelled"]
        result = await task
        assert result.outcome == expected
        assert cancelled is (expected == "cancelled")
        assert dependencies.calls[-1] == phase
        assert not runtime.owns(session)
        assert spawner.cancel_session_and_wait.await_count == 0
    store.state = "active"
    store.stopped = False
    dependencies.hold_phase = "classify"
    dependencies.entered.clear()
    task = asyncio.create_task(_run(runtime))
    await dependencies.entered.wait()
    store.lost = True
    result = await task
    assert result.outcome == "pending_reconciliation"
    assert "read" not in dependencies.calls[-1:]


@pytest.mark.asyncio
async def test_post_read_failures_do_not_reclassify_or_fabricate_grounding(harness):
    # REQ-dashboard-fast-answer-004 / REQ-dashboard-fast-answer-006.
    runtime, store, dependencies = harness
    dependencies.source = None
    result = await _run(runtime)
    assert result.handled and result.outcome == "failed"
    assert dependencies.calls == ["models", "target", "classify", "read"]
    store.state = "active"
    dependencies.calls.clear()
    dependencies.source = {
        "kind": "view",
        "ref": "concierge.v_fleet_sessions",
        "as_of": "2026-10-12T00:00:00Z",
    }
    dependencies.fail_phase = "phrase"
    result = await _run(runtime)
    assert result.handled and result.outcome == "failed"
    assert dependencies.calls.count("read") == 1
    assert dependencies.calls.count("classify") == 1
    assert dependencies.calls.count("phrase") == 1
    assert "reply" not in dependencies.calls


@pytest.mark.asyncio
async def test_registered_reply_uses_prepared_identity_and_server_source_list(harness):
    # REQ-dashboard-fast-answer-006: exercise actual registered owning MCP tool, not .fn().
    runtime, store, dependencies = harness
    server = FastMCP("registered-reply-control")
    daemon = SimpleNamespace(_dashboard_fast_answer_runtime=runtime)
    context = ToolContext(
        daemon=daemon,
        pool=object(),
        spawner=None,
        butler_name="switchboard",
        butler_type=ButlerType.STAFFER,
        is_switchboard=True,
        is_messenger=False,
        route_metrics=None,
    )

    def core_tool(group, **kwargs):
        return server.tool(**kwargs)

    register_conversation_reply_tool(context, server, core_tool)
    dependencies.hold_phase = "reply"
    task = asyncio.create_task(_run(runtime))
    await dependencies.entered.wait()
    entry = next(iter(runtime._live.values()))
    fence, payload = entry.fence, entry.reply_payload
    binding = {
        "message_id": str(fence.message_id),
        "session_id": str(fence.session_id),
        "owner_instance_id": str(fence.owner_instance_id),
        "lease_generation": fence.lease_generation,
    }
    async with Client(server) as client:
        for bad in (
            {**binding, "owner_instance_id": str(uuid4())},
            {**binding, "lease_generation": True},
            {**binding, "extra": "caller"},
        ):
            response = await client.call_tool(
                "conversation_reply",
                {
                    "conversation_id": payload["conversation_id"],
                    "message": payload["message"],
                    "sources": payload["source_labels"],
                    "fast_answer": bad,
                },
            )
            assert response.data["status"] == "refused"
        response = await client.call_tool(
            "conversation_reply",
            {
                "conversation_id": payload["conversation_id"],
                "message": payload["message"],
                "sources": payload["source_labels"],
                "fast_answer": binding,
            },
        )
        assert response.data["outcome"] == "completed"
        assert store.reply["sources"] == [dependencies.source]
    dependencies.release.set()
    assert (await task).outcome == "completed"


@pytest.mark.asyncio
async def test_operational_startup_periodic_supervision_and_shutdown_no_replay(harness):
    # REQ-dashboard-fast-answer-002: same real entrypoint used by lifecycle; SQL evidence separate.
    runtime, store, dependencies = harness
    await runtime.start()
    await asyncio.sleep(0.055)
    await runtime.close()
    assert len([event for event in store.events if event[0] == "sweep"]) >= 2
    assert not dependencies.calls
    assert runtime._supervisor is None

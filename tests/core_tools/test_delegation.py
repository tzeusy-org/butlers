"""Unit tests for the delegate_ask/delegate_receive/delegate_answer core tools.

Mirrors the fake-``_core_tool``-registry harness from
``tests/core_tools/test_infra_trigger.py`` rather than booting a full
``ButlerDaemon`` (see ``tests/daemon/test_notify_attention_ledger.py`` for
that heavier pattern) -- the tool logic here only touches
``butlers.core.delegation_ledger`` and ``daemon.switchboard_client``, both of
which are cleanly monkeypatchable/mockable at this level.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.config import ButlerType
from butlers.core_tools import _delegation
from butlers.core_tools._base import ToolContext
from butlers.core_tools._delegation import register_delegation_tools

pytestmark = pytest.mark.unit


def _register(
    butler_name: str = "finance", butler_type=ButlerType.BUTLER, switchboard_client=None, pool=None
):
    registered: dict[str, callable] = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    mcp = SimpleNamespace()
    daemon = SimpleNamespace(switchboard_client=switchboard_client)
    ctx = ToolContext(
        daemon=daemon,
        pool=pool if pool is not None else AsyncMock(),
        spawner=None,
        butler_name=butler_name,
        butler_type=butler_type,
        is_switchboard=(butler_name == "switchboard"),
        is_messenger=False,
        route_metrics=None,
    )
    register_delegation_tools(ctx, mcp, _core_tool)
    return registered


def test_staffer_gets_no_delegation_tools():
    registered = _register(butler_type=ButlerType.STAFFER)
    assert registered == {}


class TestDelegateAsk:
    async def test_empty_question_rejected(self):
        registered = _register()
        result = await registered["delegate_ask"](question="   ")
        assert result["status"] == "error"

    async def test_no_catalog_match_records_unroutable(self, monkeypatch):
        registered = _register()
        monkeypatch.setattr(
            _delegation, "resolve_target_via_catalog", AsyncMock(return_value=(None, None, None))
        )
        record_ask_mock = AsyncMock(return_value="ledger-1")
        monkeypatch.setattr(_delegation, "record_ask", record_ask_mock)

        result = await registered["delegate_ask"](question="asdf gibberish nonsense")

        assert result == {
            "status": "unroutable",
            "ledger_id": "ledger-1",
            "reason": "no_catalog_match",
        }
        assert record_ask_mock.await_args.kwargs["status"] == "unroutable"

    async def test_self_target_records_unroutable(self, monkeypatch):
        registered = _register(butler_name="finance")
        monkeypatch.setattr(
            _delegation,
            "resolve_target_via_catalog",
            AsyncMock(return_value=("finance", "cat-1", 0.9)),
        )
        record_ask_mock = AsyncMock(return_value="ledger-2")
        monkeypatch.setattr(_delegation, "record_ask", record_ask_mock)

        result = await registered["delegate_ask"](question="What is my own budget rule?")

        assert result["status"] == "unroutable"
        assert result["reason"] == "self_target"
        assert result["target_butler"] == "finance"

    async def test_switchboard_not_connected_marks_failed(self, monkeypatch):
        registered = _register(butler_name="finance", switchboard_client=None)
        monkeypatch.setattr(
            _delegation,
            "resolve_target_via_catalog",
            AsyncMock(return_value=("relationship", "cat-1", 0.7)),
        )
        monkeypatch.setattr(_delegation, "record_ask", AsyncMock(return_value="ledger-3"))
        mark_outcome_mock = AsyncMock()
        monkeypatch.setattr(_delegation, "mark_dispatch_outcome", mark_outcome_mock)

        result = await registered["delegate_ask"](question="Who is Alice's employer?")

        assert result["status"] == "failed"
        assert result["ledger_id"] == "ledger-3"
        assert result["retryable"] is True
        mark_outcome_mock.assert_awaited_once()
        assert mark_outcome_mock.await_args.kwargs["status"] == "failed"

    async def test_route_call_error_result_marks_failed(self, monkeypatch):
        client = AsyncMock()
        error_result = SimpleNamespace(is_error=True, content=[SimpleNamespace(text="boom")])
        client.call_tool = AsyncMock(return_value=error_result)
        registered = _register(butler_name="finance", switchboard_client=client)

        monkeypatch.setattr(
            _delegation,
            "resolve_target_via_catalog",
            AsyncMock(return_value=("relationship", "cat-1", 0.7)),
        )
        monkeypatch.setattr(_delegation, "record_ask", AsyncMock(return_value="ledger-4"))
        mark_outcome_mock = AsyncMock()
        monkeypatch.setattr(_delegation, "mark_dispatch_outcome", mark_outcome_mock)

        result = await registered["delegate_ask"](question="Who is Alice's employer?")

        assert result["status"] == "failed"
        assert "boom" in result["error"]
        assert mark_outcome_mock.await_args.kwargs["status"] == "failed"

    async def test_successful_route_marks_routed(self, monkeypatch):
        client = AsyncMock()
        ok_result = SimpleNamespace(is_error=False, data={"status": "scheduled"})
        client.call_tool = AsyncMock(return_value=ok_result)
        registered = _register(butler_name="finance", switchboard_client=client)

        monkeypatch.setattr(
            _delegation,
            "resolve_target_via_catalog",
            AsyncMock(return_value=("relationship", "cat-1", 0.7)),
        )
        monkeypatch.setattr(_delegation, "record_ask", AsyncMock(return_value="ledger-5"))
        mark_outcome_mock = AsyncMock()
        monkeypatch.setattr(_delegation, "mark_dispatch_outcome", mark_outcome_mock)

        result = await registered["delegate_ask"](question="Who is Alice's employer?")

        assert result == {
            "status": "routed",
            "ledger_id": "ledger-5",
            "target_butler": "relationship",
        }
        client.call_tool.assert_awaited_once()
        tool_name, tool_args = client.call_tool.await_args.args
        assert tool_name == "route"
        assert tool_args["target_butler"] == "relationship"
        assert tool_args["tool_name"] == "delegate_receive"
        assert tool_args["args"]["ledger_id"] == "ledger-5"
        mark_outcome_mock.assert_awaited_once()
        assert mark_outcome_mock.await_args.kwargs["status"] == "routed"

    async def test_switchboard_self_dispatch_uses_direct_route_function(self, monkeypatch):
        registered = _register(butler_name="switchboard", switchboard_client=None)
        monkeypatch.setattr(
            _delegation,
            "resolve_target_via_catalog",
            AsyncMock(return_value=("relationship", "cat-1", 0.7)),
        )
        monkeypatch.setattr(_delegation, "record_ask", AsyncMock(return_value="ledger-6"))
        mark_outcome_mock = AsyncMock()
        monkeypatch.setattr(_delegation, "mark_dispatch_outcome", mark_outcome_mock)

        import importlib

        # NOTE: ``roster/switchboard/tools/routing/__init__.py`` re-exports
        # ``route`` (``from .route import route``), which shadows the
        # ``routing.route`` *submodule* attribute with the function on the
        # parent package. ``importlib.import_module`` (a direct
        # ``sys.modules`` lookup) sidesteps that shadowing and gets the real
        # leaf module that ``_delegation``'s deferred
        # ``from ...routing.route import route`` actually reads from.
        _route_module = importlib.import_module("butlers.tools.switchboard.routing.route")

        direct_route_mock = AsyncMock(return_value={"result": {"status": "scheduled"}})
        monkeypatch.setattr(_route_module, "route", direct_route_mock)

        result = await registered["delegate_ask"](question="Who is Alice's employer?")

        assert result["status"] == "routed"
        direct_route_mock.assert_awaited_once()
        assert mark_outcome_mock.await_args.kwargs["status"] == "routed"


class TestDelegateReceive:
    async def test_empty_question_rejected(self):
        registered = _register()
        result = await registered["delegate_receive"](
            ledger_id="x", question="  ", asking_butler="finance"
        )
        assert result["status"] == "error"

    async def test_missing_ledger_row_rejected(self, monkeypatch):
        registered = _register(butler_name="relationship")
        monkeypatch.setattr(_delegation, "get_delegation", AsyncMock(return_value=None))

        result = await registered["delegate_receive"](
            ledger_id=str(uuid.uuid4()), question="q", asking_butler="finance"
        )
        assert result["status"] == "error"

    async def test_target_mismatch_rejected(self, monkeypatch):
        registered = _register(butler_name="relationship")
        monkeypatch.setattr(
            _delegation,
            "get_delegation",
            AsyncMock(return_value={"target_butler": "health", "status": "pending"}),
        )

        result = await registered["delegate_receive"](
            ledger_id=str(uuid.uuid4()), question="q", asking_butler="finance"
        )
        assert result["status"] == "error"
        assert "targets" in result["error"]

    async def test_already_answered_short_circuits(self, monkeypatch):
        registered = _register(butler_name="relationship")
        monkeypatch.setattr(
            _delegation,
            "get_delegation",
            AsyncMock(return_value={"target_butler": "relationship", "status": "answered"}),
        )

        result = await registered["delegate_receive"](
            ledger_id="ledger-7", question="q", asking_butler="finance"
        )
        assert result == {"status": "already_answered", "ledger_id": "ledger-7"}

    async def test_success_schedules_task(self, monkeypatch):
        registered = _register(butler_name="relationship")
        monkeypatch.setattr(
            _delegation,
            "get_delegation",
            AsyncMock(
                return_value={
                    "target_butler": "relationship",
                    "status": "pending",
                    "question": "Who is Alice's employer?",
                    "asking_butler": "finance",
                }
            ),
        )
        schedule_mock = AsyncMock(return_value=uuid.uuid4())
        monkeypatch.setattr(_delegation, "_schedule_create", schedule_mock)

        result = await registered["delegate_receive"](
            ledger_id="ledger-8",
            question="Who is Alice's employer?",
            asking_butler="finance",
        )
        assert result["status"] == "scheduled"
        assert result["ledger_id"] == "ledger-8"
        schedule_mock.assert_awaited_once()
        _pool, task_name, cron, prompt = schedule_mock.await_args.args
        assert task_name == "delegate-answer-ledger-8"
        assert cron is not None
        before = schedule_mock.await_count
        for question, actor in [
            ("substituted body", "finance"),
            ("Who is Alice's employer?", "forged"),
        ]:
            refused = await registered["delegate_receive"](
                ledger_id="ledger-8",
                question=question,
                asking_butler=actor,
            )
            assert (
                refused["status"] == "error"
                and refused["error"] == "Delegated question body differs."
            )
        assert schedule_mock.await_count == before
        await _assert_native_received_question_schedule(monkeypatch)
        assert "ledger-8" in prompt
        assert "delegate_answer" in prompt


class TestDelegateAnswer:
    async def test_empty_answer_rejected(self):
        registered = _register()
        result = await registered["delegate_answer"](ledger_id="x", answer="   ")
        assert result["status"] == "error"

    async def test_guard_failure_not_found_returns_error(self, monkeypatch):
        from butlers.core.delegation_ledger import UnacceptedAnswerClassification

        registered = _register(butler_name="relationship")
        monkeypatch.setattr(_delegation, "record_answer", AsyncMock(return_value=None))
        monkeypatch.setattr(
            _delegation,
            "classify_unaccepted_answer",
            AsyncMock(return_value=UnacceptedAnswerClassification("not_found", None)),
        )

        result = await registered["delegate_answer"](ledger_id="ledger-9", answer="Acme Corp.")
        assert result["status"] == "error"

    async def test_changed_answer_is_integrity_conflict(self, monkeypatch):
        from butlers.core.delegation_ledger import UnacceptedAnswerClassification

        registered = _register(butler_name="relationship")
        monkeypatch.setattr(_delegation, "record_answer", AsyncMock(return_value=None))
        monkeypatch.setattr(
            _delegation,
            "classify_unaccepted_answer",
            AsyncMock(
                return_value=UnacceptedAnswerClassification(
                    "changed", {"status": "answered", "answer": "Acme Corp."}
                )
            ),
        )
        dispatch_mock = AsyncMock()
        monkeypatch.setattr(_delegation, "_dispatch_via_switchboard", dispatch_mock)

        result = await registered["delegate_answer"](ledger_id="ledger-9", answer="Globex Inc.")
        assert result["status"] == "error"
        assert "integrity conflict" in result["error"]
        dispatch_mock.assert_not_awaited()

    async def test_legacy_row_reports_ok_without_callback(self, monkeypatch):
        from butlers.core.delegation_ledger import UnacceptedAnswerClassification

        registered = _register(butler_name="relationship")
        monkeypatch.setattr(_delegation, "record_answer", AsyncMock(return_value=None))
        monkeypatch.setattr(
            _delegation,
            "classify_unaccepted_answer",
            AsyncMock(
                return_value=UnacceptedAnswerClassification(
                    "legacy", {"status": "answered", "wake_key": None}
                )
            ),
        )
        dispatch_mock = AsyncMock()
        monkeypatch.setattr(_delegation, "_dispatch_via_switchboard", dispatch_mock)

        result = await registered["delegate_answer"](ledger_id="ledger-9", answer="Acme Corp.")
        assert result["status"] == "ok"
        assert result["wake_state"] == "not_applicable"
        dispatch_mock.assert_not_awaited()

    async def test_success_attempts_callback_and_reports_ok(self, monkeypatch):
        registered = _register(butler_name="relationship")
        monkeypatch.setattr(
            _delegation,
            "record_answer",
            AsyncMock(
                return_value={
                    "id": "ledger-10",
                    "asking_butler": "finance",
                    "wake_key": "delegation-wake:v1:ledger-10:abc",
                }
            ),
        )
        dispatch_mock = AsyncMock(return_value=(None, False))
        monkeypatch.setattr(_delegation, "_dispatch_via_switchboard", dispatch_mock)
        attempt_mock = AsyncMock()
        monkeypatch.setattr(_delegation, "record_wake_attempt", attempt_mock)

        result = await registered["delegate_answer"](ledger_id="ledger-10", answer="Acme Corp.")

        assert result == {"status": "ok", "ledger_id": "ledger-10", "answer_recorded": True}
        dispatch_mock.assert_awaited_once()
        kwargs = dispatch_mock.await_args.kwargs
        assert kwargs["target_butler"] == "finance"
        assert kwargs["tool_name"] == "delegate_wake"
        assert kwargs["args"] == {
            "ledger_id": "ledger-10",
            "wake_key": "delegation-wake:v1:ledger-10:abc",
        }
        attempt_mock.assert_awaited_once()
        assert attempt_mock.await_args.kwargs["result"] == "routed"

    async def test_callback_failure_reports_honest_partial_success(self, monkeypatch):
        registered = _register(butler_name="relationship")
        monkeypatch.setattr(
            _delegation,
            "record_answer",
            AsyncMock(
                return_value={
                    "id": "ledger-11",
                    "asking_butler": "finance",
                    "wake_key": "delegation-wake:v1:ledger-11:abc",
                }
            ),
        )
        monkeypatch.setattr(
            _delegation,
            "_dispatch_via_switchboard",
            AsyncMock(return_value=("Switchboard unreachable: boom", True)),
        )
        monkeypatch.setattr(_delegation, "record_wake_attempt", AsyncMock())
        mark_failed_mock = AsyncMock()
        monkeypatch.setattr(_delegation, "mark_wake_callback_failed", mark_failed_mock)

        result = await registered["delegate_answer"](ledger_id="ledger-11", answer="Acme Corp.")

        assert result["status"] == "ok"
        assert result["answer_recorded"] is True
        assert result["wake_state"] == "callback_failed"
        assert result["callback_retryable"] is True
        assert "unreachable" in result["callback_error"]
        mark_failed_mock.assert_awaited_once()

    async def test_duplicate_same_answer_replays_existing_wake_key(self, monkeypatch):
        from butlers.core.delegation_ledger import UnacceptedAnswerClassification

        registered = _register(butler_name="relationship")
        monkeypatch.setattr(_delegation, "record_answer", AsyncMock(return_value=None))
        monkeypatch.setattr(
            _delegation,
            "classify_unaccepted_answer",
            AsyncMock(
                return_value=UnacceptedAnswerClassification(
                    "duplicate",
                    {
                        "asking_butler": "finance",
                        "wake_key": "delegation-wake:v1:ledger-12:abc",
                    },
                )
            ),
        )
        dispatch_mock = AsyncMock(return_value=(None, False))
        monkeypatch.setattr(_delegation, "_dispatch_via_switchboard", dispatch_mock)
        monkeypatch.setattr(_delegation, "record_wake_attempt", AsyncMock())

        result = await registered["delegate_answer"](ledger_id="ledger-12", answer="Acme Corp.")

        assert result["status"] == "ok"
        dispatch_mock.assert_awaited_once()
        assert dispatch_mock.await_args.kwargs["args"]["wake_key"] == (
            "delegation-wake:v1:ledger-12:abc"
        )


class TestDelegateWake:
    async def test_delegates_to_handle_delegate_wake(self, monkeypatch):
        registered = _register(butler_name="finance")
        handle_mock = AsyncMock(
            return_value={"status": "ok", "ledger_id": "ledger-13", "wake_state": "task_created"}
        )
        monkeypatch.setattr(_delegation, "handle_delegate_wake", handle_mock)

        result = await registered["delegate_wake"](
            ledger_id="ledger-13", wake_key="delegation-wake:v1:ledger-13:abc"
        )

        assert result["status"] == "ok"
        handle_mock.assert_awaited_once()
        assert handle_mock.await_args.kwargs == {
            "ledger_id": "ledger-13",
            "wake_key": "delegation-wake:v1:ledger-13:abc",
            "asking_butler": "finance",
        }


class TestClassifyDelegationRouteResult:
    """This module's route()-result classification rule: a truthy ``error``
    key at either the route() envelope level or the target tool's own
    unwrapped payload is a failure. See
    ``_classify_delegation_route_result``'s docstring for the bu-xthtw
    unwrap-bugfix this covers (the pre-fix version never looked past the
    envelope, so a route()-level success wrapping a target-tool-level
    failure was silently misreported as success)."""

    def test_success_envelope_unwraps_to_inner_result(self):
        data, error, retryable = _delegation._classify_delegation_route_result(
            {"result": {"status": "scheduled", "task_id": "t1"}}
        )
        assert data == {"status": "scheduled", "task_id": "t1"}
        assert error is None
        assert retryable is False

    def test_route_level_error_envelope_is_detected(self):
        data, error, retryable = _delegation._classify_delegation_route_result(
            {"error": "RuntimeError: Unknown tool: delegate_receive"}
        )
        assert data is None
        assert error == "RuntimeError: Unknown tool: delegate_receive"
        assert retryable is False

    def test_inner_target_error_is_detected_after_unwrap(self):
        """The bu-xthtw regression case: route() itself succeeded (wraps
        {"result": ...}), but the target tool's own payload reports its own
        failure -- this must not be misread as a successful dispatch."""
        data, error, retryable = _delegation._classify_delegation_route_result(
            {"result": {"status": "error", "error": "No delegation_ledger row for id='x'."}}
        )
        assert data is None
        assert error == "No delegation_ledger row for id='x'."
        assert retryable is False

    def test_non_dict_raw_is_treated_as_no_error(self):
        data, error, retryable = _delegation._classify_delegation_route_result("not a dict")
        assert data is None
        assert error is None
        assert retryable is False


class TestDispatchViaSwitchboardEnvelope:
    """Regression coverage for the route()-envelope unwrap bug (bu-xthtw):
    a real MCP client's CallToolResult.data is route()'s own
    {"result"/"error"} shape, not the target tool's dict directly -- mirrors
    ``tests/core_tools/test_domain_events.py::TestDispatchReceiveViaSwitchboardEnvelope
    ::test_client_branch_unwraps_successful_route_result``.
    """

    async def test_client_branch_unwraps_successful_route_result(self):
        client = AsyncMock()
        client.call_tool = AsyncMock(
            return_value=SimpleNamespace(
                is_error=False,
                data={"result": {"status": "scheduled", "task_id": "t1"}},
            )
        )

        error, retryable = await _delegation._dispatch_via_switchboard(
            client,
            AsyncMock(),
            "finance",
            target_butler="relationship",
            tool_name="delegate_receive",
            args={"ledger_id": "ledger-1", "question": "q", "asking_butler": "finance"},
        )

        assert error is None
        assert retryable is False

    async def test_client_branch_surfaces_wrapped_target_failure(self):
        """A route()-level success whose target tool's own payload reports a
        failure (e.g. delegate_receive's ledger row went missing) must
        surface as a dispatch failure, not a silent success."""
        client = AsyncMock()
        client.call_tool = AsyncMock(
            return_value=SimpleNamespace(
                is_error=False,
                data={
                    "result": {
                        "status": "error",
                        "error": "No delegation_ledger row for id='ledger-1'.",
                    }
                },
            )
        )

        error, retryable = await _delegation._dispatch_via_switchboard(
            client,
            AsyncMock(),
            "finance",
            target_butler="relationship",
            tool_name="delegate_receive",
            args={"ledger_id": "ledger-1", "question": "q", "asking_butler": "finance"},
        )

        assert error == "No delegation_ledger row for id='ledger-1'."
        assert retryable is False

    async def test_switchboard_self_delivery_branch_surfaces_wrapped_target_failure(
        self, monkeypatch
    ):
        import importlib

        # See test_switchboard_self_dispatch_uses_direct_route_function above
        # for why importlib.import_module is required to reach the real
        # ``route`` submodule rather than the re-exported function.
        route_module = importlib.import_module("butlers.tools.switchboard.routing.route")

        direct_route_mock = AsyncMock(
            return_value={
                "result": {
                    "status": "error",
                    "error": "wake_key does not match the ledger row's immutable wake key.",
                }
            }
        )
        monkeypatch.setattr(route_module, "route", direct_route_mock)

        error, retryable = await _delegation._dispatch_via_switchboard(
            None,
            AsyncMock(),
            "switchboard",
            target_butler="finance",
            tool_name="delegate_wake",
            args={"ledger_id": "ledger-1", "wake_key": "bad-key"},
        )

        assert error == "wake_key does not match the ledger row's immutable wake key."
        assert retryable is False


async def _assert_native_received_question_schedule(monkeypatch):
    """Actual handler/source/receiver callbacks; SQL/HTTP doubles, no online authority claim."""
    from contextlib import asynccontextmanager
    from copy import copy, deepcopy

    # Actual core constructor/writer lifetime with pool/SQL doubles. This
    # proves no Memory prerequisite and fixed own-role refusals, not real SQL.
    import asyncpg

    from butlers.chronicler.location_delegation_copies import (
        NativeDelegationWriter,
        question_digest,
    )
    from butlers.chronicler.location_delegation_receivers import (
        prepare_question_source,
        question_challenge,
        schedule_received_question,
        verify_question_delivery,
    )
    from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
    from butlers.chronicler.location_memory_context import context_writer
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_tool_copies import _current_tool_copy, _ToolCopy
    from butlers.core.delegation_source import _writers, clear_writer, register_writer

    own_pool = MagicMock(spec=asyncpg.Pool)
    own_pool.is_closing.return_value = False
    own_conn = AsyncMock()
    namespace, role = "relationship", "butler_relationship_rw"
    own_conn.fetchval.side_effect = lambda query: (
        namespace if query == "SELECT current_schema()" else role
    )
    own_pool.acquire.return_value.__aenter__.return_value = own_conn
    core = await NativeDelegationRuntime.create(
        domain=own_pool, name="relationship", schema="relationship", registry=AsyncMock()
    )
    try:
        assert _writers[own_pool] is core.delegation_writer
        assert context_writer(own_pool) is core and not hasattr(core, "memory")
        await core.lock_domain(own_conn)
        assert own_conn.execute.await_count == 1
        role = "other_owning_role"
        with pytest.raises(PolicyUnavailableError, match="owning writer differs"):
            await core.lock_domain(own_conn)
        assert own_conn.execute.await_count == 1
        role = "butler_relationship_rw"
        namespace = "other_private_namespace"
        with pytest.raises(PolicyUnavailableError, match="owning namespace differs"):
            await NativeDelegationRuntime.create(
                domain=own_pool, name="relationship", schema="relationship", registry=AsyncMock()
            )
        assert _writers[own_pool] is core.delegation_writer
        namespace = "relationship"
        await core.lock_domain(own_conn)
        assert own_conn.execute.await_count == 2
    finally:
        core.close()
    assert own_pool not in _writers and context_writer(own_pool) is None
    with pytest.raises(PolicyUnavailableError, match="owning writer differs"):
        await core.lock_domain(own_conn)

    ledger, question, parent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    canonical = dict(
        id=ledger,
        asking_butler="chronicler",
        target_butler="relationship",
        question="synthetic source question",
        catalog_match_id=None,
        catalog_score=None,
        status="pending",
        metadata={"synthetic": True},
    )
    body_digest = question_digest(canonical)
    trace = []

    class Pool:
        def __init__(self, name):
            self.name = name
            self.inputs, self.loans, self.schedules, self.tasks = {}, {}, {}, {}
            self.attempts, self.floors, self.dispositions = {}, {}, {}
            self.unknown_terminal = False
            self.claims, self.ended, self.contexts, self.context_bindings = {}, {}, {}, {}
            self.witness_failure = False
            self.server_finished = {}
            self.question_intents = {}
            self.transaction_active = False
            self.fenced = False
            self.unknown = False
            self.native_missing = False
            self.ordinary = None

        @asynccontextmanager
        async def acquire(self):
            trace.append(self.name + ":acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            previous = deepcopy(
                (
                    self.inputs,
                    self.loans,
                    self.schedules,
                    self.tasks,
                    self.attempts,
                    self.floors,
                    self.dispositions,
                )
            )
            self.transaction_active = True
            try:
                yield
            except BaseException:
                (
                    self.inputs,
                    self.loans,
                    self.schedules,
                    self.tasks,
                    self.attempts,
                    self.floors,
                    self.dispositions,
                ) = previous
                trace.append(self.name + ":rollback")
                raise
            else:
                trace.append(self.name + ":commit")
            finally:
                self.transaction_active = False

        async def fetchrow(self, sql, *args):
            if "FROM location_received_delegation_floors f" in sql:
                if self.unknown_terminal:
                    return None
                for gen, receipt in self.dispositions.items():
                    if self.floors[gen]["decision_id"] == args[0] and receipt == args[1]:
                        return self.floors[gen] | {"receipt_id": receipt}
                return None
            if "FROM location_received_delegation_floors" in sql:
                return self.floors.get(args[0])
            if "FROM location_received_delegation_attempts" in sql:
                return self.attempts.get(args[0])
            if "FROM location_received_delegation_schedules s" in sql and "FOR UPDATE OF t" in sql:
                entry = self.schedules.get(args[0])
                if entry is None:
                    return None
                return entry | {"prompt": self.tasks[entry["task_id"]], "enabled": True}

            if "FROM location_received_delegation_schedules s" in sql:
                entry = next((r for r in self.schedules.values() if r["task_id"] == args[0]), None)
                if entry is None:
                    return None
                return dict(
                    self.inputs[entry["receiving_generation"]],
                    **entry,
                    scheduled_prompt=self.tasks[args[0]],
                )
            if "FROM location_received_delegation_inputs i" in sql:
                entry = self.schedules.get(args[0])
                if entry is None or entry["task_id"] != args[1]:
                    return None
                return self.inputs[args[0]] | entry
            if "FROM location_received_delegation_claims" in sql:
                return self.claims.get(args[0])
            if "FROM location_runtime_context_bindings" in sql:
                return self.context_bindings.get(args[0])
            if "FROM scheduled_tasks" in sql:
                return {"prompt": self.tasks[args[0]]} if args[0] in self.tasks else None
            if "FROM public.delegation_ledger" in sql:
                return canonical.copy()
            if "FROM location_ordinary_delegation_inputs" in sql:
                return self.ordinary
            if "FROM location_native_delegation_inputs" in sql:
                if self.native_missing:
                    return None
                return dict(
                    ledger_id=ledger,
                    question_generation=question,
                    body_digest=body_digest,
                    parent_count=1,
                    exclusive_input=True,
                )
            if "FROM location_native_delegation_loans" in sql:
                return self.loans.get(args[0])
            if "FROM location_received_delegation_inputs" in sql:
                if self.unknown and not self.transaction_active:
                    return None
                return self.inputs.get(args[0])
            if "FROM location_received_delegation_schedules" in sql:
                return self.schedules.get(args[0])
            raise AssertionError("unexpected receiver/source row")

        async def fetch(self, sql, *args):
            if "FROM location_native_delegation_inputs" in sql:
                # This receiver fixture owns no outgoing question header;
                # original source data resides in the distinct source pool.
                assert self.name == "receiver"
                return []
            if "FROM location_native_question_answer_observations" in sql:
                assert self.name == "receiver"
                return []
            assert "location_native_delegation_parents" in sql
            return [
                dict(parent_kind="native_copy", parent_generation=parent, parent_digest=b"p" * 32)
            ]

        async def fetchval(self, sql, *args):
            if "FROM location_received_delegation_dispositions" in sql:
                return self.dispositions.get(args[0])
            if "FROM location_received_delegation_floors" in sql:
                return args[0] in self.floors
            if "FROM location_received_delegation_attempts" in sql:
                row = self.attempts.get(args[0])
                if "ledger_id=$2" in sql:
                    return row is not None and (
                        row["ledger_id"],
                        row["body_digest"],
                        row["receiving_incarnation"],
                    ) == tuple(args[1:])
                return row is not None and (row["body_digest"], row["server_request"]) == tuple(
                    args[1:]
                )
            if "EXISTS(SELECT 1 FROM scheduled_tasks" in sql:
                return self.tasks[args[0]] == args[1]
            if "JOIN location_runtime_context_dispositions" in sql:
                return False  # No planted context disposition; claims alone do not close it.
            if "EXISTS(SELECT 1 FROM location_received_delegation_claims c" in sql:
                return any(c["receiving_generation"] == args[0] for c in self.claims.values())

            if "EXISTS(SELECT 1 FROM location_native_delegation_inputs" in sql:
                return not self.native_missing
            if "location_runtime_context_question_intents" in sql:
                return self.question_intents.get(args[0])
            if "location_received_delegation_claims_ended" in sql:
                return self.ended.get(args[0])
            if "location_received_delegation_server_finished" in sql:
                return (
                    args[0] in self.server_finished
                    if "EXISTS" in sql
                    else self.server_finished.get(args[0])
                )
            if "location_received_delegation_inputs" in sql:
                row = self.inputs.get(args[0])
                return (
                    row is not None
                    and row["body_digest"] == args[1]
                    and row["server_request"] == args[2]
                )
            if "JOIN location_runtime_context_dispositions" in sql:
                return False
            if "location_received_delegation_contexts" in sql:
                return any(row["claim_generation"] == args[0] for row in self.contexts.values())
            if "location_runtime_tool_intents" in sql:
                return True
            if "location_native_delegation_dispositions" in sql:
                return False
            if "location_native_copy_births" in sql:
                return self.fenced
            if "SELECT prompt FROM scheduled_tasks" in sql:
                return self.tasks.get(args[0])
            raise AssertionError("unexpected receiver/source value")

        async def execute(self, sql, *args):
            assert self.transaction_active
            if "INSERT INTO location_received_delegation_attempts" in sql:
                trace.append("receiver:attempt-birth")
                self.attempts[args[0]] = dict(
                    zip(
                        (
                            "receiving_generation",
                            "ledger_id",
                            "body_digest",
                            "receiving_incarnation",
                            "receiving_session",
                            "tool_generation",
                            "server_request",
                        ),
                        args,
                    )
                )
            elif "INSERT INTO location_received_delegation_floors" in sql:
                trace.append("receiver:floor")
                self.floors.setdefault(
                    args[0],
                    dict(
                        zip(
                            (
                                "receiving_generation",
                                "decision_id",
                                "manifest_digest",
                                "source_name",
                                "question_generation",
                                "ledger_id",
                                "loan_id",
                                "body_digest",
                                "receiving_incarnation",
                            ),
                            args,
                        )
                    ),
                )
            elif "INSERT INTO location_received_delegation_dispositions" in sql:
                trace.append("receiver:terminal")
                self.dispositions[args[0]] = args[1]
            elif "UPDATE scheduled_tasks SET enabled=false" in sql:
                trace.append("receiver:reduce")
                self.tasks[args[0]] = args[1]
            elif "INSERT INTO location_native_delegation_loans" in sql:
                trace.append("source:loan-birth")
                self.loans[args[0]] = dict(
                    zip(
                        (
                            "loan_id",
                            "question_generation",
                            "receiver_name",
                            "receiving_incarnation",
                            "receiving_generation",
                            "body_digest",
                        ),
                        args,
                    )
                )
            elif "INSERT INTO location_received_delegation_inputs" in sql:
                trace.append("receiver:input-birth")
                self.inputs[args[0]] = dict(
                    zip(
                        (
                            "receiving_generation",
                            "ledger_id",
                            "source_name",
                            "source_incarnation",
                            "question_generation",
                            "loan_id",
                            "body_digest",
                            "receiving_incarnation",
                            "parent_count",
                            "exclusive_input",
                            "receiving_session",
                            "tool_generation",
                            "server_request",
                        ),
                        args,
                    )
                )
            elif "INSERT INTO location_received_delegation_schedules" in sql:
                trace.append("receiver:schedule-birth")
                self.schedules[args[0]] = dict(
                    zip(("receiving_generation", "task_id", "prompt_digest"), args)
                )
            elif "INSERT INTO location_received_delegation_server_finished" in sql:
                self.server_finished[args[0]] = args[3]
            elif "INSERT INTO location_received_delegation_claims_ended" in sql:
                if self.witness_failure:
                    raise RuntimeError("planted secondary lifetime failure")
                self.ended[args[0]] = args[1]
            elif "INSERT INTO location_received_delegation_claims" in sql:
                self.claims[args[0]] = dict(
                    zip(
                        (
                            "claim_generation",
                            "receiving_generation",
                            "task_id",
                            "prompt_digest",
                            "receiving_incarnation",
                            "exclusive_input",
                        ),
                        args,
                    )
                )
            elif "INSERT INTO location_received_delegation_contexts" in sql:
                self.contexts[args[0]] = dict(
                    zip(
                        (
                            "input_generation",
                            "claim_generation",
                            "receiving_session",
                            "bundle_digest",
                        ),
                        args,
                    )
                )
            else:
                raise AssertionError("unexpected receiver/source write")

    source_pool, receiver_pool = Pool("source"), Pool("receiver")

    def runtime(pool, name):
        async def lock_domain(conn):
            assert conn is pool and conn.transaction_active
            trace.append(pool.name + ":policy")

        async def endpoint(selected):
            if source_runtime.name == "chronicler" and receiver_runtime.name == "relationship":
                assert selected in {"chronicler", "relationship"}
            else:
                assert selected in {"relationship", "finance"}
            return "fixed:" + selected

        return SimpleNamespace(
            domain=pool,
            name=name,
            active=True,
            incarnation=uuid.uuid4(),
            lock_domain=lock_domain,
            endpoint=endpoint,
        )

    source_runtime, receiver_runtime = (
        runtime(source_pool, "chronicler"),
        runtime(receiver_pool, "relationship"),
    )
    source_writer, receiver_writer = (
        NativeDelegationWriter(source_runtime),
        NativeDelegationWriter(receiver_runtime),
    )

    async def source_exchange(endpoint, token, body):
        if source_runtime.name == "chronicler":
            assert endpoint == "fixed:relationship"
        else:
            assert endpoint == "fixed:" + receiver_runtime.name
        return await question_challenge(receiver_writer, token, body)

    async def receiver_exchange(endpoint, token, body):
        if receiver_runtime.name == "relationship":
            assert endpoint == "fixed:chronicler"
        else:
            assert endpoint == "fixed:" + source_runtime.name
        if body["op"] == "question_delivery":
            return await verify_question_delivery(source_writer, token, body)
        return await prepare_question_source(source_writer, token, body)

    source_runtime.delegation_writer, receiver_runtime.delegation_writer = (
        source_writer,
        receiver_writer,
    )
    source_runtime.exchange, receiver_runtime.exchange = source_exchange, receiver_exchange
    register_writer(receiver_pool, receiver_writer)
    tool = _ToolCopy(receiver_runtime, uuid.uuid4(), uuid.uuid4(), "delegate_receive", "core")
    token = _current_tool_copy.set(tool)
    registered = _register(butler_name="relationship", pool=receiver_pool)
    monkeypatch.setattr(_delegation, "get_delegation", AsyncMock(return_value=canonical))

    async def create(conn, name, cron, prompt, **kwargs):
        if not conn.transaction_active:
            async with conn.transaction():
                return await create(conn, name, cron, prompt, **kwargs)
        assert conn is receiver_pool and conn.transaction_active
        trace.append("receiver:schedule-write")
        task = uuid.uuid4()
        conn.tasks[task] = prompt
        return task

    monkeypatch.setattr(_delegation, "_schedule_create", create)

    async def receive():
        return await registered["delegate_receive"](
            ledger_id=str(ledger),
            question=canonical["question"],
            asking_butler=canonical["asking_butler"],
        )

    try:
        with pytest.raises(PolicyUnavailableError, match="receiver challenge differs"):
            await question_challenge(
                receiver_writer,
                "caller-nonce",
                {
                    "op": "question_challenge",
                    "ledger_id": str(ledger),
                    "source": "chronicler",
                    "body_digest": body_digest.hex(),
                },
            )
        result = await receive()
        assert result["status"] == "scheduled"
        assert (
            len(source_pool.loans) == len(receiver_pool.inputs) == len(receiver_pool.schedules) == 1
        )
        receiving = next(iter(receiver_pool.inputs.values()))
        bound = receiver_pool.schedules[receiving["receiving_generation"]]
        assert (
            receiving["body_digest"] == body_digest
            and receiving["tool_generation"] == tool.generation
        )
        assert (
            receiving["receiving_session"] == tool.session
            and bound["task_id"] in receiver_pool.tasks
        )
        assert trace.index("source:loan-birth") < trace.index("receiver:input-birth")
        assert trace.index("receiver:input-birth") < trace.index("receiver:schedule-write")
        assert not receiver_writer.pending and not receiver_writer.receiving
        committed = deepcopy((source_pool.loans, receiver_pool.inputs, receiver_pool.schedules))
        source_pool.fenced = True
        refused = await receive()
        assert refused["status"] == "error"
        assert (source_pool.loans, receiver_pool.inputs, receiver_pool.schedules) == committed
        source_pool.fenced = False
        receiver_pool.unknown = True
        refused = await receive()
        assert refused["status"] == "error"
        assert len(source_pool.loans) == len(receiver_pool.inputs) == 2
        assert len(receiver_pool.schedules) == 1  # Unknown committed input never schedules prompt.
        receiver_pool.unknown = False
        assert (await receive())["status"] == "scheduled"
        # A copied private object cannot enter a schedule through matching UUID fields.
        import time

        from butlers.chronicler.location_delegation_receivers import _ReceivedQuestion

        fake = _ReceivedQuestion(
            receiver_writer, uuid.uuid4(), ledger, body_digest, time.monotonic() + 10
        )
        with pytest.raises(PolicyUnavailableError, match="schedule lifetime differs"):
            await schedule_received_question(copy(fake), "synthetic", AsyncMock())
        import hashlib

        from butlers.chronicler.location_delegation_processing import (
            bind_question_context,
            current_scheduled_question,
            scheduled_question_scope,
        )

        async def dispatch_body(prompt):
            admitted = current_scheduled_question(receiver_pool)
            assert admitted is not None and admitted.active
            generation, session = uuid.uuid4(), uuid.uuid4()
            receiver_pool.context_bindings[generation] = {
                "receiving_session": session,
                "bundle_digest": hashlib.sha256(b"frozen").digest(),
            }
            receiver_pool.question_intents[generation] = admitted.generation
            async with receiver_pool.transaction():
                await bind_question_context(
                    receiver_pool,
                    SimpleNamespace(
                        runtime=receiver_runtime, generation=generation, session=session
                    ),
                    prompt,
                )
            assert receiver_pool.contexts[generation]["claim_generation"] == admitted.generation

        task = bound["task_id"]
        prompt = receiver_pool.tasks[task]
        async with scheduled_question_scope(receiver_pool, task, prompt):
            await dispatch_body(prompt)
        assert len(receiver_pool.claims) == len(receiver_pool.ended) == 1
        assert current_scheduled_question(receiver_pool) is None
        # Independent additions preserve the whole prompt, but cannot claim
        # exclusive ancestry for lawful erasure.
        async with scheduled_question_scope(receiver_pool, task, prompt + " independent context"):
            assert current_scheduled_question(receiver_pool).exclusive is False
            await dispatch_body(prompt + " independent context")
        # Policy fencing reaches the source before any receiving claim/body.
        before = len(receiver_pool.claims)
        source_pool.fenced = True
        with pytest.raises(PolicyUnavailableError, match="parent is fenced"):
            async with scheduled_question_scope(receiver_pool, task, prompt):
                pytest.fail("fenced input must not dispatch")
        assert len(receiver_pool.claims) == before
        source_pool.fenced = False
        receiver_pool.tasks[task] = "caller substituted task prompt"
        with pytest.raises(PolicyUnavailableError, match="input source differs"):
            async with scheduled_question_scope(receiver_pool, task, prompt):
                pytest.fail("changed task must not dispatch")
        receiver_pool.tasks[task] = prompt
        # Secondary witness failure preserves primary error and cancellation,
        # while successful processing cannot return an unknown lifetime.
        receiver_pool.witness_failure = True
        with pytest.raises(ValueError, match="planted primary handler failure"):
            async with scheduled_question_scope(receiver_pool, task, prompt):
                raise ValueError("planted primary handler failure")
        import asyncio

        with pytest.raises(asyncio.CancelledError):
            async with scheduled_question_scope(receiver_pool, task, prompt):
                raise asyncio.CancelledError
        with pytest.raises(RuntimeError, match="secondary lifetime failure"):
            async with scheduled_question_scope(receiver_pool, task, prompt):
                await dispatch_body(prompt)
        receiver_pool.witness_failure = False
        with pytest.raises(PolicyUnavailableError, match="runtime input is unbound"):
            async with scheduled_question_scope(receiver_pool, task, prompt):
                pass
        async with scheduled_question_scope(receiver_pool, task, prompt):
            await dispatch_body(prompt)
        assert current_scheduled_question(receiver_pool) is None
        assert not receiver_writer.pending
        from butlers.chronicler.location_catalog_copies import (
            CatalogServerCopyLifetime,
            _server_copy_scope,
            _ServerCopyScope,
        )

        # Genuine infrastructure traffic has no receiving CLI invocation. Its
        # actual constructor-fixed ASGI lifetime must survive the online source
        # challenge, birth and same-writer schedule before response completion.
        cli_token = _current_tool_copy.set(None)
        emitted = []

        async def native_handler(scope, request, send):
            result = await receive()
            assert result["status"] == "scheduled"
            active = _server_copy_scope.get()
            selected = next(reversed(receiver_pool.inputs.values()))
            assert selected["server_request"] == active.request
            assert selected["tool_generation"] is None and selected["receiving_session"] is None
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"synthetic receipt"})

        try:
            app = CatalogServerCopyLifetime(native_handler, butler_name="relationship")
            before = len(receiver_pool.inputs)
            await app({"type": "http"}, AsyncMock(), AsyncMock(side_effect=emitted.append))
            assert len(receiver_pool.inputs) == before + 1
            actual_input = next(reversed(receiver_pool.inputs.values()))
            assert actual_input["receiving_generation"] in receiver_pool.server_finished
            assert _server_copy_scope.get() is None
            fake_scope = _ServerCopyScope(uuid.uuid4(), [], target="relationship")
            fake_token = _server_copy_scope.set(fake_scope)
            before = len(receiver_pool.inputs)
            try:
                assert (await receive())["status"] == "error"
                assert len(receiver_pool.inputs) == before
            finally:
                _server_copy_scope.reset(fake_token)
        finally:
            _current_tool_copy.reset(cli_token)
        # Real registered receiving tools, software SQL/transport doubles:
        # an unprocessed server/task copy can close after its actual attempt
        # lifetime. A locator, active lifetime or missing attempt cannot close.
        from butlers.chronicler.location_copy_transport import routed_owning_tool
        from butlers.chronicler.location_delegation_disposal import _REDUCED_TASK

        terminal_decision = uuid.uuid4()
        loan = source_pool.loans[actual_input["loan_id"]]
        question_row = {
            "question_generation": str(question),
            "ledger_id": str(ledger),
            "body_digest": body_digest.hex(),
            "parent_count": 1,
            "complete_input": True,
            "loans": [{k: v.hex() if isinstance(v, bytes) else str(v) for k, v in loan.items()}],
        }
        plan = {
            "decision_id": str(terminal_decision),
            "manifest_digest": (b"m" * 32).hex(),
            "question_cohort": [question_row],
        }

        async def routed_source(tool_name, args):
            assert tool_name == "route"
            assert args == {
                "target_butler": "chronicler",
                "tool_name": "chronicler_location_retention_status",
                "args": {"decision_id": str(terminal_decision)},
            }
            return {"result": plan}

        receiver_runtime.registry = SimpleNamespace(call_tool=routed_source)
        receiver_runtime.routed_tool = lambda target, tool_name, args: routed_owning_tool(
            receiver_runtime,
            target,
            tool_name,
            args,
        )
        receipt_tool = registered["location_retention_prepare_questions"]
        status_tool = registered["location_retention_question_status"]
        generation = actual_input["receiving_generation"]
        terminal_task = receiver_pool.schedules[generation]["task_id"]
        saved_finished = receiver_pool.server_finished.pop(generation)
        assert (await receipt_tool(terminal_decision))["receipt_ids"] == []
        assert generation in receiver_pool.floors and generation not in receiver_pool.dispositions
        assert receiver_pool.tasks[terminal_task] != _REDUCED_TASK
        before_claims = len(receiver_pool.claims)
        with pytest.raises(PolicyUnavailableError, match="receiving input is fenced"):
            async with scheduled_question_scope(
                receiver_pool,
                terminal_task,
                receiver_pool.tasks[terminal_task],
            ):
                await dispatch_body(receiver_pool.tasks[terminal_task])
        assert len(receiver_pool.claims) == before_claims
        receiver_pool.server_finished[generation] = saved_finished
        original_task = receiver_pool.tasks[terminal_task]
        receiver_pool.tasks[terminal_task] += " independent change"
        with pytest.raises(PolicyUnavailableError, match="task body changed"):
            await receipt_tool(terminal_decision)
        assert generation not in receiver_pool.dispositions
        receiver_pool.tasks[terminal_task] = original_task
        trace.clear()
        closed = await receipt_tool(terminal_decision)
        assert len(closed["receipt_ids"]) == 1
        receipt = uuid.UUID(closed["receipt_ids"][0])
        observed = await status_tool(terminal_decision, receipt)
        assert observed["body_digest"] == body_digest.hex()
        assert observed["loan_id"] == str(loan["loan_id"])
        assert observed["receiving_generation"] == str(generation)
        assert receiver_pool.tasks[terminal_task] == _REDUCED_TASK
        assert trace.index("receiver:floor") < trace.index("receiver:reduce")
        assert trace.index("receiver:reduce") < trace.index("receiver:terminal")
        assert trace.index("receiver:terminal") < trace.index("receiver:commit")
        assert (await receipt_tool(terminal_decision)) == closed
        receiver_pool.unknown_terminal = True
        with pytest.raises(PolicyUnavailableError, match="receipt is unavailable"):
            await status_tool(terminal_decision, receipt)
        receiver_pool.unknown_terminal = False
        # Exact incarnation/source/body are mandatory, not a caller verdict.
        question_row["complete_input"] = False
        with pytest.raises(PolicyUnavailableError, match="cohort differs"):
            await receipt_tool(terminal_decision)
        question_row["complete_input"] = True
        # Installed constructor ordinary ingress requires a positive fixed
        # source-owned job birth. Neither missing native data nor a caller's
        # ordinary label can manufacture that classification.
        from datetime import date

        from butlers.chronicler.location_ordinary_delegation import birthday_fields

        source_runtime.name, receiver_runtime.name = "relationship", "finance"
        canonical.clear()
        canonical.update(birthday_fields(date(2026, 4, 1)), id=ledger, status="pending")
        body_digest = question_digest(canonical)
        source_pool.native_missing = True
        source_pool.ordinary = {
            "source_generation": uuid.uuid4(),
            "ledger_id": ledger,
            "render_date": date(2026, 4, 1),
            "producer_kind": "birthday_gift_budget_ask",
            "body_digest": body_digest,
        }
        registered = _register(butler_name="finance", pool=receiver_pool)
        # Reuse the actual registered infrastructure request lifetime; no
        # receiving CLI ContextVar is invented for a deterministic job.
        cli_token = _current_tool_copy.set(None)
        before_inputs, before_loans = len(receiver_pool.inputs), len(source_pool.loans)
        try:

            async def ordinary_handler(scope, request, send):
                result = await receive()
                assert result["status"] == "scheduled"
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"synthetic receipt"})

            app = CatalogServerCopyLifetime(ordinary_handler, butler_name="finance")
            await app({"type": "http"}, AsyncMock(), AsyncMock())
            assert len(receiver_pool.inputs) == before_inputs
            assert len(source_pool.loans) == before_loans
            assert not receiver_writer.pending
            # These failures pass through the same real pending/source
            # challenge path, not a fabricated ordinary verdict response.
            for mode in ("missing", "body", "mixed"):
                original = source_pool.ordinary.copy()
                if mode == "missing":
                    source_pool.ordinary = None
                elif mode == "body":
                    source_pool.ordinary["body_digest"] = b"x" * 32
                else:
                    source_pool.native_missing = False

                async def refused_handler(scope, request, send):
                    assert (await receive())["status"] == "error"
                    await send({"type": "http.response.body", "body": b""})

                await CatalogServerCopyLifetime(refused_handler, butler_name="finance")(
                    {"type": "http"}, AsyncMock(), AsyncMock()
                )
                source_pool.ordinary = original
                source_pool.native_missing = True
                assert len(receiver_pool.inputs) == before_inputs
                assert len(source_pool.loans) == before_loans
            await app({"type": "http"}, AsyncMock(), AsyncMock())
        finally:
            _current_tool_copy.reset(cli_token)
    finally:
        _current_tool_copy.reset(token)
        clear_writer(receiver_pool, receiver_writer)

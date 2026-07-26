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
from unittest.mock import AsyncMock

import pytest

from butlers.config import ButlerType
from butlers.core_tools import _delegation
from butlers.core_tools._base import ToolContext
from butlers.core_tools._delegation import register_delegation_tools

pytestmark = pytest.mark.unit


def _register(butler_name: str = "finance", butler_type=ButlerType.BUTLER, switchboard_client=None):
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
        pool=AsyncMock(),
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
            AsyncMock(return_value={"target_butler": "relationship", "status": "pending"}),
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

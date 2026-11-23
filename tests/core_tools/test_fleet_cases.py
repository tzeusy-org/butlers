"""Unit tests for the fleet case file contribution tools (bu-8cdl1.7 Slice 3).

Mirrors the fake-``_core_tool``-registry harness from
``tests/core_tools/test_domain_events.py``. The tool logic here only touches
``butlers.core.fleet_cases`` and ``_switchboard_route_dispatch.
dispatch_via_switchboard_route``, both cleanly monkeypatchable at this level
-- no real database or network round trip needed to pin the routing
decision (switchboard writes directly; every other butler forwards through
Switchboard's route()).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.config import ButlerType
from butlers.core.fleet_cases import FleetCaseError
from butlers.core_tools import _fleet_cases
from butlers.core_tools._base import ToolContext
from butlers.core_tools._fleet_cases import register_fleet_case_tools

pytestmark = pytest.mark.unit

_CASE_ID = "11111111-1111-1111-1111-111111111111"
_CASE = {
    "id": _CASE_ID,
    "correlation_key": "health:owner:respiratory-illness",
    "state": "open",
    "posture": "silent",
    "outcome": None,
    "opened_at": "2026-09-01T00:00:00+00:00",
    "updated_at": "2026-09-01T00:00:00+00:00",
    "closed_at": None,
}


def _register(butler_name: str = "finance", switchboard_client=None) -> dict[str, callable]:
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
        butler_type=ButlerType.STAFFER if butler_name == "switchboard" else ButlerType.BUTLER,
        is_switchboard=(butler_name == "switchboard"),
        is_messenger=False,
        route_metrics=None,
    )
    register_fleet_case_tools(ctx, mcp, _core_tool)
    return registered


# Spec: REQ-fleet-case-file-008
# Partial fake-registration/patched-handler behavior; not real MCP/SQL proof.
def test_all_fleet_case_tools_registered_for_a_domain_butler():
    registered = _register(butler_name="finance")
    assert set(registered) == {
        "find_open_case",
        "open_case",
        "contribute_case_evidence",
        "propose_case_posture",
        "close_case",
        "record_case_link",
        "read_case",
    }


# Spec: REQ-fleet-case-file-008
# Partial fake-registration/patched-handler behavior; not real MCP/SQL proof.
def test_all_fleet_case_tools_also_registered_for_switchboard():
    """Unlike domain_events/delegation, fleet_cases tools are NOT STAFFER-excluded:
    Switchboard is the sole write authority and needs them registered on its
    own daemon to actually perform writes forwarded from other butlers."""
    registered = _register(butler_name="switchboard")
    assert set(registered) == {
        "find_open_case",
        "open_case",
        "contribute_case_evidence",
        "propose_case_posture",
        "close_case",
        "record_case_link",
        "read_case",
        # RFC 0038 D7: the service-only twins the fleet_case.* relays target.
        "open_case_service",
        "propose_case_posture_service",
        "close_case_service",
        "record_case_link_service",
    }


class TestFindOpenCase:
    async def test_delegates_to_the_data_layer_and_wraps_the_result(self, monkeypatch):
        registered = _register(butler_name="finance")
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, "find_open_case", AsyncMock(return_value=_CASE)
        )
        result = await registered["find_open_case"](
            correlation_key="health:owner:respiratory-illness"
        )
        assert result == {"status": "ok", "case": _CASE}

    async def test_no_open_case_is_a_normal_ok_result_with_null_case(self, monkeypatch):
        registered = _register(butler_name="finance")
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, "find_open_case", AsyncMock(return_value=None)
        )
        result = await registered["find_open_case"](correlation_key="no-such-key")
        assert result == {"status": "ok", "case": None}


class TestOpenCase:
    async def test_switchboard_writes_directly_without_dispatching(self, monkeypatch):
        registered = _register(butler_name="switchboard")
        open_case_mock = AsyncMock(return_value=_CASE)
        dispatch_mock = AsyncMock()
        monkeypatch.setattr(_fleet_cases.fleet_cases, "open_case", open_case_mock)
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", dispatch_mock)

        result = await registered["open_case"](correlation_key="health:owner:respiratory-illness")

        assert result == {"status": "ok", "case": _CASE}
        _, kwargs = open_case_mock.await_args
        assert kwargs == {"correlation_key": "health:owner:respiratory-illness"}
        dispatch_mock.assert_not_awaited()

    async def test_switchboard_error_is_returned_as_an_error_result(self, monkeypatch):
        registered = _register(butler_name="switchboard")
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "open_case",
            AsyncMock(side_effect=FleetCaseError("An open case already exists")),
        )
        result = await registered["open_case"](correlation_key="dup-key")
        assert result == {"status": "error", "error": "An open case already exists"}

    # Spec: REQ-fleet-case-file-008
    # Partial fake-registration/patched-handler behavior; not real MCP/SQL proof.
    async def test_non_switchboard_forwards_through_switchboard_route(self, monkeypatch):
        registered = _register(butler_name="finance", switchboard_client=object())
        dispatch_mock = AsyncMock(return_value=({"status": "ok", "case": _CASE}, None, False))
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", dispatch_mock)

        result = await registered["open_case"](correlation_key="health:owner:respiratory-illness")

        assert result == {"status": "ok", "case": _CASE}
        _, kwargs = dispatch_mock.await_args
        assert kwargs["target_butler"] == "switchboard"
        assert kwargs["tool_name"] == "open_case"
        assert kwargs["args"] == {"correlation_key": "health:owner:respiratory-illness"}

    async def test_route_error_surfaces_as_an_error_result(self, monkeypatch):
        registered = _register(butler_name="finance", switchboard_client=object())
        monkeypatch.setattr(
            _fleet_cases,
            "dispatch_via_switchboard_route",
            AsyncMock(return_value=(None, "Switchboard unreachable", True)),
        )
        result = await registered["open_case"](correlation_key="health:owner:respiratory-illness")
        assert result == {"status": "error", "error": "Switchboard unreachable", "retryable": True}


class TestContributeCaseEvidence:
    # Spec: REQ-fleet-case-file-004
    # Partial fake-registration/patched-handler behavior; not real MCP/SQL proof.
    async def test_never_dispatches_and_attributes_to_the_calling_butler(self, monkeypatch):
        registered = _register(butler_name="finance")
        evidence_row = {"id": "e1", "case_id": _CASE_ID, "contributor": "finance"}
        contribute_mock = AsyncMock(return_value=(evidence_row, True))
        dispatch_mock = AsyncMock()
        attention_result = {"bypass": True, "reason": "urgent_case_bypass"}
        monkeypatch.setattr(_fleet_cases.fleet_cases, "contribute_evidence", contribute_mock)
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", dispatch_mock)
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, "get_case_summary", AsyncMock(return_value=_CASE)
        )
        evaluate_mock = AsyncMock(return_value=attention_result)
        monkeypatch.setattr(_fleet_cases.fleet_cases, "evaluate_case_attention", evaluate_mock)

        result = await registered["contribute_case_evidence"](
            case_id=_CASE_ID, kind="candidate", ref="insight-42"
        )

        assert result == {
            "status": "ok",
            "evidence": evidence_row,
            "newly_recorded": True,
            "case_attention": attention_result,
            "link": None,
        }
        dispatch_mock.assert_not_awaited()
        _, kwargs = contribute_mock.await_args
        assert kwargs["contributor"] == "finance"
        assert kwargs["case_id"] == _CASE_ID
        assert kwargs["kind"] == "candidate"
        assert kwargs["ref"] == "insight-42"
        _, attention_kwargs = evaluate_mock.await_args
        assert attention_kwargs["origin_butler"] == "finance"
        assert attention_kwargs["correlation_key"] == _CASE["correlation_key"]
        assert attention_kwargs["posture"] == _CASE["posture"]
        assert attention_kwargs["state"] == _CASE["state"]

    # Spec: REQ-fleet-case-file-009
    # Partial fake-registration/patched-handler behavior; not real MCP/SQL proof.
    async def test_repeat_contribution_skips_the_attention_check(self, monkeypatch):
        """A no-op re-report (newly_recorded=False) cannot itself trigger a
        quiet-hours bypass -- evaluating it again would let a single
        contributor manufacture repeated bypasses just by re-reporting."""
        registered = _register(butler_name="finance")
        evidence_row = {"id": "e1", "case_id": _CASE_ID, "contributor": "finance"}
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "contribute_evidence",
            AsyncMock(return_value=(evidence_row, False)),
        )
        evaluate_mock = AsyncMock()
        monkeypatch.setattr(_fleet_cases.fleet_cases, "evaluate_case_attention", evaluate_mock)

        result = await registered["contribute_case_evidence"](
            case_id=_CASE_ID, kind="candidate", ref="insight-42"
        )

        assert result == {
            "status": "ok",
            "evidence": evidence_row,
            "newly_recorded": False,
            "case_attention": None,
            "link": None,
        }
        evaluate_mock.assert_not_awaited()

    async def test_data_layer_error_is_returned_as_an_error_result(self, monkeypatch):
        registered = _register(butler_name="finance")
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "contribute_evidence",
            AsyncMock(side_effect=FleetCaseError("No fleet case with id='bogus'.")),
        )
        result = await registered["contribute_case_evidence"](
            case_id="bogus", kind="candidate", ref="insight-42"
        )
        assert result == {"status": "error", "error": "No fleet case with id='bogus'."}

    # Spec: REQ-fleet-case-file-013
    # Partial fake-registration/patched-handler behavior; not real MCP/SQL proof.
    async def test_linkable_kind_writes_the_link_directly_on_switchboard(self, monkeypatch):
        """RFC 0032 Slice 7: a newly-recorded contribution whose kind is one
        of LINK_KINDS also binds the case to that ledger entry."""
        registered = _register(butler_name="switchboard")
        evidence_row = {"id": "e1", "case_id": _CASE_ID, "contributor": "switchboard"}
        link_row = {"id": "l1", "case_id": _CASE_ID, "link_kind": "insight_candidate"}
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "contribute_evidence",
            AsyncMock(return_value=(evidence_row, True)),
        )
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, "get_case_summary", AsyncMock(return_value=_CASE)
        )
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "evaluate_case_attention",
            AsyncMock(return_value={"bypass": False, "reason": "not_urgent"}),
        )
        write_link_mock = AsyncMock(return_value=(link_row, True))
        monkeypatch.setattr(_fleet_cases.fleet_cases, "write_case_link", write_link_mock)
        dispatch_mock = AsyncMock()
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", dispatch_mock)

        result = await registered["contribute_case_evidence"](
            case_id=_CASE_ID, kind="insight_candidate", ref="candidate-1"
        )

        assert result["link"] == link_row
        dispatch_mock.assert_not_awaited()
        _, kwargs = write_link_mock.await_args
        assert kwargs == {
            "case_id": _CASE_ID,
            "link_kind": "insight_candidate",
            "ref": "candidate-1",
        }

    async def test_linkable_kind_forwards_through_switchboard_route_for_a_domain_butler(
        self, monkeypatch
    ):
        registered = _register(butler_name="health", switchboard_client=object())
        evidence_row = {"id": "e1", "case_id": _CASE_ID, "contributor": "health"}
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "contribute_evidence",
            AsyncMock(return_value=(evidence_row, True)),
        )
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, "get_case_summary", AsyncMock(return_value=_CASE)
        )
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "evaluate_case_attention",
            AsyncMock(return_value={"bypass": False, "reason": "not_urgent"}),
        )
        link_row = {"id": "l1", "case_id": _CASE_ID, "link_kind": "owner_condition"}
        dispatch_mock = AsyncMock(return_value=({"status": "ok", "link": link_row}, None, False))
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", dispatch_mock)

        result = await registered["contribute_case_evidence"](
            case_id=_CASE_ID, kind="owner_condition", ref="condition-1"
        )

        assert result["link"] == link_row
        _, kwargs = dispatch_mock.await_args
        assert kwargs["target_butler"] == "switchboard"
        assert kwargs["tool_name"] == "record_case_link"
        assert kwargs["args"] == {
            "case_id": _CASE_ID,
            "link_kind": "owner_condition",
            "ref": "condition-1",
        }

    # Spec: REQ-fleet-case-file-013
    # Partial fake-registration/patched-handler behavior; not real MCP/SQL proof.
    async def test_urgent_bypass_with_a_ledger_id_also_writes_an_attention_record_link(
        self, monkeypatch
    ):
        registered = _register(butler_name="switchboard")
        evidence_row = {"id": "e1", "case_id": _CASE_ID, "contributor": "switchboard"}
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "contribute_evidence",
            AsyncMock(return_value=(evidence_row, True)),
        )
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, "get_case_summary", AsyncMock(return_value=_CASE)
        )
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "evaluate_case_attention",
            AsyncMock(
                return_value={
                    "bypass": True,
                    "reason": "urgent_case_bypass",
                    "attention_ledger_id": "attn-1",
                }
            ),
        )
        write_link_mock = AsyncMock(return_value=({"id": "l1"}, True))
        monkeypatch.setattr(_fleet_cases.fleet_cases, "write_case_link", write_link_mock)

        await registered["contribute_case_evidence"](
            case_id=_CASE_ID, kind="session", ref="session-1"
        )

        _, kwargs = write_link_mock.await_args
        assert kwargs == {
            "case_id": _CASE_ID,
            "link_kind": "attention_record",
            "ref": "attn-1",
        }

    # Spec: REQ-fleet-case-file-013
    # Partial fake-registration/patched-handler behavior; not real MCP/SQL proof.
    async def test_bypass_without_a_ledger_id_writes_no_link(self, monkeypatch):
        """record_attention_event is best-effort and can return None; a
        missing ledger id must never be passed to write_case_link as a
        blank/None ref."""
        registered = _register(butler_name="switchboard")
        evidence_row = {"id": "e1", "case_id": _CASE_ID, "contributor": "switchboard"}
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "contribute_evidence",
            AsyncMock(return_value=(evidence_row, True)),
        )
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, "get_case_summary", AsyncMock(return_value=_CASE)
        )
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "evaluate_case_attention",
            AsyncMock(
                return_value={
                    "bypass": True,
                    "reason": "urgent_case_bypass",
                    "attention_ledger_id": None,
                }
            ),
        )
        write_link_mock = AsyncMock()
        monkeypatch.setattr(_fleet_cases.fleet_cases, "write_case_link", write_link_mock)

        await registered["contribute_case_evidence"](
            case_id=_CASE_ID, kind="session", ref="session-1"
        )

        write_link_mock.assert_not_awaited()


class TestProposeCasePosture:
    async def test_switchboard_writes_directly(self, monkeypatch):
        registered = _register(butler_name="switchboard")
        updated = {**_CASE, "posture": "urgent"}
        attention_result = {"bypass": True, "reason": "urgent_case_bypass"}
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, "propose_posture", AsyncMock(return_value=updated)
        )
        dispatch_mock = AsyncMock()
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", dispatch_mock)
        evaluate_mock = AsyncMock(return_value=attention_result)
        monkeypatch.setattr(_fleet_cases.fleet_cases, "evaluate_case_attention", evaluate_mock)
        write_link_mock = AsyncMock()
        monkeypatch.setattr(_fleet_cases.fleet_cases, "write_case_link", write_link_mock)

        result = await registered["propose_case_posture"](case_id=_CASE_ID, posture="urgent")

        assert result == {"status": "ok", "case": updated, "case_attention": attention_result}
        dispatch_mock.assert_not_awaited()
        _, kwargs = evaluate_mock.await_args
        assert kwargs["origin_butler"] == "switchboard"
        assert kwargs["correlation_key"] == updated["correlation_key"]
        assert kwargs["posture"] == "urgent"
        assert kwargs["state"] == updated["state"]
        # attention_result has no attention_ledger_id -- no link write attempted.
        write_link_mock.assert_not_awaited()

    # Spec: REQ-fleet-case-file-013
    # Partial fake-registration/patched-handler behavior; not real MCP/SQL proof.
    async def test_switchboard_bypass_with_a_ledger_id_writes_an_attention_record_link(
        self, monkeypatch
    ):
        registered = _register(butler_name="switchboard")
        updated = {**_CASE, "posture": "urgent"}
        attention_result = {
            "bypass": True,
            "reason": "urgent_case_bypass",
            "attention_ledger_id": "attn-2",
        }
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, "propose_posture", AsyncMock(return_value=updated)
        )
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "evaluate_case_attention",
            AsyncMock(return_value=attention_result),
        )
        write_link_mock = AsyncMock(return_value=({"id": "l1"}, True))
        monkeypatch.setattr(_fleet_cases.fleet_cases, "write_case_link", write_link_mock)

        await registered["propose_case_posture"](case_id=_CASE_ID, posture="urgent")

        _, kwargs = write_link_mock.await_args
        assert kwargs == {
            "case_id": _CASE_ID,
            "link_kind": "attention_record",
            "ref": "attn-2",
        }

    async def test_non_switchboard_forwards_through_switchboard_route(self, monkeypatch):
        registered = _register(butler_name="health", switchboard_client=object())
        updated = {**_CASE, "posture": "urgent"}
        dispatch_mock = AsyncMock(return_value=({"status": "ok", "case": updated}, None, False))
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", dispatch_mock)

        result = await registered["propose_case_posture"](case_id=_CASE_ID, posture="urgent")

        assert result == {"status": "ok", "case": updated}
        _, kwargs = dispatch_mock.await_args
        assert kwargs["target_butler"] == "switchboard"
        assert kwargs["tool_name"] == "propose_case_posture"
        assert kwargs["args"] == {"case_id": _CASE_ID, "posture": "urgent"}


class TestCloseCase:
    async def test_switchboard_writes_directly(self, monkeypatch):
        registered = _register(butler_name="switchboard")
        closed = {**_CASE, "state": "closed", "outcome": "resolved"}
        monkeypatch.setattr(_fleet_cases.fleet_cases, "close_case", AsyncMock(return_value=closed))
        dispatch_mock = AsyncMock()
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", dispatch_mock)

        result = await registered["close_case"](case_id=_CASE_ID, outcome="resolved")

        assert result == {"status": "ok", "case": closed}
        dispatch_mock.assert_not_awaited()

    async def test_non_switchboard_forwards_through_switchboard_route(self, monkeypatch):
        registered = _register(butler_name="health", switchboard_client=object())
        closed = {**_CASE, "state": "closed", "outcome": "resolved"}
        dispatch_mock = AsyncMock(return_value=({"status": "ok", "case": closed}, None, False))
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", dispatch_mock)

        result = await registered["close_case"](case_id=_CASE_ID, outcome="resolved")

        assert result == {"status": "ok", "case": closed}
        _, kwargs = dispatch_mock.await_args
        assert kwargs["target_butler"] == "switchboard"
        assert kwargs["tool_name"] == "close_case"
        assert kwargs["args"] == {"case_id": _CASE_ID, "outcome": "resolved"}


class TestRecordCaseLink:
    """RFC 0032 Slice 7's directly callable tool -- same is_switchboard split
    as open_case/propose_case_posture/close_case."""

    async def test_switchboard_writes_directly(self, monkeypatch):
        registered = _register(butler_name="switchboard")
        link_row = {"id": "l1", "case_id": _CASE_ID, "link_kind": "insight_candidate"}
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "write_case_link",
            AsyncMock(return_value=(link_row, True)),
        )
        dispatch_mock = AsyncMock()
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", dispatch_mock)

        result = await registered["record_case_link"](
            case_id=_CASE_ID, link_kind="insight_candidate", ref="candidate-1"
        )

        assert result == {"status": "ok", "link": link_row, "newly_recorded": True}
        dispatch_mock.assert_not_awaited()

    async def test_switchboard_error_is_returned_as_an_error_result(self, monkeypatch):
        registered = _register(butler_name="switchboard")
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "write_case_link",
            AsyncMock(side_effect=FleetCaseError("link_kind='bogus' must be one of [...].")),
        )
        result = await registered["record_case_link"](
            case_id=_CASE_ID, link_kind="bogus", ref="ref-1"
        )
        assert result == {
            "status": "error",
            "error": "link_kind='bogus' must be one of [...].",
        }

    async def test_non_switchboard_forwards_through_switchboard_route(self, monkeypatch):
        registered = _register(butler_name="finance", switchboard_client=object())
        link_row = {"id": "l1", "case_id": _CASE_ID, "link_kind": "owner_condition"}
        dispatch_mock = AsyncMock(
            return_value=(
                {"status": "ok", "link": link_row, "newly_recorded": True},
                None,
                False,
            )
        )
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", dispatch_mock)

        result = await registered["record_case_link"](
            case_id=_CASE_ID, link_kind="owner_condition", ref="condition-1"
        )

        assert result == {"status": "ok", "link": link_row, "newly_recorded": True}
        _, kwargs = dispatch_mock.await_args
        assert kwargs["target_butler"] == "switchboard"
        assert kwargs["tool_name"] == "record_case_link"
        assert kwargs["args"] == {
            "case_id": _CASE_ID,
            "link_kind": "owner_condition",
            "ref": "condition-1",
        }

    async def test_route_error_surfaces_as_an_error_result(self, monkeypatch):
        registered = _register(butler_name="finance", switchboard_client=object())
        monkeypatch.setattr(
            _fleet_cases,
            "dispatch_via_switchboard_route",
            AsyncMock(return_value=(None, "Switchboard unreachable", True)),
        )
        result = await registered["record_case_link"](
            case_id=_CASE_ID, link_kind="owner_condition", ref="condition-1"
        )
        assert result == {"status": "error", "error": "Switchboard unreachable", "retryable": True}


_WRITES = [
    ("open_case", {"correlation_key": "health:owner:respiratory-illness"}, "fleet_case.open"),
    (
        "propose_case_posture",
        {"case_id": _CASE_ID, "posture": "urgent"},
        "fleet_case.propose_posture",
    ),
    ("close_case", {"case_id": _CASE_ID, "outcome": "resolved"}, "fleet_case.close"),
    (
        "record_case_link",
        {"case_id": _CASE_ID, "link_kind": "owner_condition", "ref": "condition-1"},
        "fleet_case.record_link",
    ),
]


def _mock_data_layer(monkeypatch) -> dict[str, AsyncMock]:
    mocks = {
        "open_case": AsyncMock(return_value=_CASE),
        "propose_posture": AsyncMock(return_value={**_CASE, "posture": "urgent"}),
        "close_case": AsyncMock(return_value={**_CASE, "state": "closed"}),
        "write_case_link": AsyncMock(return_value=({"id": "l1"}, True)),
        "evaluate_case_attention": AsyncMock(
            return_value={"bypass": True, "attention_ledger_id": "attn-9"}
        ),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(_fleet_cases.fleet_cases, name, mock)
    return mocks


class TestServiceTwins:
    """RFC 0038 D7: each Switchboard write tool has a service-only twin the
    ``fleet_case.*`` relays target; the twin runs the same Switchboard write."""

    def test_twins_are_registered_only_on_switchboard(self):
        registered = _register(butler_name="finance")
        assert not {name for name in registered if name.endswith("_service")}

    @pytest.mark.parametrize(("tool", "args", "_relay"), _WRITES, ids=[w[0] for w in _WRITES])
    async def test_a_twin_writes_exactly_as_its_tool_does_on_switchboard(
        self, monkeypatch, tool, args, _relay
    ):
        registered = _register(butler_name="switchboard")
        mocks = _mock_data_layer(monkeypatch)
        monkeypatch.setattr(_fleet_cases, "dispatch_via_switchboard_route", AsyncMock())

        via_tool = await registered[tool](**args)
        tool_calls = {name: mock.await_args_list[:] for name, mock in mocks.items()}
        for mock in mocks.values():
            mock.reset_mock()
        via_twin = await registered[f"{tool}_service"](**args)
        twin_calls = {name: mock.await_args_list[:] for name, mock in mocks.items()}

        assert via_twin == via_tool
        assert via_twin["status"] == "ok"
        assert twin_calls == tool_calls
        _fleet_cases.dispatch_via_switchboard_route.assert_not_awaited()

    @pytest.mark.parametrize("tool", ["open_case", "close_case", "record_case_link"])
    async def test_a_twin_reports_a_data_layer_refusal_as_an_error(self, monkeypatch, tool):
        registered = _register(butler_name="switchboard")
        target = {"record_case_link": "write_case_link"}.get(tool, tool)
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, target, AsyncMock(side_effect=FleetCaseError("refused"))
        )
        args = next(args for name, args, _ in _WRITES if name == tool)

        assert await registered[f"{tool}_service"](**args) == {
            "status": "error",
            "error": "refused",
        }


class _RelayedSwitchboard:
    """A domain daemon's Switchboard client; with the switch on only its URL is used."""

    def __init__(self, url: str | None = "http://butlers-control:41100/mcp") -> None:
        self.transport = SimpleNamespace(url=url)


@pytest.fixture
def relay_on(monkeypatch):
    """Switch on; record call_relay; the generic route must not be used."""
    import butlers.core.service_plane.client as relay_client

    monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")
    monkeypatch.setattr(
        _fleet_cases,
        "dispatch_via_switchboard_route",
        AsyncMock(side_effect=AssertionError("generic route used")),
    )
    relay = SimpleNamespace(calls=[], reply=None, failure=None)

    async def call_relay(name, args, *, daemon, switchboard_url, **kwargs):
        relay.calls.append((name, args, daemon, switchboard_url))
        if relay.failure is not None:
            raise relay.failure
        return relay.reply

    monkeypatch.setattr(relay_client, "call_relay", call_relay)
    return relay


class TestSwitchOnRelay:
    @pytest.mark.parametrize(("tool", "args", "relay_name"), _WRITES, ids=[w[0] for w in _WRITES])
    async def test_a_domain_butler_writes_through_its_named_relay(
        self, relay_on, tool, args, relay_name
    ):
        registered = _register(butler_name="health", switchboard_client=_RelayedSwitchboard())
        relay_on.reply = {"result": {"status": "ok", "case": _CASE}, "transport": "mcp"}

        result = await registered[tool](**args)

        assert result == {"status": "ok", "case": _CASE}
        assert relay_on.calls == [(relay_name, args, "health", "http://butlers-control:41100/mcp")]

    async def test_a_linkable_evidence_kind_links_through_the_relay(self, relay_on, monkeypatch):
        registered = _register(butler_name="health", switchboard_client=_RelayedSwitchboard())
        monkeypatch.setattr(
            _fleet_cases.fleet_cases,
            "contribute_evidence",
            AsyncMock(return_value=({"id": "e1"}, True)),
        )
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, "get_case_summary", AsyncMock(return_value=None)
        )
        relay_on.reply = {"result": {"status": "ok", "link": {"id": "l1"}}, "transport": "mcp"}

        result = await registered["contribute_case_evidence"](
            case_id=_CASE_ID, kind="owner_condition", ref="condition-1"
        )

        assert result["link"] == {"id": "l1"}
        assert [call[0] for call in relay_on.calls] == ["fleet_case.record_link"]

    async def test_relay_failures_and_route_errors_are_error_results(self, relay_on):
        from butlers.core.service_plane.client import RelayUnavailable
        from butlers.core.service_plane.relays import RelayRefused

        registered = _register(butler_name="finance", switchboard_client=_RelayedSwitchboard())
        cases = [
            (RelayRefused("caller_not_allowed"), None, False),
            (RelayRefused("unknown_relay", status=404), None, True),
            (RelayUnavailable("http_502"), None, True),
            (OSError("token missing"), None, True),
            (None, {"error": "ConnectionError: down", "retryable": True}, True),
            (None, {"result": {"status": "error", "error": "closed"}}, False),
        ]
        for failure, reply, retryable in cases:
            relay_on.failure, relay_on.reply = failure, reply
            result = await registered["close_case"](case_id=_CASE_ID, outcome="resolved")
            assert result["status"] == "error"
            assert result.get("retryable", False) is retryable, (failure, reply)

    async def test_without_a_configured_switchboard_nothing_is_sent(self, relay_on):
        for client in (None, _RelayedSwitchboard(url=None)):
            registered = _register(butler_name="finance", switchboard_client=client)
            result = await registered["open_case"](correlation_key="k")
            assert result["status"] == "error" and result["retryable"] is True
        assert relay_on.calls == []

    async def test_switchboard_itself_never_relays(self, relay_on, monkeypatch):
        registered = _register(butler_name="switchboard")
        _mock_data_layer(monkeypatch)

        assert (await registered["open_case"](correlation_key="k"))["status"] == "ok"
        assert relay_on.calls == []


class TestReadCase:
    async def test_returns_the_case_with_evidence_and_links(self, monkeypatch):
        registered = _register(butler_name="finance")
        full_case = {**_CASE, "evidence": [], "links": []}
        monkeypatch.setattr(
            _fleet_cases.fleet_cases, "read_case", AsyncMock(return_value=full_case)
        )
        result = await registered["read_case"](case_id=_CASE_ID)
        assert result == {"status": "ok", "case": full_case}

    async def test_missing_case_is_an_error_result(self, monkeypatch):
        registered = _register(butler_name="finance")
        monkeypatch.setattr(_fleet_cases.fleet_cases, "read_case", AsyncMock(return_value=None))
        result = await registered["read_case"](case_id=_CASE_ID)
        assert result == {"status": "error", "error": f"No fleet case with id={_CASE_ID!r}."}

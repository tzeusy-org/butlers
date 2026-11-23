"""A dashboard call's verified lineage: the owner acting directly, with no goal (RFC 0038 D7).

The dashboard's calls to model-reachable tools are forwarded with an assertion
whose principal is ``dashboard``, origin ``owner`` and ``goal_id`` null. The
domain binds that as lineage like any other verified call: it carries owner
authority, stamps no taint, approves nothing by itself, and a gated call that
messages someone other than the owner still parks for the owner's approval.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.capability_broker.assertions import dashboard_assertion_payload
from butlers.core.capability_json import arguments_digest
from butlers.core.goal_lineage import (
    VerifiedLineage,
    current_lineage,
    event_lineage,
    lineage_scope,
    taint_stamp,
)

pytestmark = pytest.mark.unit

_ARGS = {"message": "hi", "entity_id": str(uuid.uuid4())}


def _dashboard(args: dict[str, Any] = _ARGS) -> VerifiedLineage:
    return VerifiedLineage.from_payload(
        dashboard_assertion_payload(
            audience="messenger", capability="notify", arguments=args, now=1_800_000_000
        )
    )


def test_a_dashboard_payload_binds_the_owner_with_no_goal() -> None:
    lineage = _dashboard()
    assert (lineage.principal, lineage.origin) == ("dashboard", "owner")
    assert (lineage.goal_id, lineage.invocation_id, lineage.effect_id) == (None, None, None)
    assert (lineage.depth, lineage.exposed, lineage.approval_ref) == (0, False, None)
    assert lineage.covers("notify", _ARGS)
    assert not lineage.approves("notify", _ARGS)


def test_a_goal_payload_binds_its_goal() -> None:
    goal, invocation = uuid.uuid4(), uuid.uuid4()
    payload = {
        **dashboard_assertion_payload(
            audience="messenger", capability="notify", arguments=_ARGS, now=1_800_000_000
        ),
        "principal": "goal",
        "goal_id": str(goal),
        "invocation_id": str(invocation),
    }
    lineage = VerifiedLineage.from_payload(payload)
    assert (lineage.principal, lineage.goal_id, lineage.invocation_id) == (
        "goal",
        goal,
        invocation,
    )


def _lineage(**fields: Any) -> VerifiedLineage:
    base: dict[str, Any] = {
        "goal_id": uuid.uuid4(),
        "invocation_id": uuid.uuid4(),
        "depth": 0,
        "origin": "owner",
        "exposed": False,
        "capability": "notify",
        "args_digest": arguments_digest(_ARGS),
        "call_id": uuid.uuid4(),
    }
    return VerifiedLineage(**{**base, **fields})


def test_a_goal_lineage_defaults_to_the_goal_principal() -> None:
    assert _lineage().principal == "goal"


@pytest.mark.parametrize(
    "fields",
    [
        {"goal_id": None},
        {"invocation_id": None},
        {"goal_id": None, "invocation_id": None},
        # A dashboard lineage names no goal and no invocation.
        {"principal": "dashboard"},
        {"principal": "dashboard", "invocation_id": None},
        {"principal": "dashboard", "goal_id": None},
        {"principal": "dashboard", "goal_id": None, "invocation_id": None, "origin": "staffer"},
        {"principal": "dashboard", "goal_id": None, "invocation_id": None, "exposed": True},
        {"principal": "dashboard", "goal_id": None, "invocation_id": None, "depth": 1},
        {
            "principal": "dashboard",
            "goal_id": None,
            "invocation_id": None,
            "effect_id": uuid.uuid4(),
            "approval_ref": "action-1",
        },
        {"principal": "owner"},
    ],
)
def test_a_lineage_outside_the_two_principals_cannot_be_built(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _lineage(**fields)


def test_a_dashboard_call_stamps_no_taint_and_records_no_goal() -> None:
    with lineage_scope(_dashboard()):
        assert taint_stamp() == (None, None)
        assert event_lineage() == {
            "goal_id": None,
            "goal_invocation_id": None,
            "goal_effect_id": None,
            "goal_exposed": False,
        }


async def test_a_dashboard_call_writes_with_owner_authority() -> None:
    from butlers.core import fact_authority
    from butlers.modules.memory.content_authority import resolve_content_authority

    owner = uuid.uuid4()
    with (
        lineage_scope(_dashboard()),
        patch(
            "butlers.modules.memory.content_authority.fetch_owner_entity_id",
            AsyncMock(return_value=owner),
        ),
    ):
        stamped = await resolve_content_authority(None)
        assert (stamped.authority, stamped.entity_id) == ("owner", owner)
        assert fact_authority.current_fact_write_context().authority == "owner_device"


def test_a_dashboard_call_is_not_an_approved_execution() -> None:
    from butlers.modules.approvals.execution_context import get_approval_execution_context

    with lineage_scope(_dashboard()):
        assert get_approval_execution_context(tool_name="notify", tool_args=_ARGS) is None


def _gate(pool: Any, original: Any) -> Any:
    from butlers.modules.approvals.gate import _make_gate_wrapper

    return _make_gate_wrapper(
        tool_name="notify",
        original_fn=original,
        pool=pool,
        expiry_hours=72,
        risk_tier=MagicMock(value="medium"),
        rule_precedence=("contact_role", "standing_rule"),
        butler_name="messenger",
    )


_DOSSIER = {
    "_why": "Owner asked to send this",
    "_evidence": [],
    "_blast_radius": "self",
    "_reversibility": "compensable",
}


async def test_a_dashboard_send_to_someone_else_parks_for_the_owner() -> None:
    from butlers.modules.approvals.gate import RuleAdmission

    ran: list[dict[str, Any]] = []
    seen: list[VerifiedLineage | None] = []

    async def notify(message: str, entity_id: str) -> dict[str, Any]:
        ran.append({"message": message, "entity_id": entity_id})
        return {"status": "sent"}

    async def park(*_args: Any, **_kwargs: Any) -> None:
        seen.append(current_lineage())

    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=AssertionError("no approved action is looked up"))
    args = {**_ARGS, **_DOSSIER}
    with (
        lineage_scope(_dashboard(args)),
        patch(
            "butlers.modules.approvals.gate.resolve_action_target_contact",
            AsyncMock(return_value=None),
        ),
        patch(
            "butlers.modules.approvals.gate.admit_standing_rule",
            AsyncMock(return_value=RuleAdmission(None)),
        ),
        patch("butlers.modules.approvals.gate.park_pending_action", AsyncMock(side_effect=park)),
        patch("butlers.modules.approvals.gate.record_approval_event", AsyncMock()),
    ):
        result = await _gate(pool, notify)(**args)

    assert result["status"] == "pending_approval"
    assert ran == []
    assert len(seen) == 1 and seen[0] is not None and seen[0].principal == "dashboard"

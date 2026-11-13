"""D4/D6/D13 disposition of one authorized call: dispatch, park or refuse.

Pure and deterministic. The grant, resource scope and budget were already
checked; this applies the two owner rules, exposure, park-only, the approval
mode and the tool's approval policy. Order matters: a refusal can never be
turned into a park, and a park can never be turned into a dispatch.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from butlers.core.goal_admission import Grant

from .assertions import REPLAN_MOVE
from .contracts import ActuationRisk, Capability, Decision, Disposition, Principal, Recipient

REFUSE = Decision(Disposition.REFUSE)
DISPATCH = Decision(Disposition.DISPATCH)
PER_EVENT = Decision(Disposition.PARK, allow_standing_rule=False)
RULE_ELIGIBLE = Decision(Disposition.PARK, allow_standing_rule=True)
#: A rule-eligible park first offered to the calendar domain as a conditional move.
REPLAN = Decision(Disposition.PARK, allow_standing_rule=True, conditional=REPLAN_MOVE)

#: D13 replan exception (owner decision bu-qlx055 q12 = B). Dark until the
#: switch: off, every calendar move parks exactly as before.
REPLAN_MOVES_LIVE = False
#: The one capability the exception covers, and the arguments a move may carry.
#: ``request_id`` is absent so a declined move leaves no replayable receipt.
REPLAN_CAPABILITY = "calendar_update_event"
REPLAN_MOVE_ARGS = frozenset({"event_id", "start_at", "end_at", "timezone", "calendar_id"})


def listable(capability: Capability, depth: int) -> bool:
    """Owner rule 1 and D9: transact is never listed; administer never to a child."""
    return capability.effect_class != "transact" and not (
        depth == 1 and capability.effect_class == "administer"
    )


def decide(
    principal: Principal,
    capability: Capability,
    grant: Grant,
    recipient: Recipient,
    actuation_risk: ActuationRisk,
) -> Decision:
    kind = capability.effect_class
    if not listable(capability, principal.depth):
        return REFUSE
    if principal.exposed and kind == "record" and capability.owner_authority:
        return REFUSE
    # Per-event only: no standing rule, inherited approval or owner shortcut.
    if grant.park_only or capability.always_per_event or kind == "administer":
        return PER_EVENT
    if kind == "actuate" and actuation_risk != "safe":
        return PER_EVENT  # RFC 0028: unknown services are protected.
    if principal.exposed and kind == "communicate":
        if capability.name == "notify" and recipient == "owner":
            return DISPATCH  # The domain renders links inert for an exposed assertion.
        # The approvals module admits only owner-recipient or template-pinned
        # rules for an exposed lineage (REQ-capability-authorization-006).
        return RULE_ELIGIBLE
    if recipient == "non_owner" or capability.approval_gate:
        return RULE_ELIGIBLE
    return DISPATCH


def replan(principal: Principal, capability: Capability, args: Mapping[str, Any]) -> bool:
    """Whether a rule-eligible park of this call is first offered as a conditional move.

    Only the call is judged here: an unexposed goal moving one event in time and
    changing nothing else. Whether the event may move (Butlers-created, no
    attendees) is the calendar domain's check at execution, against the
    provider's copy; unmet, it declines and the call parks. The caller applies
    this only to a ``RULE_ELIGIBLE`` decision with no owner-law hold, so a
    park-only grant, ``always_per_event``, ``always_ask`` or exposure never
    reaches it.
    """
    if not REPLAN_MOVES_LIVE or principal.exposed:
        return False
    if capability.name != REPLAN_CAPABILITY:
        return False
    if not set(args) <= REPLAN_MOVE_ARGS:
        return False
    event_id = args.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        return False
    return args.get("start_at") is not None or args.get("end_at") is not None

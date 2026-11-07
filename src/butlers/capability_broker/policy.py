"""D4/D6/D13 disposition of one authorized call: dispatch, park or refuse.

Pure and deterministic. The grant, resource scope and budget were already
checked; this applies the two owner rules, exposure, park-only, the approval
mode and the tool's approval policy. Order matters: a refusal can never be
turned into a park, and a park can never be turned into a dispatch.
"""

from __future__ import annotations

from butlers.core.goal_admission import Grant

from .contracts import ActuationRisk, Capability, Decision, Disposition, Principal, Recipient

REFUSE = Decision(Disposition.REFUSE)
DISPATCH = Decision(Disposition.DISPATCH)
PER_EVENT = Decision(Disposition.PARK, allow_standing_rule=False)
RULE_ELIGIBLE = Decision(Disposition.PARK, allow_standing_rule=True)


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

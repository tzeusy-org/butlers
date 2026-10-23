"""The adopted D4/D7/D13 behavior matrix, independent of any LLM decision."""

from dataclasses import replace

from butlers.core.goal_admission import Grant

from .contracts import (
    BrokerRefused,
    Capability,
    Decision,
    Disposition,
    PolicyEvidence,
    Principal,
    Reason,
    RecipientBinding,
)


def bind_recipient_policy(
    capability: Capability, args: dict, digest: str, evidence: PolicyEvidence
) -> PolicyEvidence:
    """Do not accept an unbound owner enum, stale args or another tool's proof.

    Native selector parsing, unique live-owner reads and inherited-party
    resolution remain mandatory responsibilities of the production adapter.
    Descriptive catalog conditions are never evaluated as executable code.
    """
    r = capability.recipient_params
    carries_parties = bool(
        r.argument_names
        or r.undeterminable
        or r.default_owner_condition
        or r.undeterminable_condition
    )
    if not carries_parties:
        if evidence.recipient != "none" or evidence.recipient_binding is not None:
            raise BrokerRefused(Reason.POLICY)
        return evidence
    b = evidence.recipient_binding
    present = {n for n in r.argument_names if args.get(n) not in (None, "", [])}
    if (
        not isinstance(b, RecipientBinding)
        or b.capability != capability.identity
        or b.args_digest != digest
        or set(b.argument_names) != present
        or (present and not b.targets)
        or (not present and b.targets and not b.default_owner)
        or (b.default_owner and (present or not r.default_owner_condition))
    ):
        raise BrokerRefused(Reason.POLICY)
    # An unconditional unknown party is never resolved away. A conditional
    # native branch needs the trusted producer's complete exact-call evidence.
    native_branch = (
        capability.recipient_resolver == "core_notify"
        and capability.name == "notify"
        and capability.effect_class == "communicate"
        and bool(r.undeterminable_condition)
    )
    unresolved = r.undeterminable and not native_branch
    if unresolved or b.undeterminable or b.owner_id is None:
        recipient = "non_owner"
    elif b.targets:
        recipient = "owner" if all(t == b.owner_id for t in b.targets) else "non_owner"
    elif b.default_owner:
        recipient = "owner"
    else:
        recipient = "non_owner" if capability.effect_class == "communicate" else "none"
    return replace(evidence, recipient=recipient)


def grantable(capability: Capability, depth: int) -> bool:
    return (
        capability.presentable
        and capability.effect_class != "transact"
        and not (depth == 1 and capability.effect_class == "administer")
    )


def decide(
    principal: Principal, capability: Capability, grant: Grant, evidence: PolicyEvidence
) -> Decision:
    if not grantable(capability, principal.depth):
        return Decision(Disposition.REFUSE)
    if principal.exposed and capability.effect_class == "record" and capability.owner_authority:
        return Decision(Disposition.REFUSE)
    if principal.origin == "third_party" and capability.effect_class in {"actuate", "administer"}:
        return Decision(Disposition.REFUSE)
    # No rule, owner-directed shortcut or inherited approval can remove park-only.
    if grant.park_only or capability.always_per_event:
        return Decision(Disposition.PARK)
    permitted_rule = evidence.standing_rule and (
        not principal.exposed
        or (
            capability.effect_class == "communicate"
            and (evidence.recipient == "owner" or evidence.template_pinned)
        )
    )
    approval = capability.approval_required or evidence.recipient == "non_owner"
    if capability.effect_class == "actuate":
        approval |= evidence.actuation_risk != "safe"
    if principal.exposed:
        approval |= capability.effect_class == "administer"
        if capability.effect_class == "communicate":
            approval |= not (
                capability.name == "notify"
                and evidence.recipient == "owner"
                and evidence.inert_owner_notify
            )
    if approval:
        # Rule matching/approval still happens at park time in the owning domain.
        return Decision(Disposition.PARK, allow_standing_rule=permitted_rule)
    return Decision(Disposition.DISPATCH)

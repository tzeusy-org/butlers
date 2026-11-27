"""D4/D6/D13 disposition of one authorized call: dispatch, park or refuse.

Pure and deterministic. The grant, resource scope and budget were already
checked; this applies the two owner rules, exposure, park-only, the approval
mode and the tool's approval policy. Order matters: a refusal can never be
turned into a park, and a park can never be turned into a dispatch. The two
capability exceptions of D13 live here too: the calendar replan (a park first
offered as a conditional move) and the Gmail exposure rule (a dispatch that
becomes a per-event park, by the call's arguments).
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


#: Gmail exposure rule (D13; the owner's rule recorded on bu-kqme7f.5, built by bu-kqme7f.10).
#: An exposed or third-party goal may change only the Gmail thread that admitted it, never
#: removes INBOX or UNREAD without a per-event approval, and makes at most this many calls.
GMAIL_MODIFY = "gmail_modify_threads"
GMAIL_MODIFY_CAP = 5
GMAIL_PARKED_REMOVALS = frozenset({"INBOX", "UNREAD"})


def gmail_rule_applies(principal: Principal, capability: Capability) -> bool:
    """Whether the Gmail exposure rule binds this call: judged by exposure, not origin.

    Third-party origin is always bound (its goals start exposed). An unexposed owner,
    obligation or staffer goal keeps the ordinary ``record`` disposition.
    """
    return capability.name == GMAIL_MODIFY and (
        principal.exposed or principal.origin == "third_party"
    )


def gmail_parks(principal: Principal, args: Mapping[str, Any]) -> bool:
    """Whether a bound call parks for per-event approval rather than dispatching.

    It dispatches only when it names exactly the goal's admitting account and only threads of
    that one thread, and removes neither INBOX nor UNREAD. Anything malformed, any other
    account or thread, and a goal no Gmail message admitted all park. The domain validates the
    call itself; this only decides who must approve it.
    """
    trigger = principal.trigger_thread
    if trigger is None:
        return True
    account, thread_ids, remove = (
        args.get("account"),
        args.get("thread_ids"),
        args.get("remove_labels"),
    )
    if not isinstance(account, str) or account.strip().lower() != trigger[0]:
        return True
    if not isinstance(thread_ids, list) or not thread_ids:
        return True
    if any(thread_id != trigger[1] for thread_id in thread_ids):
        return True
    if remove is None:
        return False
    if not isinstance(remove, list):
        return True
    return any(not isinstance(label, str) or label in GMAIL_PARKED_REMOVALS for label in remove)


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
    # A Gmail draft reaches no recipient, but exposed/third-party proposals must remain
    # parked with lineage until the owner approves this exact mailbox write (D13).
    if capability.name == "gmail_save_draft" and (
        principal.exposed or principal.origin == "third_party"
    ):
        return PER_EVENT
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

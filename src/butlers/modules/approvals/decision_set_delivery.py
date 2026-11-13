"""Owner delivery of a decision set: its one subject, Telegram card tokens, and the decisions.

The set stays the authority; ``public.owner_delivery_subjects`` holds its one ``decision_set``
subject per ``(set id, version)``, bound to the set digest and the card's default selection
(:func:`~butlers.modules.approvals.decision_set_card.action_digest`). Dark behind
``OWNER_DECISIONS_LIVE``: the resolver refuses ``unavailable`` until the owner turns it on.

Two paths decide, both through :class:`DecisionSetResolver`, which rechecks every row and digest
under the goal lock:

* a Telegram ``od1`` token (:func:`redeem_set_token`): Approve decides the default selection and
  Reject rejects all, both recomputed here from the freshly read view; nothing about the
  selection is carried in the token or the callback;
* the dashboard (:func:`decide_selected`): the owner's per-item ticks, then the subject closes so
  a card still in Telegram is retracted and its tokens go stale.

The resolver commits on its own connections, so the subject CAS in :func:`redeem_set_token` is
serialised by the subject row lock alone (never the set's version row, which would deadlock
with the resolver). If the resolver refuses, the CAS rolls back and the token stays redeemable;
if the redeem transaction fails *after* the resolver committed, a retry is refused ``stale`` by
the resolver (the set is already decided) and the subject is closed by the next dashboard read.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

from butlers.capability_broker.owner_decisions import OwnerContext, SetDecision
from butlers.core import owner_delivery as od
from butlers.modules.approvals.decision_set_card import (
    action_digest,
    approve_selection,
    reject_selection,
)
from butlers.modules.approvals.decision_set_view import SetView

#: The card's buttons. ``ack`` is never offered for a set.
SET_CARD_ACTIONS: tuple[od.Action, ...] = ("approve", "reject")
_DASHBOARD_DECISIONS = {"approve": "approved", "reject": "rejected"}


class Resolver(Protocol):
    async def decide(
        self,
        set_id: UUID,
        version: int,
        selected: Mapping[UUID, str],
        explicit: Mapping[UUID, str],
        decision: str,
        owner: OwnerContext,
    ) -> SetDecision: ...


def set_source_ref(set_id: UUID, version: int) -> str:
    return f"{set_id}:{version}"


async def open_set_subject(conn: Any, view: SetView) -> od.Subject:
    """Open (or return) the subject of one set version; refuse a set no card can decide."""
    if approve_selection(view) is None:
        raise od.OwnerDeliveryRefused("set_not_decidable")
    owner = await od.owner_entity_id(conn)
    if owner is None:
        raise od.OwnerDeliveryRefused("owner_unavailable")
    return await od.open_subject(
        conn,
        kind="decision_set",
        source_ref=set_source_ref(view.set_id, view.version),
        owner_entity=owner,
        content_digest=view.digest,
        action_digest=action_digest(view),
        goal_effect_id=None,
    )


async def present_set_card(conn: Any, view: SetView, *, owner_recipient: str) -> od.Presentation:
    """The Telegram presentation of a decidable set, with Approve and Reject tokens.

    Call only for a view whose card offers a decision; an open-only card has no tokens and so
    needs no subject.
    """
    subject = await open_set_subject(conn, view)
    return await od.present(
        conn,
        subject.id,
        surface="telegram",
        route_ref=owner_recipient,
        actions=SET_CARD_ACTIONS,
        expires_at=view.expires_at,
    )


def _summary(decision: SetDecision) -> dict[str, Any]:
    return {
        "set_id": str(decision.set_id),
        "version": decision.version,
        "outcomes": [
            {"effect_id": str(o.effect_id), "outcome": o.outcome, "status": o.status}
            for o in decision.outcomes
        ],
    }


async def redeem_set_token(
    conn: Any,
    token: object,
    *,
    view: SetView,
    resolver: Resolver,
    expected_action: od.Action,
    owner_entity: UUID,
    owner: OwnerContext,
) -> od.Redemption:
    """Redeem one Telegram card token for a set against the freshly read ``view``.

    Run inside a transaction on the schema that holds the subject. The token's action digest must
    equal the digest of ``view`` now (same version, same exact items, same default selection), or
    it is refused ``action_changed`` before anything is decided.
    """

    async def decide(_conn: Any, action: str) -> dict[str, Any]:
        if action == "approve":
            picked = approve_selection(view)
            if picked is None:
                raise od.OwnerDeliveryRefused("action_changed")
            result = await resolver.decide(view.set_id, view.version, picked, {}, "approve", owner)
        else:
            rejected = reject_selection(view)
            if rejected is None:
                raise od.OwnerDeliveryRefused("action_changed")
            plain, explicit = rejected
            result = await resolver.decide(
                view.set_id, view.version, plain, explicit, "reject", owner
            )
        return _summary(result)

    return await od.redeem(
        conn,
        token,
        kind="decision_set",
        source_ref=set_source_ref(view.set_id, view.version),
        expected_action=expected_action,
        owner_entity=owner_entity,
        surface="telegram",
        current_action_digest=action_digest(view),
        decide=decide,
    )


class SelectionRefused(Exception):
    """A dashboard selection that is not exactly some of the shown items."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def split_selection(
    view: SetView, picks: Mapping[UUID, str]
) -> tuple[dict[UUID, str], dict[UUID, str]]:
    """Split the owner's ticks into the resolver's ``selected`` and ``explicit`` maps.

    Every tick must name an item of this exact version; an ``explicit_only`` item ticked by
    itself goes to ``explicit``. A tick on an unknown item, or none at all, is refused.
    """
    if not view.decidable:
        raise SelectionRefused("not_decidable")
    if not picks:
        raise SelectionRefused("empty")
    by_effect = {item.effect_id: item for item in view.items}
    selected: dict[UUID, str] = {}
    explicit: dict[UUID, str] = {}
    for effect_id, digest in picks.items():
        item = by_effect.get(effect_id)
        if item is None:
            raise SelectionRefused("unknown_item")
        (explicit if item.explicit_only else selected)[effect_id] = digest
    return selected, explicit


async def decide_selected(
    resolver: Resolver,
    view: SetView,
    decision: str,
    picks: Mapping[UUID, str],
    owner: OwnerContext,
) -> SetDecision:
    """Decide the owner's ticked items of the shown version (the dashboard path)."""
    if decision not in _DASHBOARD_DECISIONS:
        raise SelectionRefused("decision")
    selected, explicit = split_selection(view, picks)
    return await resolver.decide(view.set_id, view.version, selected, explicit, decision, owner)


async def close_set_subject(
    conn: Any, view: SetView, decision: str
) -> tuple[od.RetractTarget, ...]:
    """Resolve the card's subject after a dashboard decision, so its tokens go stale."""
    return await od.close_for_authority(
        conn,
        kind="decision_set",
        source_ref=set_source_ref(view.set_id, view.version),
        status=_DASHBOARD_DECISIONS[decision],
    )


__all__ = [
    "SET_CARD_ACTIONS",
    "Resolver",
    "SelectionRefused",
    "close_set_subject",
    "decide_selected",
    "open_set_subject",
    "present_set_card",
    "redeem_set_token",
    "set_source_ref",
    "split_selection",
]

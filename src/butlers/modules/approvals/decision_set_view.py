"""The owner-facing view of one decision set version (RFC 0038 D13/D19, M7; bu-jv1moa.27).

A decision set stores, per item, only the parked effect's capability, the digest of its stored
arguments and its recipient (``core.decision_sets``). This module reads each item's exact stored
call from the parked ``pending_actions`` row, recomputes that digest and rebuilds the call through
the S1/catalog preview, so the owner is shown the call that would run and nothing else.

Fail closed, never partial:

* An item whose row is missing, no longer pending, belongs to another goal, names another tool,
  whose stored arguments no longer match the item digest, or which has no complete exact preview
  is ``open_only``: it shows no facts and cannot be approved from a card.
* One ``open_only`` item, a state other than ``open`` or an elapsed set makes the whole set
  not ``decidable``: the owner reviews each call on its own approval page. There is no blanket
  approval to fall back to.

The view grants nothing. Deciding goes through ``DecisionSetResolver.decide``, which rechecks
every row and digest again under the goal lock.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from butlers.capability_broker.owner_decisions import UNTICKED_OUTCOME
from butlers.core.capability_json import InvalidArguments, arguments_digest
from butlers.core.decision_sets import DecisionSet, DecisionSetItem, DecisionSetLedger
from butlers.modules.approvals.lineage_facts import LineageFacts, lineage_facts
from butlers.modules.approvals.models import ActionStatus, PendingAction
from butlers.modules.approvals.previews import PreviewFact, dashboard_call_preview

#: Why an item cannot be approved from a card. Closed vocabulary; never free text.
OPEN_ONLY_REASONS = frozenset({"missing", "unavailable", "not_pending", "changed", "no_preview"})


@dataclass(frozen=True, slots=True)
class ItemView:
    position: int
    effect_id: UUID
    capability: str
    explicit_only: bool
    item_digest: str
    args_digest: str
    recipient_entity: UUID | None
    recipient_channel: str | None
    exact: bool
    reason: str | None
    title: str | None
    facts: tuple[PreviewFact, ...]
    lineage: LineageFacts | None
    #: The assistant's own words about the call (why, blast radius, reversibility): claims, never
    #: facts, shown under their own label. Empty for an ``open_only`` item.
    claims: tuple[PreviewFact, ...] = ()


@dataclass(frozen=True, slots=True)
class SetView:
    set_id: UUID
    version: int
    goal_id: UUID
    state: str
    expires_at: datetime
    digest: str
    items: tuple[ItemView, ...]
    #: Every item exact, the version open and not elapsed: a card may offer a decision.
    decidable: bool
    #: ``state``, ``expired`` or ``items`` when not decidable.
    reason: str | None
    #: What happens to an item the owner does not tick (the producer's ``UNTICKED_OUTCOME``).
    unticked_outcome: str


def _open_only(item: DecisionSetItem, reason: str) -> ItemView:
    return ItemView(
        position=item.position,
        effect_id=item.effect_id,
        capability=item.capability,
        explicit_only=item.explicit_only,
        item_digest=item.item_digest,
        args_digest=item.args_digest,
        recipient_entity=item.recipient_entity,
        recipient_channel=item.recipient_channel,
        exact=False,
        reason=reason,
        title=None,
        facts=(),
        lineage=None,
    )


def item_view(
    item: DecisionSetItem,
    goal_id: UUID,
    row: Mapping[str, Any] | None,
    *,
    unavailable: bool = False,
    ambiguous: bool = False,
) -> ItemView:
    """One item against its parked action row; any doubt is ``open_only``.

    ``ambiguous``: the effect id was found in more than one approvals pool, so no single row can
    be trusted as the parked call.
    """
    if ambiguous:
        return _open_only(item, "changed")
    if row is None:
        return _open_only(item, "unavailable" if unavailable else "missing")
    try:
        action = PendingAction.from_row(row)
    except Exception:  # noqa: BLE001 - an unreadable row is not a shown call
        return _open_only(item, "changed")
    if action.id != item.effect_id or action.status is not ActionStatus.PENDING:
        return _open_only(item, "not_pending")
    # The capability is ``<domain>__<tool>``; the tool must be exactly the parked action's, so a
    # nested name (``messenger__evil__notify``) never matches ``notify``.
    _, _, tool = item.capability.partition("__")
    if action.goal_id != goal_id or tool != action.tool_name:
        return _open_only(item, "changed")
    try:
        if arguments_digest(action.tool_args) != item.args_digest:
            return _open_only(item, "changed")
    except InvalidArguments:
        return _open_only(item, "changed")
    preview = dashboard_call_preview(action.tool_name, action.tool_args)
    if preview is None:
        return _open_only(item, "no_preview")
    return ItemView(
        position=item.position,
        effect_id=item.effect_id,
        capability=item.capability,
        explicit_only=item.explicit_only,
        item_digest=item.item_digest,
        args_digest=item.args_digest,
        recipient_entity=item.recipient_entity,
        recipient_channel=item.recipient_channel,
        exact=True,
        reason=None,
        title=preview["title"],
        facts=tuple(PreviewFact(f["label"], f["value"]) for f in preview["facts"]),
        lineage=lineage_facts(action),
        claims=tuple(
            PreviewFact(label, value)
            for label, value in (
                ("Why", action.why),
                ("Blast radius", action.blast_radius),
                ("Reversibility", action.reversibility),
            )
            if isinstance(value, str) and value.strip()
        ),
    )


def build_set_view(
    shown: DecisionSet,
    rows: Mapping[UUID, Mapping[str, Any]],
    *,
    now: datetime,
    unavailable: bool = False,
    unavailable_effects: Collection[UUID] = (),
    ambiguous: Collection[UUID] = (),
) -> SetView:
    """The whole version: per-item views plus whether a decision may be offered at all."""
    items = tuple(
        item_view(
            item,
            shown.goal_id,
            rows.get(item.effect_id),
            unavailable=unavailable or item.effect_id in unavailable_effects,
            ambiguous=item.effect_id in ambiguous,
        )
        for item in shown.items
    )
    if shown.state != "open":
        reason: str | None = "state"
    elif shown.expires_at <= now:
        reason = "expired"
    elif not items or not all(item.exact for item in items):
        reason = "items"
    else:
        reason = None
    return SetView(
        set_id=shown.set_id,
        version=shown.version,
        goal_id=shown.goal_id,
        state=shown.state,
        expires_at=shown.expires_at,
        digest=shown.digest,
        items=items,
        decidable=reason is None,
        reason=reason,
        unticked_outcome=UNTICKED_OUTCOME,
    )


async def load_set_view(
    set_id: UUID,
    version: int | None,
    *,
    ledger_pool: Any,
    action_pools: Sequence[tuple[str, Any]],
    now: datetime,
    on_pool_failure: Callable[[str], None] | None = None,
) -> SetView | None:
    """Read one version and every item's parked row, then build the view.

    ``action_pools`` are the owning approvals pools (the dashboard's approvals pool finder). A
    pool that cannot be read marks its items ``unavailable`` rather than failing the read, and is
    named through ``on_pool_failure`` for the degraded envelope. ``None``: no such set version.
    """
    async with ledger_pool.acquire() as connection:
        shown = await DecisionSetLedger(connection).read(set_id, version)
    if shown is None:
        return None
    wanted = [item.effect_id for item in shown.items]
    domain_of = {item.effect_id: item.capability.partition("__")[0] for item in shown.items}
    pools = dict(action_pools)
    seen: dict[UUID, list[tuple[str, Mapping[str, Any]]]] = {}
    failed: set[str] = set()
    for name, pool in action_pools:
        try:
            async with pool.acquire() as connection:
                found = await connection.fetch(
                    "SELECT * FROM pending_actions WHERE id = ANY($1::uuid[])", wanted
                )
        except Exception:  # noqa: BLE001 - one unreadable pool must not hide the others
            failed.add(name)
            if on_pool_failure is not None:
                on_pool_failure(name)
            continue
        for row in found:
            seen.setdefault(row["id"], []).append((name, row))
    # An item is read only from the pool of its capability's domain (the pool the broker parked it
    # in). A copy anywhere else is never a source; an id in more than one pool is ambiguous.
    rows: dict[UUID, Mapping[str, Any]] = {}
    ambiguous: set[UUID] = set()
    unavailable_effects: set[UUID] = set()
    for effect_id, domain in domain_of.items():
        if domain not in pools or domain in failed:
            unavailable_effects.add(effect_id)
            continue
        hits = seen.get(effect_id, [])
        if len(hits) > 1:
            ambiguous.add(effect_id)
        elif hits and hits[0][0] == domain:
            rows[effect_id] = hits[0][1]
    return build_set_view(
        shown,
        rows,
        now=now,
        unavailable_effects=unavailable_effects,
        ambiguous=ambiguous,
    )


__all__ = [
    "OPEN_ONLY_REASONS",
    "ItemView",
    "SetView",
    "build_set_view",
    "item_view",
    "load_set_view",
]

"""The one owner approve-and-dispatch path, and the decision-set resolver (RFC 0038 D11-D13, M7).

Every owner decision of parked goal effects, one item or a whole decision set, runs here in three
committed steps:

1. One Switchboard transaction, under the goal lock Stop takes, commits the owner decision
   receipt (sw_073) together with each decided effect's ledger transition: ``parked ->
   approved`` (``BrokerLedger.approve_parked``) or ``parked -> rejected``. This is the
   authoritative, digest-bound decision.
2. The decision is mirrored to the owning domain's action (``Domains.decide``), which fences its
   owner prompts. A domain action whose window already elapsed ends the approval instead
   (``approved -> expired``); nothing dispatches.
3. An approved item's stored call, exactly as the domain holds it and only if its digest still
   matches the receipt, dispatches through ``Broker.execute_approved``, which re-checks Stop, the
   grant, the catalog and owner law (D13).

A crash after step 1 is re-driven by ``OwnerDecisions.redrive``: an effect still ``approved``
under an approving receipt goes through steps 2 and 3 again, and a rejection whose domain action
is still pending is mirrored again. Both steps are idempotent, and ``begin_approved`` dispatches an
effect at most once (D12): a re-drive never re-dispatches.

``DecisionSetResolver`` decides one version of a set (bu-qlx055 q3/q11 = A: the owner's one tap
is each ticked item's per-event approval). The decision is exact or refused whole: the version
must still be the goal's open one, under the goal's current epoch and plan, every item must
still be parked with the call it was shown with, and the selection must name shown items by
their digests. An unticked item stays parked for its own later decision (Q3 = B,
``UNTICKED_OUTCOME``). A rule or schedule change (``explicit_only``) is decided only through the
explicit field, never inside an "all" selection (Q4).

The whole path is dark behind ``OWNER_DECISIONS_LIVE`` (False): no entry point records,
mirrors or dispatches anything until it flips, and the re-drive is a no-op.

Callers are trusted control-plane wiring that has already verified the owner (bu-jv1moa.27 owns
the od1 token kind, its redemption and the card); this module does no token work and is never
reachable from a session.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from butlers.core.capability_json import (
    InvalidArguments,
    arguments_digest,
    canonical_bytes,
    parse_json_object,
)
from butlers.core.decision_sets import DecisionSetItem, DecisionSetLedger

from .contracts import BrokerRefused, Reason

logger = logging.getLogger(__name__)

# Dark until the owner can reach it (bu-jv1moa.27) and the goal runtime cuts over: while False,
# no owner decision is recorded, mirrored or dispatched through this path, single item or set,
# and the re-drive is a no-op. Tests construct ``OwnerDecisions(..., live=True)``.
OWNER_DECISIONS_LIVE = False
# Q3 = B (pending owner confirmation): an item the owner did not tick stays parked under its
# own expiry. Switching to A (reject the unticked) is this one value.
UNTICKED_OUTCOME = "left"
DECISIONS = {"approve": "approved", "reject": "rejected"}
_ENDED = frozenset({"rejected", "expired", "cancelled"})
# Broker refusals that can clear on their own; an approval refused for one stays ``approved``
# for the re-drive. Any other refusal ends the approval (``cancelled``).
_TRANSIENT = frozenset({Reason.UNAVAILABLE, Reason.CAPABILITY_UNAVAILABLE})
_LIVE_GOAL = frozenset({"running", "waiting"})
_REDRIVE_BATCH = 50
# A rejection's domain mirror is retried while its action could still be pending
# (``goal_park._PARK_LIFETIME``); after that the action has expired on its own.
_REJECTION_MIRROR_WINDOW = "48 hours"


class OwnerDecisionRefused(Exception):
    """Why an owner decision decided nothing.

    ``resource`` (no such goal, effect or set), ``goal`` (the goal is not live), ``stale`` (the
    shown version, plan, epoch or item no longer holds, or it was already decided), ``expired``
    (the set's window elapsed), ``policy`` (the selection is not exactly the shown items) or
    ``unavailable`` (the path is dark, ``OWNER_DECISIONS_LIVE``).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class OwnerContext:
    """The verified owner context a decision is recorded under (never a token)."""

    decided_by: str
    via: str

    def __post_init__(self) -> None:
        for value in (self.decided_by, self.via):
            if type(value) is not str or not value:
                raise ValueError("owner_context")


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """What one decided item came to.

    ``outcome`` is the owner's decision of it (``approved``, ``rejected`` or ``left``);
    ``status`` is where its effect ended up (``committed``, ``failed``, ``unknown``, ``rejected``,
    ``expired``, ``cancelled``, ``parked`` for a left item, or ``approved`` when its dispatch was
    refused and waits for a re-drive). ``reason`` is the broker's refusal, if any.
    """

    effect_id: UUID
    outcome: str
    status: str
    reason: Reason | None = None


@dataclass(frozen=True, slots=True)
class SetDecision:
    receipt_id: UUID
    set_id: UUID
    version: int
    outcomes: tuple[ItemOutcome, ...]


@dataclass(frozen=True, slots=True)
class _Decided:
    """One effect a committed receipt decided, to be mirrored and (if approved) dispatched."""

    goal_id: UUID
    effect_id: UUID
    capability: str
    approval_ref: str
    args_digest: str
    outcome: str
    decided_by: str


def plan_outcomes(
    items: Sequence[DecisionSetItem],
    selected: Mapping[UUID, str],
    explicit: Mapping[UUID, str],
    decision: str,
) -> tuple[tuple[DecisionSetItem, str], ...]:
    """Each item of a version with what the owner's decision does to it, or refuse the whole.

    ``selected`` ticks plain items and ``explicit`` ticks ``explicit_only`` ones, each mapping an
    effect id to the item digest the owner was shown. Anything else refuses ``policy``; a digest
    that is not the item's refuses ``stale``.
    """
    if decision not in DECISIONS:
        raise OwnerDecisionRefused("policy")
    if not isinstance(selected, Mapping) or not isinstance(explicit, Mapping):
        raise OwnerDecisionRefused("policy")
    if not selected and not explicit:
        raise OwnerDecisionRefused("policy")
    if set(selected) & set(explicit):
        raise OwnerDecisionRefused("policy")
    by_effect = {item.effect_id: item for item in items}
    for picks, wants_explicit in ((selected, False), (explicit, True)):
        for effect_id, shown in picks.items():
            item = by_effect.get(effect_id)
            if item is None or item.explicit_only != wants_explicit or type(shown) is not str:
                raise OwnerDecisionRefused("policy")
    for picks in (selected, explicit):
        for effect_id, shown in picks.items():
            if by_effect[effect_id].item_digest != shown:
                raise OwnerDecisionRefused("stale")
    ticked = DECISIONS[decision]
    return tuple(
        (
            item,
            ticked
            if item.effect_id in selected or item.effect_id in explicit
            else UNTICKED_OUTCOME,
        )
        for item in items
    )


class OwnerDecisionLedger:
    """The receipt rows (sw_073), on the caller's transaction connection."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    async def record(
        self,
        goal_id: UUID,
        owner: OwnerContext,
        decision: str,
        items: Sequence[tuple[UUID, str, str, str]],
        set_id: UUID | None = None,
        set_version: int | None = None,
    ) -> UUID:
        """Insert one receipt; ``items`` are ``(effect_id, approval_ref, args_digest, outcome)``."""
        receipt = uuid4()
        await self.connection.execute(
            "INSERT INTO switchboard.owner_decisions (id, goal_id, set_id, set_version, "
            "decision, decided_by, via) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            receipt,
            goal_id,
            set_id,
            set_version,
            decision,
            owner.decided_by,
            owner.via,
        )
        await self.connection.executemany(
            "INSERT INTO switchboard.owner_decision_items (decision_id, goal_id, effect_id, "
            "approval_ref, args_digest, outcome) VALUES ($1, $2, $3, $4, $5, $6)",
            [(receipt, goal_id, *item) for item in items],
        )
        return receipt

    async def effects(self, goal_id: UUID, effect_ids: Sequence[UUID]) -> dict[UUID, Any]:
        """The named effects of the goal, locked, after the caller took the goal lock."""
        rows = await self.connection.fetch(
            "SELECT id, capability, status, approval_ref, args_digest FROM switchboard.effects "
            "WHERE goal_id = $1 AND id = ANY($2::uuid[]) ORDER BY id FOR UPDATE",
            goal_id,
            list(effect_ids),
        )
        return {row["id"]: row for row in rows}

    async def effect_status(self, goal_id: UUID, effect_id: UUID) -> str | None:
        return await self.connection.fetchval(
            "SELECT status FROM switchboard.effects WHERE goal_id = $1 AND id = $2",
            goal_id,
            effect_id,
        )

    async def undriven(self, outcome: str, limit: int) -> list[_Decided]:
        """Decided items whose effect still waits on steps 2-3 (approvals) or step 2 (rejections).

        An approval is undriven while its effect is ``approved`` (never reserved for dispatch).
        A rejection is a candidate while its domain action could still be pending.
        """
        if outcome == "approved":
            window = ""
        else:
            window = (
                f"AND d.decided_at > clock_timestamp() - interval '{_REJECTION_MIRROR_WINDOW}' "
            )
        rows = await self.connection.fetch(
            "SELECT i.goal_id, i.effect_id, e.capability, i.approval_ref, i.args_digest, "
            "d.decided_by FROM switchboard.owner_decision_items i "
            "JOIN switchboard.owner_decisions d ON d.id = i.decision_id "
            "JOIN switchboard.effects e ON (e.goal_id, e.id) = (i.goal_id, i.effect_id) "
            "WHERE i.outcome = $1 AND e.status = $1 AND e.approval_ref = i.approval_ref "
            f"{window}ORDER BY d.decided_at LIMIT $2",
            outcome,
            limit,
        )
        return [_Decided(outcome=outcome, **dict(row)) for row in rows]


def _domain(capability: str) -> str:
    return capability.split("__", 1)[0]


async def _current_bindings(connection: Any, goal_id: UUID) -> tuple[Any, Any]:
    """The goal's current lead epoch and plan version, read under the caller's goal lock."""
    row = await connection.fetchrow(
        "SELECT (SELECT max(epoch) FROM switchboard.goal_epochs WHERE goal_id = $1) AS epoch, "
        "(SELECT plan_version FROM switchboard.goals WHERE id = $1) AS plan_version",
        goal_id,
    )
    return row["epoch"], row["plan_version"]


class OwnerDecisions:
    """The shared approve-and-dispatch path; one instance per trusted caller."""

    def __init__(self, broker: Any, *, live: bool | None = None) -> None:
        self.broker = broker
        self.live = OWNER_DECISIONS_LIVE if live is None else live

    def require_live(self) -> None:
        """Refuse ``unavailable`` while the path is dark; nothing is recorded or dispatched."""
        if not self.live:
            raise OwnerDecisionRefused("unavailable")
        self.broker._require_available()

    async def approve(self, goal_id: UUID, effect_id: UUID, owner: OwnerContext) -> ItemOutcome:
        """The owner approved one parked effect: record, mirror, dispatch its stored call."""
        return await self._decide_one(goal_id, effect_id, owner, "approve")

    async def reject(self, goal_id: UUID, effect_id: UUID, owner: OwnerContext) -> ItemOutcome:
        """The owner rejected one parked effect: record it, end it, mirror it."""
        return await self._decide_one(goal_id, effect_id, owner, "reject")

    async def _decide_one(
        self, goal_id: UUID, effect_id: UUID, owner: OwnerContext, decision: str
    ) -> ItemOutcome:
        self.require_live()
        async with self.broker._transaction() as ledger:
            recorded = await self._record_one(ledger, goal_id, effect_id, owner, decision)
        if isinstance(recorded, OwnerDecisionRefused):
            raise recorded
        return await self.drive(recorded)

    async def _record_one(
        self, ledger: Any, goal_id: UUID, effect_id: UUID, owner: OwnerContext, decision: str
    ) -> _Decided | OwnerDecisionRefused:
        goal = await ledger._lock_goal(goal_id)
        if goal is None:
            return OwnerDecisionRefused("resource")
        if goal["status"] not in _LIVE_GOAL:
            return OwnerDecisionRefused("goal")
        receipts = OwnerDecisionLedger(ledger.connection)
        effect = (await receipts.effects(goal_id, [effect_id])).get(effect_id)
        if effect is None:
            return OwnerDecisionRefused("resource")
        if effect["status"] != "parked" or effect["approval_ref"] is None:
            return OwnerDecisionRefused("stale")
        outcome = DECISIONS[decision]
        await receipts.record(
            goal_id,
            owner,
            decision,
            [(effect_id, effect["approval_ref"], effect["args_digest"], outcome)],
        )
        await self._transition(ledger, goal_id, effect, outcome)
        return _Decided(
            goal_id,
            effect_id,
            effect["capability"],
            effect["approval_ref"],
            effect["args_digest"],
            outcome,
            owner.decided_by,
        )

    @staticmethod
    async def _transition(ledger: Any, goal_id: UUID, effect: Any, outcome: str) -> None:
        """Step 1's ledger half, in the receipt's transaction (the goal is live and locked)."""
        if outcome == "approved":
            refused = await ledger.approve_parked(
                goal_id, effect["id"], effect["approval_ref"], effect["args_digest"]
            )
            if refused is not None:  # unreachable under the caller's live-goal check
                raise refused
        else:
            await ledger.end_parked(goal_id, effect["id"], "rejected")

    async def drive(self, decided: _Decided) -> ItemOutcome:
        """Steps 2 and 3 for one effect a committed receipt decided. Idempotent."""
        self.require_live()
        domains = self.broker._live_domains
        mirrored = await domains.decide(
            _domain(decided.capability),
            decided.goal_id,
            decided.approval_ref,
            approve=decided.outcome == "approved",
            decided_by=decided.decided_by,
        )
        if decided.outcome != "approved":
            return ItemOutcome(decided.effect_id, decided.outcome, "rejected")
        status = getattr(mirrored, "status", None)
        if mirrored is None or status in _ENDED:
            # The domain action ended before the owner's decision reached it: never dispatch.
            ended = status if status in _ENDED else "cancelled"
            async with self.broker._transaction() as ledger:
                await ledger.void_approved(decided.goal_id, decided.effect_id, ended)
            return ItemOutcome(decided.effect_id, decided.outcome, ended)
        arguments = mirrored.arguments
        if status != "approved" or arguments is None:
            # The domain holds no runnable approved call for this effect: it never runs.
            return await self._void(decided, Reason.POLICY)
        try:
            stored = parse_json_object(canonical_bytes(arguments))
        except (InvalidArguments, TypeError):
            stored = None
        if stored is None or arguments_digest(stored) != decided.args_digest:
            # The domain holds a different call than the one the owner decided: it never runs.
            logger.error(
                "owner decision args mismatch goal=%s effect=%s",
                decided.goal_id,
                decided.effect_id,
            )
            return await self._void(decided, Reason.POLICY)
        try:
            result = await self.broker.execute_approved(
                decided.goal_id, decided.effect_id, decided.approval_ref, stored
            )
        except BrokerRefused as refused:
            if refused.reason not in _TRANSIENT:
                # A refusal that will not clear on its own (grant, catalog, label, owner law):
                # end the approval, so it is not re-driven every sweep and starves no other.
                return await self._void(decided, refused.reason)
            async with self.broker._transaction() as ledger:
                now = await OwnerDecisionLedger(ledger.connection).effect_status(
                    decided.goal_id, decided.effect_id
                )
            return ItemOutcome(decided.effect_id, decided.outcome, now or "", refused.reason)
        return ItemOutcome(decided.effect_id, decided.outcome, result.status)

    async def _void(self, decided: _Decided, reason: Reason) -> ItemOutcome:
        """``approved -> cancelled`` for an approval that will never dispatch.

        Only a never-dispatched approval changes (``void_approved``), so a concurrent driver
        that already dispatched it is left alone. The domain action is marked with a failed
        outcome (best effort), which keeps it out of any legacy dispatch recovery.
        """
        async with self.broker._transaction() as ledger:
            voided = await ledger.void_approved(decided.goal_id, decided.effect_id, "cancelled")
            now = await OwnerDecisionLedger(ledger.connection).effect_status(
                decided.goal_id, decided.effect_id
            )
        if voided:
            try:
                await self.broker._live_domains.record_outcome(
                    _domain(decided.capability),
                    decided.goal_id,
                    decided.approval_ref,
                    "cancelled",
                    None,
                )
            except Exception:
                logger.warning(
                    "owner decision void write-back failed goal=%s effect=%s",
                    decided.goal_id,
                    decided.effect_id,
                )
        return ItemOutcome(decided.effect_id, decided.outcome, now or "", reason)

    async def redrive(self) -> int:
        """Finish decisions a crash interrupted after their receipt; returns how many were driven.

        Approved effects never reserved for dispatch go through steps 2 and 3 again; rejections
        whose domain action is still pending are mirrored again. Neither can re-dispatch. Dark
        (``OWNER_DECISIONS_LIVE``), it does nothing: no receipt can exist.
        """
        if not self.live:
            return 0
        self.broker._require_available()
        async with self.broker._transaction() as ledger:
            receipts = OwnerDecisionLedger(ledger.connection)
            approvals = await receipts.undriven("approved", _REDRIVE_BATCH)
            rejections = await receipts.undriven("rejected", _REDRIVE_BATCH)
        driven = 0
        for decided in approvals:
            if await self._redrive_one(decided):
                driven += 1
        pending = await self._pending_mirrors(rejections)
        for decided in pending:
            if await self._redrive_one(decided):
                driven += 1
        return driven

    async def _redrive_one(self, decided: _Decided) -> bool:
        try:
            await self.drive(decided)
        except Exception:
            logger.exception(
                "owner decision re-drive failed goal=%s effect=%s",
                decided.goal_id,
                decided.effect_id,
            )
            return False
        return True

    async def _pending_mirrors(self, rejections: list[_Decided]) -> list[_Decided]:
        domains = self.broker._live_domains
        pending: list[_Decided] = []
        groups: dict[tuple[str, UUID], list[_Decided]] = {}
        for decided in rejections:
            groups.setdefault((_domain(decided.capability), decided.goal_id), []).append(decided)
        for (domain, goal_id), group in groups.items():
            try:
                states = await domains.action_states(
                    domain, goal_id, [decided.approval_ref for decided in group]
                )
            except Exception:
                logger.exception("owner decision mirror check failed goal=%s", goal_id)
                continue
            pending += [d for d in group if states.get(d.approval_ref) == "pending"]
        return pending


class DecisionSetResolver:
    """Decide one version of a decision set through the shared path."""

    def __init__(self, decisions: OwnerDecisions) -> None:
        self.decisions = decisions

    async def decide(
        self,
        set_id: UUID,
        version: int,
        selected: Mapping[UUID, str],
        explicit: Mapping[UUID, str],
        decision: str,
        owner: OwnerContext,
    ) -> SetDecision:
        """Record the owner's decision of the shown version, then drive each ticked item.

        Refuses the whole decision (nothing recorded) unless it is exact; see the module doc. An
        item whose mirror or dispatch fails is reported and left to the re-drive; it never stops
        the other items.
        """
        self.decisions.require_live()
        broker = self.decisions.broker
        async with broker._transaction() as ledger:
            shown = await DecisionSetLedger(ledger.connection).read(set_id, version)
        if shown is None:
            raise OwnerDecisionRefused("resource")
        planned = plan_outcomes(shown.items, selected, explicit, decision)
        async with broker._transaction() as ledger:
            recorded = await self._record(
                ledger, shown.goal_id, set_id, version, planned, decision, owner
            )
        if isinstance(recorded, OwnerDecisionRefused):
            raise recorded
        receipt, decided = recorded
        outcomes = []
        for item, outcome in planned:
            if outcome == UNTICKED_OUTCOME:
                outcomes.append(ItemOutcome(item.effect_id, outcome, "parked"))
                continue
            try:
                outcomes.append(await self.decisions.drive(decided[item.effect_id]))
            except Exception:
                logger.exception(
                    "decision set item drive failed set=%s effect=%s", set_id, item.effect_id
                )
                outcomes.append(ItemOutcome(item.effect_id, outcome, outcome, Reason.UNAVAILABLE))
        return SetDecision(receipt, set_id, version, tuple(outcomes))

    async def _record(
        self,
        ledger: Any,
        goal_id: UUID,
        set_id: UUID,
        version: int,
        planned: tuple[tuple[DecisionSetItem, str], ...],
        decision: str,
        owner: OwnerContext,
    ) -> tuple[UUID, dict[UUID, _Decided]] | OwnerDecisionRefused:
        connection = ledger.connection
        goal = await ledger._lock_goal(goal_id)
        if goal is None:
            return OwnerDecisionRefused("resource")
        if goal["status"] not in _LIVE_GOAL:
            return OwnerDecisionRefused("goal")
        state = await connection.fetchrow(
            "SELECT v.state, v.epoch, v.plan_version, clock_timestamp() >= v.expires_at "
            "AS elapsed FROM switchboard.decision_set_versions v "
            "WHERE v.set_id = $1 AND v.version = $2 FOR UPDATE",
            set_id,
            version,
        )
        if state is None or state["state"] != "open":
            return OwnerDecisionRefused("stale")
        if state["elapsed"]:
            await connection.execute(
                "UPDATE switchboard.decision_set_versions SET state = 'expired', "
                "updated_at = clock_timestamp() WHERE set_id = $1 AND version = $2",
                set_id,
                version,
            )
            return OwnerDecisionRefused("expired")
        if (state["epoch"], state["plan_version"]) != await _current_bindings(connection, goal_id):
            return OwnerDecisionRefused("stale")
        receipts = OwnerDecisionLedger(connection)
        effects = await receipts.effects(goal_id, [item.effect_id for item, _ in planned])
        for item, _ in planned:
            effect = effects.get(item.effect_id)
            if (
                effect is None
                or effect["status"] != "parked"
                or effect["approval_ref"] is None
                or effect["args_digest"] != item.args_digest
            ):
                return OwnerDecisionRefused("stale")
        receipt = await receipts.record(
            goal_id,
            owner,
            decision,
            [
                (
                    item.effect_id,
                    effects[item.effect_id]["approval_ref"],
                    item.args_digest,
                    outcome,
                )
                for item, outcome in planned
            ],
            set_id,
            version,
        )
        decided: dict[UUID, _Decided] = {}
        for item, outcome in planned:
            if outcome == UNTICKED_OUTCOME:
                continue
            effect = effects[item.effect_id]
            await OwnerDecisions._transition(ledger, goal_id, effect, outcome)
            decided[item.effect_id] = _Decided(
                goal_id,
                item.effect_id,
                effect["capability"],
                effect["approval_ref"],
                item.args_digest,
                outcome,
                owner.decided_by,
            )
        await connection.execute(
            "UPDATE switchboard.decision_set_versions SET state = 'decided', "
            "updated_at = clock_timestamp() WHERE set_id = $1 AND version = $2",
            set_id,
            version,
        )
        return receipt, decided

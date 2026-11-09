"""Broker SQL over the sw_047 goal ledger, one locked goal row per transaction.

Every method runs inside the caller's transaction on a connection holding the
``butler_switchboard_rw`` role. Each takes the goal row ``FOR UPDATE`` first,
the same order Stop uses (D14), so a call either commits before a Stop or is
refused after it, and two calls racing the last unit of budget serialise on
the goal. No method does network I/O.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from butlers.core.goal_admission import decode_grants, digest, encode_grants
from butlers.core.goal_labels import EXPOSURE_CAUSES, Label
from butlers.core.goal_ledger import GoalLedger
from butlers.core.owner_policy import (
    Policy,
    PolicyHold,
    PolicyUnavailable,
    hold_for,
    read_policy,
)

from .contracts import (
    BrokerRefused,
    Capability,
    Decision,
    Disposition,
    Principal,
    Reason,
    Recipient,
    Reservation,
    mint_token,
    token_hash,
)

# Effects a repeated call must not duplicate (D12): committed or unknown, plus
# anything still in flight that could become either.
_LIVE_EFFECTS = ("requested", "parked", "approved", "dispatched", "committed", "unknown")
# Effects that have not reached the domain and that a Stop cancels (D11, D14).
_CANCELLABLE = ("requested", "parked", "approved")
_ENDED_INVOCATION = ("returned", "failed", "timed_out", "stopped", "escalated")
# The requested name is session-supplied text. A refusal record keeps it only
# when it is a catalog identity (``<domain>__<tool>``, which the broker checks
# against the live catalog before writing). Anything else becomes this marker,
# so an immutable row never holds free text (D17).
UNPUBLISHED_CAPABILITY = "<unpublished>"
_CATALOG_IDENTITY = re.compile(r"[a-z][a-z0-9_]{0,62}__[a-z][a-z0-9_]{0,126}")


class BrokerLedger:
    def __init__(self, connection: Any):
        self.connection = connection

    async def _lock_goal(self, goal_id: UUID):
        try:
            await GoalLedger(self.connection).verify_role()
        except Exception:
            raise BrokerRefused(Reason.UNAVAILABLE) from None
        await self.connection.execute("SET LOCAL lock_timeout = '5s'")
        await self.connection.execute("SET LOCAL statement_timeout = '5s'")
        return await self.connection.fetchrow(
            "SELECT id, status, origin, catalog_digest, ceiling "
            "FROM switchboard.goals WHERE id = $1 FOR UPDATE",
            goal_id,
        )

    async def _lead_epoch_current(self, row) -> bool:
        if row["depth"] != 0:
            return True  # A child's token survives a lead replacement (D14).
        current = await self.connection.fetchval(
            "SELECT max(lead_epoch) FROM switchboard.invocations WHERE goal_id = $1 AND depth = 0",
            row["goal_id"],
        )
        return current == row["lead_epoch"]

    @staticmethod
    def _principal(goal, invocation) -> Principal:
        """The authenticated invocation; an unreadable label refuses (S1)."""
        try:
            snapshot = invocation["grant_snapshot"]
            grants = decode_grants(snapshot)
            if encode_grants(grants) != snapshot or digest(snapshot) != invocation["grant_digest"]:
                raise ValueError("grant_snapshot")
            label = Label.from_row(
                invocation["label_version"],
                invocation["label_instr"],
                invocation["label_data"],
                invocation["label_sens"],
            )
            return Principal(
                goal["id"],
                invocation["id"],
                invocation["depth"],
                goal["origin"],
                # S1 first switch: the invocation's own chain, never the goal
                # union. sw_057 keeps the Boolean equal to the label, so a
                # legacy row keeps its old restriction.
                label.exposed or invocation["exposed"] is not False,
                goal["catalog_digest"],
                grants,
                label,
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise BrokerRefused(Reason.AUTHENTICATION) from None

    async def _join(self, goal_id: UUID, invocation_id: UUID, label: Label, cause: str) -> bool:
        """Join ``label`` into the invocation (S1); the goal must already be locked.

        The union is taken against the row as it is when this statement runs,
        so concurrent joins never lose a principal or category. ``cause`` is
        recorded only if this join is the one that first exposes it.
        """
        if cause not in EXPOSURE_CAUSES:
            raise ValueError("exposure_cause")
        _, instr, data, sens = label.to_row()
        row = await self.connection.fetchrow(
            "UPDATE switchboard.invocations SET "
            "label_instr = switchboard.goal_label_union(label_instr, $3::text[]), "
            "label_data = switchboard.goal_label_union(label_data, $4::text[]), "
            "label_sens = switchboard.goal_label_union(label_sens, $5::text[]), "
            "exposure_cause = COALESCE(exposure_cause, CASE WHEN "
            "switchboard.goal_label_exposed($3::text[], $4::text[]) THEN $6 END), "
            "updated_at = now() WHERE goal_id = $1 AND id = $2 RETURNING id",
            goal_id,
            invocation_id,
            instr,
            data,
            sens,
            cause,
        )
        return row is not None

    # -- tokens (runner-private; never an MCP route) ---------------------------

    async def issue_token(self, goal_id: UUID, invocation_id: UUID) -> str:
        """Bind a fresh token to a launching invocation; plaintext returned once."""
        goal = await self._lock_goal(goal_id)
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        token, hashed = mint_token()
        row = await self.connection.fetchrow(
            "UPDATE switchboard.invocations SET token_hash = $3, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2 AND status = 'launching' AND token_hash IS NULL "
            "RETURNING id",
            goal_id,
            invocation_id,
            hashed,
        )
        if row is None:
            raise BrokerRefused(Reason.AUTHENTICATION)
        return token

    async def revoke_token(self, goal_id: UUID, invocation_id: UUID) -> None:
        """Clear the hash once the runner has recorded the invocation's end."""
        if await self._lock_goal(goal_id) is None:
            raise BrokerRefused(Reason.GOAL)
        await self.connection.execute(
            "UPDATE switchboard.invocations SET token_hash = NULL, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2 AND status = ANY($3::text[])",
            goal_id,
            invocation_id,
            list(_ENDED_INVOCATION),
        )

    # -- label joins at the runner's reads (runner-private; S1) ------------------

    async def consume_inbox(self, goal_id: UUID, invocation_id: UUID, inbox_id: UUID) -> bool:
        """Deliver one inbox row to a running invocation and join its label.

        The consumption and the join commit together (cause ``inbox``), so the
        reader is exposed before the reply can reach its model. False when the
        row is not this goal's or was already consumed.
        """
        goal = await self._lock_goal(goal_id)
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        row = await self.connection.fetchrow(
            "UPDATE switchboard.goal_inbox SET consumed_at = clock_timestamp() "
            "WHERE goal_id = $1 AND id = $2 AND consumed_at IS NULL "
            "RETURNING label_version, label_instr, label_data, label_sens",
            goal_id,
            inbox_id,
        )
        if row is None:
            return False
        label = Label.from_row(*row.values())
        if not await self._join(goal_id, invocation_id, label, "inbox"):
            raise BrokerRefused(Reason.AUTHENTICATION)
        return True

    async def read_child_result(self, goal_id: UUID, lead_id: UUID, child_id: UUID) -> Label:
        """A lead reading its child's result joins the child's label (cause ``child``)."""
        if await self._lock_goal(goal_id) is None:
            raise BrokerRefused(Reason.GOAL)
        # Children of a replaced lead report to its successor (D14), so any
        # child of the goal is readable by any of the goal's leads.
        child = await self.connection.fetchrow(
            "SELECT c.label_version, c.label_instr, c.label_data, c.label_sens "
            "FROM switchboard.invocations c JOIN switchboard.invocations l "
            "ON l.goal_id = c.goal_id AND l.id = $3 AND l.depth = 0 "
            "WHERE c.goal_id = $1 AND c.id = $2 AND c.depth = 1",
            goal_id,
            child_id,
            lead_id,
        )
        if child is None:
            raise BrokerRefused(Reason.AUTHENTICATION)
        label = Label.from_row(*child.values())
        if not await self._join(goal_id, lead_id, label, "child"):
            raise BrokerRefused(Reason.AUTHENTICATION)
        return label

    # -- refusal records (sw_056) -------------------------------------------------

    async def record_refusal(
        self,
        principal: Principal | None,
        capability: str,
        reason: Reason,
        effect_id: UUID | None = None,
        hold: PolicyHold | None = None,
    ) -> None:
        """One content-blind D7 refusal record, lineage only when authenticated.

        An owner-law refusal also records its holding terms (sw_059), linked
        to the refusal record, in the same transaction.
        """
        recorded = capability if _CATALOG_IDENTITY.fullmatch(capability) else UNPUBLISHED_CAPABILITY
        refusal_id = await self.connection.fetchval(
            "INSERT INTO switchboard.broker_refusals "
            "(goal_id, invocation_id, capability, reason, effect_id) VALUES ($1, $2, $3, $4, $5) "
            "RETURNING id",
            principal.goal_id if principal else None,
            principal.invocation_id if principal else None,
            recorded,
            reason.value,
            effect_id,
        )
        if hold is not None and principal is not None and recorded != UNPUBLISHED_CAPABILITY:
            await self.record_hold(
                principal, recorded, "refused", hold, effect_id=effect_id, refusal_id=refusal_id
            )

    # -- owner law (sw_059; S1, REQ-capability-authorization-020) ----------------

    async def owner_law(
        self,
        capability: Capability,
        args: dict[str, Any],
        *,
        resolved: frozenset[str] = frozenset(),
        canonical: Callable[[Policy], Awaitable[Policy]] | None = None,
    ) -> PolicyHold | None:
        """The owner's active terms on this call, read under this transaction.

        The generation row is held ``FOR SHARE`` until the call commits, so a
        concurrent owner change lands before this call or after its effect
        fence, never between. Unreadable policy refuses (fail closed).
        ``resolved`` names the alias-resolved params whose values in ``args``
        are canonical; ``canonical`` puts the terms' own ids in the same form
        (``aliases``). Only those, and the alias-free params, can release a term.
        """
        try:
            policy = await read_policy(self.connection)
        except PolicyUnavailable:
            raise BrokerRefused(Reason.UNAVAILABLE) from None
        if canonical is not None:
            policy = await canonical(policy)
        return hold_for(
            policy,
            capability.identity,
            capability.domain,
            capability.effect_class,
            args,
            capability.alias_free_params | (resolved & capability.alias_resolved),
        )

    async def record_hold(
        self,
        principal: Principal,
        capability: str,
        disposition: str,
        hold: PolicyHold,
        *,
        effect_id: UUID | None = None,
        refusal_id: UUID | None = None,
    ) -> None:
        await self.connection.execute(
            "INSERT INTO switchboard.owner_policy_holds (id, goal_id, invocation_id, capability, "
            "disposition, term, effect_id, refusal_id, generation, terms) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)",
            uuid4(),
            principal.goal_id,
            principal.invocation_id,
            capability,
            disposition,
            hold.constraint,
            effect_id,
            refusal_id,
            hold.generation,
            hold.refs,
        )

    # -- per-call transaction ---------------------------------------------------

    async def authenticate(self, goal_id: UUID, token: str) -> Principal:
        """D7 step 1: the token resolves to one running invocation of a running goal."""
        hashed = token_hash(token)
        goal = await self._lock_goal(goal_id)
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        rows = await self.connection.fetch(
            "SELECT *, lease_expires_at IS NULL OR lease_expires_at > clock_timestamp() "
            "AS lease_valid FROM switchboard.invocations WHERE goal_id = $1 AND token_hash = $2",
            goal_id,
            hashed,
        )
        if len(rows) != 1 or rows[0]["status"] != "running" or rows[0]["lease_valid"] is not True:
            raise BrokerRefused(Reason.AUTHENTICATION)
        if not await self._lead_epoch_current(rows[0]):
            raise BrokerRefused(Reason.EPOCH)
        return self._principal(goal, rows[0])

    async def reserve(
        self,
        principal: Principal,
        capability: Capability,
        decision: Decision,
        args_digest: str,
        recipient: Recipient,
    ) -> Reservation | BrokerRefused:
        """D7 steps 2 and 4: budget, duplicate digest, effect row, budget charge.

        Budget exhaustion is *returned*, not raised, so the caller commits the
        goal's ``budget_exhausted`` transition before refusing.
        """
        exhausted = await self._budget_exhausted(principal)
        if exhausted is not None:
            return exhausted
        effect_id = None
        if capability.effect_class != "read":
            previous = await self.connection.fetchval(
                "SELECT id FROM switchboard.effects WHERE goal_id = $1 AND capability = $2 "
                "AND args_digest = $3 AND status = ANY($4::text[]) ORDER BY created_at, id LIMIT 1",
                principal.goal_id,
                capability.identity,
                args_digest,
                list(_LIVE_EFFECTS),
            )
            if previous is not None:
                raise BrokerRefused(Reason.DUPLICATE, effect_id=previous)
            effect_id = uuid4()
            # A dispatched call commits its intent before any byte leaves the
            # broker; a parked one stays requested until the domain parks it.
            status = "dispatched" if decision.disposition is Disposition.DISPATCH else "requested"
            await self.connection.execute(
                "INSERT INTO switchboard.effects "
                "(id, goal_id, invocation_id, capability, args_digest, status) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                effect_id,
                principal.goal_id,
                principal.invocation_id,
                capability.identity,
                args_digest,
                status,
            )
        await self._charge(principal)
        return Reservation(
            principal, capability, decision, uuid4(), effect_id, args_digest, recipient
        )

    async def _budget_exhausted(self, principal: Principal) -> BrokerRefused | None:
        """D10, fail closed: the goal's calls and deadline, then the invocation's share.

        Goal exhaustion ends the goal ``budget_exhausted``. A child whose own D9
        share (calls or wall time) is spent is refused, and the goal runs on.
        """
        budget = await self.connection.fetchrow(
            "SELECT max_calls, used_calls, reserved_calls, deadline > clock_timestamp() AS live "
            "FROM switchboard.budgets WHERE goal_id = $1 FOR UPDATE",
            principal.goal_id,
        )
        if budget is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        if (
            budget["live"] is not True
            or budget["used_calls"] + budget["reserved_calls"] >= budget["max_calls"]
        ):
            await self.connection.execute(
                "UPDATE switchboard.goals SET status = 'budget_exhausted', updated_at = now() "
                "WHERE id = $1 AND status = 'running'",
                principal.goal_id,
            )
            return BrokerRefused(Reason.BUDGET)
        share = await self.connection.fetchrow(
            "SELECT max_calls IS NULL OR used_calls < max_calls AS calls_left, "
            "deadline IS NULL OR deadline > clock_timestamp() AS time_left "
            "FROM switchboard.invocations WHERE goal_id = $1 AND id = $2",
            principal.goal_id,
            principal.invocation_id,
        )
        if share is None or share["calls_left"] is not True or share["time_left"] is not True:
            return BrokerRefused(Reason.BUDGET)
        return None

    async def _charge(self, principal: Principal) -> None:
        """One call against the goal budget and the calling invocation's count (D14)."""
        await self.connection.execute(
            "UPDATE switchboard.budgets SET used_calls = used_calls + 1, updated_at = now() "
            "WHERE goal_id = $1",
            principal.goal_id,
        )
        await self.connection.execute(
            "UPDATE switchboard.invocations SET used_calls = used_calls + 1, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2",
            principal.goal_id,
            principal.invocation_id,
        )

    async def charge_call(self, principal: Principal) -> BrokerRefused | None:
        """A goal tool call (D8) draws one call from the D10 budget, like any call."""
        exhausted = await self._budget_exhausted(principal)
        if exhausted is None:
            await self._charge(principal)
        return exhausted

    # -- settlement -------------------------------------------------------------

    async def settle(
        self,
        reservation: Reservation,
        status: str,
        *,
        receipt_ref: str | None = None,
        output: Label | None = None,
        writers: tuple[tuple[Any, Any], ...] = (),
    ) -> None:
        """Record a domain receipt (D11) and join the label its output carries (D6, S1).

        A stamped read's tainted records name their writer invocation
        (``taint_goal_id``, ``taint_invocation_id``); each writer's persisted
        label is joined with ``output``. The join commits with the receipt,
        before the result can reach the model; sw_057 raises the goal's
        reporting union as well.
        """
        principal = reservation.principal
        await self._lock_goal(principal.goal_id)
        if output is not None:
            for pointer in writers:
                output = output.join(await self.writer_label(pointer))
        if output is not None and not await self._join(
            principal.goal_id, principal.invocation_id, output, "read"
        ):
            raise BrokerRefused(Reason.UNAVAILABLE)
        if reservation.effect_id is None:
            return
        row = await self.connection.fetchrow(
            "UPDATE switchboard.effects SET status = $3, receipt_ref = $4, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2 AND status IN ('requested', 'dispatched') "
            "RETURNING id",
            principal.goal_id,
            reservation.effect_id,
            status,
            receipt_ref,
        )
        if row is None:
            raise BrokerRefused(Reason.UNAVAILABLE)

    async def writer_label(self, pointer: Any) -> Label:
        """The persisted label of a stored record's writer invocation (D6, S1).

        Resolved through the record's ``(taint_goal_id, taint_invocation_id)``
        pointer. A missing, malformed, dangling or unreadable pointer is the
        unknown label (``{world}``, all six categories): it never reads clean.
        """
        try:
            goal_id, invocation_id = pointer
            if not isinstance(goal_id, UUID) or not isinstance(invocation_id, UUID):
                return Label.unknown()
            row = await self.connection.fetchrow(
                "SELECT label_version, label_instr, label_data, label_sens "
                "FROM switchboard.invocations WHERE goal_id = $1 AND id = $2",
                goal_id,
                invocation_id,
            )
            return Label.unknown() if row is None else Label.from_row(*row.values())
        except (TypeError, ValueError):
            return Label.unknown()

    async def mark_parked(self, reservation: Reservation, approval_ref: str) -> bool:
        """requested -> parked. False when a Stop cancelled it while in flight."""
        await self._lock_goal(reservation.principal.goal_id)
        row = await self.connection.fetchrow(
            "UPDATE switchboard.effects SET status = 'parked', approval_ref = $3, "
            "updated_at = now() WHERE goal_id = $1 AND id = $2 AND status = 'requested' "
            "RETURNING id",
            reservation.principal.goal_id,
            reservation.effect_id,
            approval_ref,
        )
        return row is not None

    async def effect_capability(self, goal_id: UUID, effect_id: UUID) -> str | None:
        return await self.connection.fetchval(
            "SELECT capability FROM switchboard.effects WHERE goal_id = $1 AND id = $2",
            goal_id,
            effect_id,
        )

    async def begin_approved(
        self,
        goal_id: UUID,
        effect_id: UUID,
        approval_ref: str,
        args_digest: str,
        catalog_capability: Capability | None,
        catalog_digest: str,
    ) -> Reservation | BrokerRefused:
        """D13 approved execution: re-check Stop and grant, then commit dispatch.

        A stopped goal's effect is cancelled and the refusal *returned*, so the
        caller commits the cancellation before refusing.

        The stored call keeps its effect row. An owner edit changes the digest,
        which the effect row cannot (sw_047 makes it immutable), so the
        original is recorded ``rejected`` and the edited call gets its own
        effect row under the same approval reference.
        """
        goal = await self._lock_goal(goal_id)
        if goal is None:
            raise BrokerRefused(Reason.GOAL)
        effect = await self.connection.fetchrow(
            "SELECT e.*, i.depth, i.exposed AS invocation_exposed, i.grant_snapshot, "
            "i.grant_digest, i.label_version AS invocation_label_version, "
            "i.label_instr AS invocation_label_instr, i.label_data AS invocation_label_data, "
            "i.label_sens AS invocation_label_sens "
            "FROM switchboard.effects e JOIN switchboard.invocations i "
            "ON (i.goal_id, i.id) = (e.goal_id, e.invocation_id) "
            "WHERE e.goal_id = $1 AND e.id = $2 FOR UPDATE OF e",
            goal_id,
            effect_id,
        )
        if effect is None or effect["status"] not in ("parked", "approved"):
            raise BrokerRefused(Reason.POLICY)
        if effect["approval_ref"] != approval_ref:
            raise BrokerRefused(Reason.POLICY)
        if goal["status"] not in ("running", "awaiting_approval", "settling"):
            # A stopped (or otherwise ended) goal never executes an approval.
            await self.connection.execute(
                "UPDATE switchboard.effects SET status = 'cancelled', updated_at = now() "
                "WHERE goal_id = $1 AND id = $2",
                goal_id,
                effect_id,
            )
            return BrokerRefused(Reason.GOAL)
        # The approved call runs under its requesting invocation's current label,
        # which covers the effect's own snapshot: approval never narrows it.
        invocation = {
            "id": effect["invocation_id"],
            "depth": effect["depth"],
            "exposed": effect["invocation_exposed"],
            "grant_snapshot": effect["grant_snapshot"],
            "grant_digest": effect["grant_digest"],
            "label_version": effect["invocation_label_version"],
            "label_instr": effect["invocation_label_instr"],
            "label_data": effect["invocation_label_data"],
            "label_sens": effect["invocation_label_sens"],
        }
        principal = self._principal(goal, invocation)
        effect_label = Label.from_row(
            effect["label_version"],
            effect["label_instr"],
            effect["label_data"],
            effect["label_sens"],
        )
        if not principal.label.covers(effect_label):
            raise BrokerRefused(Reason.POLICY)
        if (
            catalog_capability is None
            or catalog_capability.identity != effect["capability"]
            or principal.grant_for(effect["capability"]) is None
        ):
            raise BrokerRefused(Reason.CAPABILITY)
        if catalog_digest != principal.catalog_digest:
            raise BrokerRefused(Reason.CATALOG)
        dispatch_id = effect_id
        if args_digest == effect["args_digest"]:
            if effect["status"] != "approved":
                # D11: the owner's approval is committed (approve_parked) first.
                raise BrokerRefused(Reason.POLICY)
            await self.connection.execute(
                "UPDATE switchboard.effects SET status = 'dispatched', updated_at = now() "
                "WHERE goal_id = $1 AND id = $2",
                goal_id,
                effect_id,
            )
        else:
            dispatch_id = uuid4()
            await self.connection.execute(
                "UPDATE switchboard.effects SET status = 'rejected', updated_at = now() "
                "WHERE goal_id = $1 AND id = $2",
                goal_id,
                effect_id,
            )
            await self.connection.execute(
                "INSERT INTO switchboard.effects (id, goal_id, invocation_id, capability, "
                "args_digest, status, approval_ref) VALUES ($1, $2, $3, $4, $5, 'dispatched', $6)",
                dispatch_id,
                goal_id,
                effect["invocation_id"],
                effect["capability"],
                args_digest,
                approval_ref,
            )
        decision = Decision(Disposition.DISPATCH)
        return Reservation(
            principal, catalog_capability, decision, uuid4(), dispatch_id, args_digest
        )

    async def approve_parked(
        self, goal_id: UUID, effect_id: UUID, approval_ref: str, args_digest: str
    ) -> BrokerRefused | None:
        """D11 ``parked -> approved``: the owner approved the stored call.

        Committed on its own before dispatch begins, so the decision survives a
        failed or interrupted dispatch. An edit (a different digest) never
        approves the stored call; ``begin_approved`` records it ``rejected``.
        A stopped goal's effect is cancelled and the refusal *returned*.
        """
        goal = await self._lock_goal(goal_id)
        if goal is None:
            raise BrokerRefused(Reason.GOAL)
        effect = await self.connection.fetchrow(
            "SELECT status, approval_ref, args_digest FROM switchboard.effects "
            "WHERE goal_id = $1 AND id = $2 FOR UPDATE",
            goal_id,
            effect_id,
        )
        if (
            effect is None
            or effect["status"] not in ("parked", "approved")
            or effect["approval_ref"] != approval_ref
        ):
            raise BrokerRefused(Reason.POLICY)
        if goal["status"] not in ("running", "awaiting_approval", "settling"):
            await self.connection.execute(
                "UPDATE switchboard.effects SET status = 'cancelled', updated_at = now() "
                "WHERE goal_id = $1 AND id = $2",
                goal_id,
                effect_id,
            )
            return BrokerRefused(Reason.GOAL)
        if effect["status"] == "parked" and effect["args_digest"] == args_digest:
            await self.connection.execute(
                "UPDATE switchboard.effects SET status = 'approved', updated_at = now() "
                "WHERE goal_id = $1 AND id = $2",
                goal_id,
                effect_id,
            )
        return None

    async def parked_refs(self, goal_id: UUID) -> list[tuple[UUID, str, str]]:
        """``(effect_id, capability, approval_ref)`` of every parked effect."""
        if await self._lock_goal(goal_id) is None:
            raise BrokerRefused(Reason.GOAL)
        rows = await self.connection.fetch(
            "SELECT id, capability, approval_ref FROM switchboard.effects "
            "WHERE goal_id = $1 AND status = 'parked' AND approval_ref IS NOT NULL",
            goal_id,
        )
        return [(r["id"], r["capability"], r["approval_ref"]) for r in rows]

    async def end_parked(self, goal_id: UUID, effect_id: UUID, status: str) -> bool:
        """D11 ``parked -> rejected | expired | cancelled`` from the domain's decision."""
        if status not in ("rejected", "expired", "cancelled"):
            raise ValueError("parked_end_status")
        await self._lock_goal(goal_id)
        row = await self.connection.fetchrow(
            "UPDATE switchboard.effects SET status = $3, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2 AND status = 'parked' RETURNING id",
            goal_id,
            effect_id,
            status,
        )
        return row is not None

    async def cancel_parked(self, goal_id: UUID) -> list[tuple[str, str]]:
        """D14: a stopped goal cancels every effect that has not reached a domain.

        Returns ``(capability, approval_ref)`` pairs whose owning approvals
        must be cancelled too. Dispatched effects are left to settle.
        """
        goal = await self._lock_goal(goal_id)
        if goal is None or goal["status"] != "stopped":
            raise BrokerRefused(Reason.GOAL)
        rows = await self.connection.fetch(
            "UPDATE switchboard.effects SET status = 'cancelled', updated_at = now() "
            "WHERE goal_id = $1 AND status = ANY($2::text[]) RETURNING capability, approval_ref",
            goal_id,
            list(_CANCELLABLE),
        )
        return [(r["capability"], r["approval_ref"]) for r in rows if r["approval_ref"]]

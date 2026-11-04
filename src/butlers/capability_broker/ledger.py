"""Broker SQL over the sw_047 goal ledger, one locked goal row per transaction.

Every method runs inside the caller's transaction on a connection holding the
``butler_switchboard_rw`` role. Each takes the goal row ``FOR UPDATE`` first,
the same order Stop uses (D14), so a call either commits before a Stop or is
refused after it, and two calls racing the last unit of budget serialise on
the goal. No method does network I/O.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from butlers.core.goal_admission import decode_grants, digest, encode_grants
from butlers.core.goal_ledger import GoalLedger

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
            "SELECT id, status, origin, exposed, catalog_digest, ceiling "
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
        try:
            snapshot = invocation["grant_snapshot"]
            grants = decode_grants(snapshot)
            if encode_grants(grants) != snapshot or digest(snapshot) != invocation["grant_digest"]:
                raise ValueError("grant_snapshot")
            return Principal(
                goal["id"],
                invocation["id"],
                invocation["depth"],
                goal["origin"],
                bool(goal["exposed"] or invocation["exposed"]),
                goal["catalog_digest"],
                grants,
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise BrokerRefused(Reason.AUTHENTICATION) from None

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
        budget = await self.connection.fetchrow(
            "SELECT max_calls, used_calls, reserved_calls, deadline > clock_timestamp() AS live "
            "FROM switchboard.budgets WHERE goal_id = $1 FOR UPDATE",
            principal.goal_id,
        )
        if budget is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        if (
            not budget["live"]
            or budget["used_calls"] + budget["reserved_calls"] >= budget["max_calls"]
        ):
            await self.connection.execute(
                "UPDATE switchboard.goals SET status = 'budget_exhausted', updated_at = now() "
                "WHERE id = $1 AND status = 'running'",
                principal.goal_id,
            )
            return BrokerRefused(Reason.BUDGET)
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
        await self.connection.execute(
            "UPDATE switchboard.budgets SET used_calls = used_calls + 1, updated_at = now() "
            "WHERE goal_id = $1",
            principal.goal_id,
        )
        return Reservation(
            principal, capability, decision, uuid4(), effect_id, args_digest, recipient
        )

    # -- settlement -------------------------------------------------------------

    async def settle(
        self,
        reservation: Reservation,
        status: str,
        *,
        receipt_ref: str | None = None,
        expose: bool = False,
    ) -> None:
        """Record a domain receipt (D11) and any exposure the output caused (D6)."""
        principal = reservation.principal
        await self._lock_goal(principal.goal_id)
        if expose:
            # Monotonic; the sw_047 trigger marks the goal exposed too.
            await self.connection.execute(
                "UPDATE switchboard.invocations SET exposed = true, updated_at = now() "
                "WHERE goal_id = $1 AND id = $2 AND NOT exposed",
                principal.goal_id,
                principal.invocation_id,
            )
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
            "i.grant_digest FROM switchboard.effects e JOIN switchboard.invocations i "
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
        invocation = {
            "id": effect["invocation_id"],
            "depth": effect["depth"],
            "exposed": effect["invocation_exposed"],
            "grant_snapshot": effect["grant_snapshot"],
            "grant_digest": effect["grant_digest"],
        }
        principal = self._principal(goal, invocation)
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

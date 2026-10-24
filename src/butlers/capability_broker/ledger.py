"""Qualified existing sw047 SQL, with one goal lock ordering calls against Stop.

No DDL, domain approval writes, second pool acquisition, or network request is
performed here. The owning service encloses each operation in a transaction.
Real PostgreSQL concurrency/RLS evidence remains a separate required lane.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg

from butlers.core.goal_admission import decode_grants, digest, encode_grants, intersect
from butlers.core.goal_ledger import GoalLedger

from .contracts import (
    BrokerRefused,
    Capability,
    Decision,
    Disposition,
    Principal,
    Reason,
    Reservation,
    mint_token,
    token_hash,
)
from .domains import Authorities
from .policy import bind_recipient_policy, decide, grantable


class BrokerLedger:
    def __init__(self, connection: asyncpg.Connection):
        self.connection = connection

    async def issue_token(self, goal_id: UUID, invocation_id: UUID) -> str:
        """Issue once for an existing runner-owned launching invocation.

        This never creates an invocation or accepts a grant/epoch from a caller.
        The service returns plaintext only after this transaction commits. A
        lost commit cannot be recovered by issuing a second bearer credential.
        """
        await GoalLedger(self.connection).verify_role()
        await self.connection.execute("SET LOCAL lock_timeout='5s'")
        await self.connection.execute("SET LOCAL statement_timeout='5s'")
        goal = await self.connection.fetchrow(
            "SELECT id,status,ceiling FROM switchboard.goals WHERE id=$1 FOR UPDATE", goal_id
        )
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        row = await self.connection.fetchrow(
            "SELECT *,lease_expires_at IS NULL OR lease_expires_at>clock_timestamp() "
            "AS lease_valid FROM switchboard.invocations WHERE goal_id=$1 AND id=$2 FOR UPDATE",
            goal_id,
            invocation_id,
        )
        if (
            row is None
            or row["status"] != "launching"
            or row["token_hash"] is not None
            or row["lease_valid"] is not True
            or type(row["depth"]) is not int
            or row["depth"] not in (0, 1)
        ):
            raise BrokerRefused(Reason.AUTHENTICATION)
        if row["depth"] == 0:
            epoch = await self.connection.fetchval(
                "SELECT max(lead_epoch) FROM switchboard.invocations WHERE goal_id=$1 AND depth=0",
                goal_id,
            )
            if epoch != row["lead_epoch"]:
                raise BrokerRefused(Reason.EPOCH)
        try:
            snapshot = row["grant_snapshot"]
            grants = decode_grants(snapshot)
            ceiling = decode_grants(goal["ceiling"])
            if (
                encode_grants(grants) != snapshot
                or digest(snapshot) != row["grant_digest"]
                or intersect(grants, ceiling) != grants
            ):
                raise ValueError("invalid_grant_snapshot")
        except (ValueError, KeyError, TypeError, AttributeError):
            raise BrokerRefused(Reason.AUTHENTICATION) from None
        token, hashed = mint_token()
        updated = await self.connection.fetchrow(
            "UPDATE switchboard.invocations SET token_hash=$3,updated_at=now() "
            "WHERE goal_id=$1 AND id=$2 AND status='launching' AND token_hash IS NULL RETURNING id",
            goal_id,
            invocation_id,
            hashed,
        )
        if updated is None:
            raise BrokerRefused(Reason.AUTHENTICATION)
        return token

    async def revoke_token(self, goal_id: UUID, invocation_id: UUID) -> None:
        """Clear credentials after the runner commits a terminal invocation.

        Stop uses this same goal-first order and clears all invocation tokens.
        Requiring the terminal row prevents a still-launching invocation from
        obtaining a replacement token after revocation. Ending the process and
        recording its outcome/budget accounting remain the runner's job.
        A missing goal/invocation is not silently reported as a completed revoke.
        """
        await GoalLedger(self.connection).verify_role()
        await self.connection.execute("SET LOCAL lock_timeout='5s'")
        await self.connection.execute("SET LOCAL statement_timeout='5s'")
        goal = await self.connection.fetchrow(
            "SELECT id FROM switchboard.goals WHERE id=$1 FOR UPDATE", goal_id
        )
        if goal is None:
            raise BrokerRefused(Reason.GOAL)
        row = await self.connection.fetchrow(
            "UPDATE switchboard.invocations SET token_hash=NULL,updated_at=now() "
            "WHERE goal_id=$1 AND id=$2 "
            "AND status IN ('returned','failed','timed_out','stopped','escalated') RETURNING id",
            goal_id,
            invocation_id,
        )
        if row is None:
            raise BrokerRefused(Reason.AUTHENTICATION)

    async def authenticate(self, goal_id: UUID, token: str) -> Principal:
        hashed = token_hash(token)
        await GoalLedger(self.connection).verify_role()
        # Finite local lock/statement waits; caller also owns a total deadline.
        await self.connection.execute("SET LOCAL lock_timeout='5s'")
        await self.connection.execute("SET LOCAL statement_timeout='5s'")
        goal = await self.connection.fetchrow(
            "SELECT id,status,origin,exposed,catalog_digest FROM switchboard.goals "
            "WHERE id=$1 FOR UPDATE",
            goal_id,
        )
        if not goal or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        invocations = await self.connection.fetch(
            "SELECT *,lease_expires_at IS NULL OR lease_expires_at>now() AS lease_valid "
            "FROM switchboard.invocations WHERE goal_id=$1 AND token_hash=$2 FOR UPDATE",
            goal_id,
            hashed,
        )
        if len(invocations) != 1:
            raise BrokerRefused(Reason.AUTHENTICATION)
        invocation = invocations[0]
        if invocation["status"] != "running" or not invocation["lease_valid"]:
            raise BrokerRefused(Reason.AUTHENTICATION)
        if invocation["depth"] == 0:
            # sw047 stores epoch on invocation history, not on the goal row.
            epoch = await self.connection.fetchval(
                "SELECT max(lead_epoch) FROM switchboard.invocations WHERE goal_id=$1 AND depth=0",
                goal_id,
            )
            if epoch != invocation["lead_epoch"]:
                raise BrokerRefused(Reason.EPOCH)
        try:
            snapshot = invocation["grant_snapshot"]
            grants = decode_grants(snapshot)
            if encode_grants(grants) != snapshot or digest(snapshot) != invocation["grant_digest"]:
                raise ValueError("invalid_grant_snapshot")
            if type(goal["exposed"]) is not bool or type(invocation["exposed"]) is not bool:
                raise ValueError("invalid_exposure")
            return Principal(
                goal_id,
                invocation["id"],
                invocation["depth"],
                goal["origin"],
                goal["exposed"] or invocation["exposed"],
                goal["catalog_digest"],
                grants,
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise BrokerRefused(Reason.AUTHENTICATION) from None

    async def listing(
        self, principal: Principal, authorities: Authorities
    ) -> tuple[Capability, ...]:
        catalog = await authorities.catalog(self.connection, principal)
        if catalog.digest != principal.catalog_digest:
            raise BrokerRefused(Reason.CATALOG)
        grants = {grant.capability: grant for grant in principal.grants}
        result = []
        for capability in catalog.capabilities:
            grant = grants.get(capability.identity)
            if grant is None or not grantable(capability, principal.depth):
                continue
            restrictions = dict(grant.resources)
            if not set(restrictions) <= set(capability.resource_params):
                continue
            if any(
                bound and name not in restrictions for name, bound in capability.selection_bound
            ):
                continue
            if any(dict(capability.selection_bound).values()):
                if (
                    await authorities.selection(self.connection, principal, capability, grant)
                    is not True
                ):
                    continue
            result.append(capability)
        return tuple(result)

    async def resolve_call(
        self,
        principal: Principal,
        name: str,
        args: dict,
        args_digest: str,
        authorities: Authorities,
    ) -> tuple[Capability, Decision]:
        """Revalidate current catalog, scope, selection and independent refusals.

        Used by both new calls and the private stored-approved central path.
        An owner decision may satisfy PARK; it never makes REFUSE grantable.
        This performs no budget/effect writes or external I/O.
        """
        catalog = await authorities.catalog(self.connection, principal)
        if catalog.digest != principal.catalog_digest:
            raise BrokerRefused(Reason.CATALOG)
        capability = next((c for c in catalog.capabilities if c.mcp_name == name), None)
        grant = next(
            (g for g in principal.grants if capability and g.capability == capability.identity),
            None,
        )
        if capability is None or grant is None or not grantable(capability, principal.depth):
            raise BrokerRefused(Reason.CAPABILITY)
        restrictions = dict(grant.resources)
        if not set(restrictions) <= set(capability.resource_params):
            raise BrokerRefused(Reason.RESOURCE)
        if any(bound and name not in restrictions for name, bound in capability.selection_bound):
            raise BrokerRefused(Reason.SELECTION)
        for param, permitted in restrictions.items():
            value = args.get(param)
            values = [value] if type(value) is str else value
            if (
                type(values) is not list
                or not values
                or any(type(v) is not str or v not in permitted for v in values)
            ):
                raise BrokerRefused(Reason.RESOURCE)
        if any(dict(capability.selection_bound).values()):
            if (
                await authorities.selection(self.connection, principal, capability, grant)
                is not True
            ):
                raise BrokerRefused(Reason.SELECTION)
        evidence = await authorities.policy(self.connection, principal, capability, args)
        evidence = bind_recipient_policy(capability, args, args_digest, evidence)
        decision = decide(principal, capability, grant, evidence)
        if decision.disposition is Disposition.REFUSE:
            raise BrokerRefused(Reason.POLICY)
        if decision.disposition is Disposition.PARK and not capability.approvals_available:
            raise BrokerRefused(Reason.UNAVAILABLE)
        return capability, decision

    async def reserve(
        self,
        principal: Principal,
        name: str,
        args: dict,
        args_digest: str,
        authorities: Authorities,
    ) -> Reservation | BrokerRefused:
        capability, decision = await self.resolve_call(
            principal, name, args, args_digest, authorities
        )
        budget = await self.connection.fetchrow(
            "SELECT *,deadline>now() AS deadline_valid FROM switchboard.budgets "
            "WHERE goal_id=$1 FOR UPDATE",
            principal.goal_id,
        )
        if budget is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        if (
            not budget["deadline_valid"]
            or budget["used_calls"] + budget["reserved_calls"] >= budget["max_calls"]
        ):
            await self.connection.execute(
                "UPDATE switchboard.goals SET status='budget_exhausted',updated_at=now() "
                "WHERE id=$1",
                principal.goal_id,
            )
            # Return the refusal so the exhaustion transition commits before raising.
            return BrokerRefused(Reason.BUDGET)
        effect_id = None
        call_id = uuid4()
        selected = next(g for g in principal.grants if g.capability == capability.identity)
        if capability.effect_class != "read":
            previous = await self.connection.fetchrow(
                "SELECT id FROM switchboard.effects WHERE goal_id=$1 AND capability=$2 "
                "AND args_digest=$3 AND status IN "
                "('requested','parked','approved','dispatched','committed','unknown') "
                "ORDER BY created_at,id LIMIT 1 FOR UPDATE",
                principal.goal_id,
                capability.identity,
                args_digest,
            )
            if previous:
                raise BrokerRefused(Reason.DUPLICATE, effect_id=previous["id"])
            effect_id = uuid4()
            await self.connection.execute(
                "INSERT INTO switchboard.effects "
                "(id,goal_id,invocation_id,capability,args_digest,status) "
                "VALUES ($1,$2,$3,$4,$5,'requested')",
                effect_id,
                principal.goal_id,
                principal.invocation_id,
                capability.identity,
                args_digest,
            )
        if decision.disposition is Disposition.PARK:
            # The association commits with the original effect and budget charge,
            # even when the owning forward never returns an approval reference.
            if effect_id is None:
                raise BrokerRefused(Reason.UNAVAILABLE)
            await self.connection.execute(
                "INSERT INTO switchboard.goal_park_associations "
                "(effect_id,goal_id,owning_domain,action_id,allow_standing_rule,park_call_id,"
                "selected_grant,catalog_digest) "
                "VALUES ($1,$2,$3,$1,$4,$5,$6,$7)",
                effect_id,
                principal.goal_id,
                capability.domain,
                decision.allow_standing_rule,
                call_id,
                encode_grants((selected,))[0],
                principal.catalog_digest,
            )
        await self.connection.execute(
            "UPDATE switchboard.budgets SET used_calls=used_calls+1,updated_at=now() "
            "WHERE goal_id=$1",
            principal.goal_id,
        )
        return Reservation(
            principal,
            capability,
            decision,
            call_id,
            effect_id,
            args_digest,
            next(g for g in principal.grants if g.capability == capability.identity),
        )

    async def dispatching(self, reservation: Reservation) -> None:
        if reservation.effect_id is not None:
            row = await self.connection.fetchrow(
                "UPDATE switchboard.effects SET status='dispatched',updated_at=now() "
                "WHERE id=$1 AND goal_id=$2 AND status='requested' RETURNING id",
                reservation.effect_id,
                reservation.principal.goal_id,
            )
            if row is None:
                raise BrokerRefused(Reason.UNAVAILABLE)

    async def settle(
        self,
        reservation: Reservation,
        status: str,
        *,
        reference: str | None = None,
        expose: bool = False,
    ) -> str:
        await GoalLedger(self.connection).verify_role()
        await self.connection.execute("SET LOCAL lock_timeout='5s'")
        await self.connection.execute("SET LOCAL statement_timeout='5s'")
        if status not in {"parked", "committed", "failed", "unknown", "refused"}:
            raise ValueError("invalid_settlement")
        goal = await self.connection.fetchrow(
            "SELECT id,status FROM switchboard.goals WHERE id=$1 FOR UPDATE",
            reservation.principal.goal_id,
        )
        if goal is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        stopped_park = (
            reservation.decision.disposition is Disposition.PARK and goal["status"] == "stopped"
        )
        if expose:
            # Same order as authorization/Stop; monotonic even if invocation ended.
            await self.connection.execute(
                "UPDATE switchboard.goals SET exposed=true,updated_at=now() WHERE id=$1",
                reservation.principal.goal_id,
            )
            await self.connection.execute(
                "UPDATE switchboard.invocations SET exposed=true,updated_at=now() "
                "WHERE id=$1 AND goal_id=$2",
                reservation.principal.invocation_id,
                reservation.principal.goal_id,
            )
        if (
            reservation.effect_id is not None
            and reservation.decision.disposition is Disposition.PARK
        ):
            if reference is not None and (
                type(reference) is not str or not 1 <= len(reference) <= 512
            ):
                raise BrokerRefused(Reason.UNAVAILABLE)
            # Full immutable call identity and one-time reference attachment.
            # A late PARK response may locate owning work; it cannot revive a
            # stopped effect or claim cancellation completed.
            row = await self.connection.fetchrow(
                "WITH binding AS ("
                "SELECT e.id FROM switchboard.effects e "
                "JOIN switchboard.goal_park_associations a "
                "ON (a.goal_id,a.effect_id)=(e.goal_id,e.id) "
                "WHERE e.id=$1 AND e.goal_id=$2 AND e.invocation_id=$5 "
                "AND e.capability=$6 AND e.args_digest=$7 AND a.owning_domain=$9 "
                "AND a.action_id=e.id AND a.allow_standing_rule=$10 "
                "AND a.dispatch_intent_id IS NULL FOR UPDATE OF e,a), attached AS ("
                "UPDATE switchboard.goal_park_associations a "
                "SET approval_ref=COALESCE(a.approval_ref,$4) FROM binding b "
                "WHERE a.effect_id=b.id AND ($4::text IS NULL OR a.approval_ref IS NULL "
                "OR a.approval_ref=$4) RETURNING a.effect_id) "
                "UPDATE switchboard.effects e SET status=CASE WHEN $8 THEN e.status ELSE $3 END, "
                "approval_ref=CASE WHEN $3='parked' THEN $4 ELSE e.approval_ref END, "
                "updated_at=now() WHERE e.id=$1 AND e.goal_id=$2 "
                "AND (e.status IN ('requested','parked','approved','unknown') "
                "OR ($8 AND e.status='cancelled')) "
                "AND EXISTS (SELECT 1 FROM attached WHERE effect_id=e.id) RETURNING e.id",
                reservation.effect_id,
                reservation.principal.goal_id,
                status,
                reference,
                reservation.principal.invocation_id,
                reservation.capability.identity,
                reservation.args_digest,
                stopped_park,
                reservation.capability.domain,
                reservation.decision.allow_standing_rule,
            )
            if row is None:
                raise BrokerRefused(Reason.UNAVAILABLE)
        elif reservation.effect_id is not None:
            row = await self.connection.fetchrow(
                "UPDATE switchboard.effects SET status=$3, "
                "approval_ref=CASE WHEN $3='parked' THEN $4 ELSE approval_ref END, "
                "receipt_ref=CASE WHEN $3<>'parked' THEN $4 ELSE receipt_ref END,updated_at=now() "
                "WHERE id=$1 AND goal_id=$2 AND status IN ('requested','dispatched') "
                "AND invocation_id=$5 AND capability=$6 AND args_digest=$7 RETURNING id",
                reservation.effect_id,
                reservation.principal.goal_id,
                status,
                reference,
                reservation.principal.invocation_id,
                reservation.capability.identity,
                reservation.args_digest,
            )
            if row is None:
                raise BrokerRefused(Reason.UNAVAILABLE)
        return "unknown" if stopped_park else status

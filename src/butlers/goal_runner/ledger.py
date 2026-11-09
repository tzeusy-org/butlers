"""Runner SQL over the sw_047/sw_062 goal ledger, one locked goal row per step.

Every method runs inside the caller's transaction on a ``butler_switchboard_rw``
connection and takes the goal row ``FOR UPDATE`` first, the order the broker's
per-call transaction and Stop use (D14). So a delegation, a launch or a
completion either commits before a Stop or is refused after it, and two
children racing the last unit of budget serialise on the goal. No method does
network I/O.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from butlers.capability_broker.boundaries import BoundaryLedger
from butlers.capability_broker.contracts import BrokerRefused, Catalog, Principal, Reason
from butlers.capability_broker.ledger import BrokerLedger
from butlers.capability_broker.recipients import live_owner
from butlers.capability_broker.typed import TypedSchema, parse_schema
from butlers.core.goal_admission import Grant, decode_grants, digest, encode_grants

from .contracts import GoalEnded, InvocationBudgetExhausted, LedgerSummary, Role
from .delegation import DelegationRequest, child_grant

LIVE = ("launching", "running")
# Goal states a Stop fences (D11: running and settling, plus the transient ones).
_STOPPABLE = ("admitted", "running", "awaiting_approval", "recovering", "settling")
# Goal states a failed lead or exhausted budget ends (never settling: its lead is done).
_ACTIVE = ("admitted", "running", "awaiting_approval", "recovering")
_TERMINAL = ("completed", "partial", "failed", "stopped", "budget_exhausted")
# Effects that may still change: settling waits for them (D11).
_UNSETTLED = ("requested", "parked", "approved", "dispatched")
Outcome = Literal["completed", "partial", "failed"]


@dataclass(frozen=True, slots=True)
class Started:
    """A launched invocation, as the ledger recorded it (D17 attributes)."""

    goal_id: UUID
    invocation_id: UUID
    depth: int
    epoch: int
    token: str
    origin: str
    exposed: bool


@dataclass(frozen=True, slots=True)
class Leaderless:
    """A running or recovering goal whose latest lead ended with no successor (D14)."""

    goal_id: UUID
    lead_id: UUID
    end_reason: str | None
    role: Role
    tier: str | None
    relaunch_of: UUID | None


@dataclass(frozen=True, slots=True)
class Expired:
    """An invocation whose lease lapsed, and whether its goal now recovers (D14)."""

    invocation_id: UUID
    depth: int
    role: Role
    tier: str | None
    recover: bool


class RunnerLedger:
    def __init__(self, connection: Any):
        self.connection = connection
        self.broker = BrokerLedger(connection)

    # -- locking --------------------------------------------------------------

    async def lock(self, goal_id: UUID):
        """The goal row ``FOR UPDATE`` (verifies the runtime role first)."""
        return await self.broker._lock_goal(goal_id)

    async def _goal(self, goal_id: UUID, statuses: tuple[str, ...]):
        goal = await self.lock(goal_id)
        if goal is None or goal["status"] not in statuses:
            raise GoalEnded("goal_not_running")
        return goal

    # -- invocation rows ------------------------------------------------------

    async def _reserve_invocation(self, goal_id: UUID) -> None:
        """D10: every launch, relaunch, replacement and escalation draws one invocation."""
        budget = await self.connection.fetchrow(
            "SELECT max_invocations, used_invocations, reserved_invocations, "
            "deadline > clock_timestamp() AS live FROM switchboard.budgets "
            "WHERE goal_id = $1 FOR UPDATE",
            goal_id,
        )
        if budget is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        if (
            budget["live"] is not True
            or budget["used_invocations"] + budget["reserved_invocations"]
            >= budget["max_invocations"]
        ):
            raise InvocationBudgetExhausted("invocation_budget_exhausted")
        await self.connection.execute(
            "UPDATE switchboard.budgets SET used_invocations = used_invocations + 1, "
            "updated_at = now() WHERE goal_id = $1",
            goal_id,
        )

    async def has_invocation_room(self, goal_id: UUID) -> bool:
        await self.lock(goal_id)
        return bool(
            await self.connection.fetchval(
                "SELECT used_invocations + reserved_invocations < max_invocations "
                "AND deadline > clock_timestamp() FROM switchboard.budgets WHERE goal_id = $1",
                goal_id,
            )
        )

    async def _insert(
        self,
        goal_id: UUID,
        *,
        depth: int,
        parent: UUID | None,
        epoch: int,
        grants: tuple[Grant, ...],
        max_turns: int,
        max_tokens: int,
        role: Role,
        tier: str,
        model_id: str | None = None,
        max_calls: int | None = None,
        deadline_seconds: int | None = None,
        relaunch_of: UUID | None = None,
    ) -> UUID:
        await self._reserve_invocation(goal_id)
        invocation_id, snapshot = uuid4(), encode_grants(grants)
        await self.connection.execute(
            "INSERT INTO switchboard.invocations (id, goal_id, parent_invocation_id, depth, role, "
            "lead_epoch, status, grant_snapshot, grant_digest, max_turns, max_tokens, max_calls, "
            "deadline, model_role, model_tier, model_id, relaunch_of) VALUES ($1, $2, $3, $4, $5, "
            "$6, 'launching', $7::jsonb, $8, $9, $10, $11, "
            "CASE WHEN $12::bigint IS NULL THEN NULL "
            "ELSE clock_timestamp() + make_interval(secs => $12::bigint) END, "
            "$13, $14, $15, $16)",
            invocation_id,
            goal_id,
            parent,
            depth,
            "lead" if depth == 0 else "child",
            epoch,
            snapshot,
            digest(snapshot),
            max_turns,
            max_tokens,
            max_calls,
            deadline_seconds,
            role,
            tier,
            model_id,
            relaunch_of,
        )
        return invocation_id

    async def _start(self, goal, invocation_id: UUID, lease_seconds: float) -> Started:
        """launching -> running with a fresh token and lease, in this transaction."""
        token = await self.broker.issue_token(goal["id"], invocation_id)
        row = await self.connection.fetchrow(
            "UPDATE switchboard.invocations SET status = 'running', "
            "started_at = clock_timestamp(), "
            "lease_expires_at = clock_timestamp() + make_interval(secs => $3::float8), "
            "updated_at = now() WHERE goal_id = $1 AND id = $2 AND status = 'launching' "
            "RETURNING depth, lead_epoch, exposed",
            goal["id"],
            invocation_id,
            lease_seconds,
        )
        if row is None:
            raise GoalEnded("invocation_not_launching")
        return Started(
            goal["id"],
            invocation_id,
            row["depth"],
            row["lead_epoch"],
            token,
            goal["origin"],
            row["exposed"],
        )

    async def open_lead(
        self,
        goal_id: UUID,
        *,
        role: Role,
        tier: str,
        model_id: str,
        max_turns: int,
        max_tokens: int,
        lease_seconds: float,
        relaunch_of: UUID | None = None,
    ) -> Started:
        """Launch a lead: on admission, recovery, relaunch or escalation (D8, D14, D16).

        The epoch is one past every earlier lead of the goal, so the new lead's
        insert is what revokes any predecessor's token (D14). The goal ceiling
        is the lead's grant; exposure and labels come from the ledger triggers.
        """
        goal = await self._goal(goal_id, ("admitted", "running", "recovering"))
        if await self.connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM switchboard.invocations WHERE goal_id = $1 "
            "AND depth = 0 AND status = ANY($2::text[]))",
            goal_id,
            list(LIVE),
        ):
            raise GoalEnded("lead_active")
        if relaunch_of is not None and not await self.connection.fetchval(
            # D14: one relaunch, of the latest lead only, never twice.
            "SELECT (SELECT id FROM switchboard.invocations WHERE goal_id = $1 AND depth = 0 "
            "ORDER BY lead_epoch DESC, created_at DESC LIMIT 1) = $2 "
            "AND NOT EXISTS (SELECT 1 FROM switchboard.invocations WHERE goal_id = $1 "
            "AND relaunch_of = $2)",
            goal_id,
            relaunch_of,
        ):
            raise GoalEnded("relaunch_unavailable")
        epoch = await self.connection.fetchval(
            "SELECT COALESCE(max(lead_epoch) + 1, 0) FROM switchboard.invocations "
            "WHERE goal_id = $1 AND depth = 0",
            goal_id,
        )
        await self.connection.execute(
            "UPDATE switchboard.goals SET status = 'running', updated_at = now() "
            "WHERE id = $1 AND status <> 'running'",
            goal_id,
        )
        invocation_id = await self._insert(
            goal_id,
            depth=0,
            parent=None,
            epoch=epoch,
            grants=decode_grants(goal["ceiling"]),
            max_turns=max_turns,
            max_tokens=max_tokens,
            role=role,
            tier=tier,
            model_id=model_id,
            relaunch_of=relaunch_of,
        )
        return await self._start(goal, invocation_id, lease_seconds)

    async def delegate(
        self,
        principal: Principal,
        catalog: Catalog,
        request: DelegationRequest,
        *,
        max_turns: int,
    ) -> UUID | BrokerRefused:
        """D9: admit one child, conditional on the goal running and the lead's epoch.

        ``principal`` was authenticated in this transaction, so the epoch is
        current. Depth comes from the caller's row, never from the request.
        Budget exhaustion is *returned* so the caller commits the goal's
        ``budget_exhausted`` transition before refusing.
        """
        goal = await self.lock(principal.goal_id)
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        if principal.depth != 0:
            raise BrokerRefused(Reason.CAPABILITY)
        if catalog.digest != principal.catalog_digest:
            raise BrokerRefused(Reason.CATALOG)
        grants = child_grant(request.capabilities, principal.grants, catalog)
        exhausted = await self.broker.charge_call(principal)
        if exhausted is not None:
            return exhausted
        budget = await self.connection.fetchrow(
            "SELECT max_children, max_calls - used_calls - reserved_calls AS calls_left, "
            "max_tokens - used_tokens - reserved_tokens AS tokens_left, "
            "extract(epoch FROM deadline - clock_timestamp()) AS seconds_left "
            "FROM switchboard.budgets WHERE goal_id = $1 FOR UPDATE",
            principal.goal_id,
        )
        active = await self.connection.fetchval(
            "SELECT count(*) FROM switchboard.invocations WHERE goal_id = $1 AND depth = 1 "
            "AND status = ANY($2::text[])",
            principal.goal_id,
            list(LIVE),
        )
        share = request.share
        if (
            budget is None
            or active >= budget["max_children"]
            or share.calls > budget["calls_left"]
            or share.tokens > budget["tokens_left"]
            or share.wall_seconds > (budget["seconds_left"] or 0)
        ):
            raise BrokerRefused(Reason.BUDGET)
        epoch = await self.connection.fetchval(
            "SELECT lead_epoch FROM switchboard.invocations WHERE goal_id = $1 AND id = $2",
            principal.goal_id,
            principal.invocation_id,
        )
        try:
            child_id = await self._insert(
                principal.goal_id,
                depth=1,
                parent=principal.invocation_id,
                epoch=epoch,
                grants=grants,
                max_turns=max_turns,
                max_tokens=share.tokens,
                role="child",
                tier=request.model_tier,
                max_calls=share.calls,
                deadline_seconds=share.wall_seconds,
            )
        except InvocationBudgetExhausted:
            raise BrokerRefused(Reason.BUDGET) from None
        await BoundaryLedger(self.broker).fix_result_schema(
            principal.goal_id, child_id, request.result_schema
        )
        return child_id

    async def start_child(
        self, goal_id: UUID, child_id: UUID, model_id: str, lease_seconds: float
    ) -> Started:
        """Bind the resolved model to a delegated child and start it."""
        goal = await self._goal(goal_id, ("running",))
        bound = await self.connection.fetchval(
            "UPDATE switchboard.invocations SET model_id = $3, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2 AND depth = 1 AND status = 'launching' "
            "AND model_id IS NULL RETURNING true",
            goal_id,
            child_id,
            model_id,
        )
        if bound is not True:
            raise GoalEnded("invocation_not_launching")
        return await self._start(goal, child_id, lease_seconds)

    async def relaunch_child(
        self, goal_id: UUID, failed_id: UUID, model_id: str, lease_seconds: float
    ) -> Started:
        """D14: the failed child's one relaunch, as a new invocation with its share."""
        goal = await self._goal(goal_id, ("running",))
        old = await self.connection.fetchrow(
            "SELECT i.*, r.result_schema::text AS schema_text FROM switchboard.invocations i "
            "LEFT JOIN switchboard.goal_child_results r "
            "ON (r.goal_id, r.invocation_id) = (i.goal_id, i.id) "
            "WHERE i.goal_id = $1 AND i.id = $2 AND i.depth = 1 AND i.status = 'failed' "
            "AND i.end_reason = 'runtime_failure'",
            goal_id,
            failed_id,
        )
        if old is None or old["schema_text"] is None:
            raise GoalEnded("relaunch_unavailable")
        seconds = None
        if old["deadline"] is not None:
            seconds = await self.connection.fetchval(
                "SELECT greatest(ceil(extract(epoch FROM $1::timestamptz - clock_timestamp())), 1)"
                "::bigint",
                old["deadline"],
            )
        child_id = await self._insert(
            goal_id,
            depth=1,
            parent=old["parent_invocation_id"],
            epoch=old["lead_epoch"],
            grants=decode_grants(old["grant_snapshot"]),
            max_turns=old["max_turns"],
            max_tokens=old["max_tokens"],
            role="child",
            tier=old["model_tier"],
            model_id=model_id,
            max_calls=old["max_calls"],
            deadline_seconds=seconds,
            relaunch_of=failed_id,
        )
        schema: TypedSchema = parse_schema(json.loads(old["schema_text"]))
        await BoundaryLedger(self.broker).fix_result_schema(goal_id, child_id, schema)
        return await self._start(goal, child_id, lease_seconds)

    async def end(self, goal_id: UUID, invocation_id: UUID, status: str, reason: str) -> bool:
        """End a live invocation and revoke its token; False if it had already ended.

        An invocation the ledger ended another way (``goal.return``, Stop,
        escalation, a lapsed lease) keeps that end; only its token is cleared.
        """
        await self.lock(goal_id)
        ended = await self.connection.fetchval(
            "UPDATE switchboard.invocations SET status = $3, end_reason = $4, "
            "ended_at = clock_timestamp(), token_hash = NULL, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2 AND status = ANY($5::text[]) RETURNING true",
            goal_id,
            invocation_id,
            status,
            reason,
            list(LIVE),
        )
        if ended is not True:
            await self.connection.execute(
                "UPDATE switchboard.invocations SET token_hash = NULL, updated_at = now() "
                "WHERE goal_id = $1 AND id = $2 AND token_hash IS NOT NULL",
                goal_id,
                invocation_id,
            )
        return ended is True

    async def goal_status(self, goal_id: UUID) -> str | None:
        return await self.connection.fetchval(
            "SELECT status FROM switchboard.goals WHERE id = $1", goal_id
        )

    async def status_of(self, goal_id: UUID, invocation_id: UUID) -> str | None:
        return await self.connection.fetchval(
            "SELECT status FROM switchboard.invocations WHERE goal_id = $1 AND id = $2",
            goal_id,
            invocation_id,
        )

    async def recorded_calls(self, goal_id: UUID, invocation_id: UUID) -> list[dict[str, str]]:
        """Every broker call made with the invocation's token (D14), names only.

        Charged calls, effect rows and refusal records all count: a refused call
        still reached the broker, and a relaunch must never follow one.
        """
        used = await self.connection.fetchval(
            "SELECT used_calls FROM switchboard.invocations WHERE goal_id = $1 AND id = $2",
            goal_id,
            invocation_id,
        )
        names = [
            row["capability"]
            for row in await self.connection.fetch(
                "SELECT capability FROM switchboard.effects WHERE goal_id = $1 "
                "AND invocation_id = $2 UNION ALL SELECT capability "
                "FROM switchboard.broker_refusals WHERE goal_id = $1 AND invocation_id = $2",
                goal_id,
                invocation_id,
            )
        ]
        calls = [{"name": name} for name in names]
        if (used or 0) > len(calls):
            calls.append({"name": "broker_call"})
        return calls

    # -- goal tools -----------------------------------------------------------

    async def model_of(self, principal: Principal) -> tuple[Role, str | None]:
        row = await self.connection.fetchrow(
            "SELECT model_role, model_tier FROM switchboard.invocations "
            "WHERE goal_id = $1 AND id = $2",
            principal.goal_id,
            principal.invocation_id,
        )
        return (row["model_role"] or "lead"), row["model_tier"]

    async def stop_child(self, principal: Principal, child_id: Any) -> UUID:
        """``goal.stop_child``: the lead stops one child of its goal."""
        goal = await self.lock(principal.goal_id)
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        if principal.depth != 0:
            raise BrokerRefused(Reason.CAPABILITY)
        try:
            identity = UUID(child_id) if type(child_id) is str else None
        except ValueError:
            identity = None
        if identity is None or str(identity) != child_id:
            raise BrokerRefused(Reason.RESOURCE)
        child = await self.connection.fetchrow(
            "SELECT status FROM switchboard.invocations WHERE goal_id = $1 AND id = $2 "
            "AND depth = 1",
            principal.goal_id,
            identity,
        )
        if child is None:
            raise BrokerRefused(Reason.RESOURCE)
        await self.connection.execute(
            "UPDATE switchboard.invocations SET status = 'stopped', end_reason = 'child_stopped', "
            "ended_at = clock_timestamp(), token_hash = NULL, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2 AND status = ANY($3::text[])",
            principal.goal_id,
            identity,
            list(LIVE),
        )
        return identity

    async def escalate(self, principal: Principal) -> bool:
        """``goal.escalate``: end the current lead ``escalated`` (D16)."""
        goal = await self.lock(principal.goal_id)
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        if principal.depth != 0:
            raise BrokerRefused(Reason.CAPABILITY)
        return await self.end(principal.goal_id, principal.invocation_id, "escalated", "escalated")

    async def complete(
        self,
        principal: Principal,
        outcome: Outcome,
        relied: tuple[UUID, ...],
        reply: UUID | None,
    ) -> list[UUID] | BrokerRefused:
        """``goal.complete``: record the outcome; the goal settles (D11, D12).

        Live children are stopped with it (returned for termination). The goal
        enters ``settling`` and reaches its final state once no effect can still
        change; ``resolve`` runs at once for a goal with nothing outstanding.
        """
        goal = await self.lock(principal.goal_id)
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        if principal.depth != 0:
            raise BrokerRefused(Reason.CAPABILITY)
        named = set(relied) | ({reply} if reply else set())
        if named:
            found = await self.connection.fetchval(
                "SELECT count(*) FROM switchboard.effects WHERE goal_id = $1 "
                "AND id = ANY($2::uuid[])",
                principal.goal_id,
                sorted(named),
            )
            if found != len(named):
                raise BrokerRefused(Reason.RESOURCE)
        if reply is not None and not await self._owner_delivery(principal.goal_id, reply):
            raise BrokerRefused(Reason.RESOURCE)
        exhausted = await self.broker.charge_call(principal)
        if exhausted is not None:
            return exhausted
        await self.connection.execute(
            "INSERT INTO switchboard.goal_outcomes "
            "(goal_id, invocation_id, outcome, relied_effects, reply_effect_id) "
            "VALUES ($1, $2, $3, $4::uuid[], $5)",
            principal.goal_id,
            principal.invocation_id,
            outcome,
            sorted(set(relied)),
            reply,
        )
        children = await self._end_live(principal.goal_id, "stopped", "goal_ended", depth=1)
        await self.end(principal.goal_id, principal.invocation_id, "returned", "completed")
        await self.connection.execute(
            "UPDATE switchboard.goals SET status = 'settling', updated_at = now() WHERE id = $1",
            principal.goal_id,
        )
        await self.resolve(principal.goal_id)
        return children

    async def _owner_delivery(self, goal_id: UUID, effect_id: UUID) -> bool:
        """The effect is the goal's communication to the live owner (D11).

        The broker records the owner as recipient only for a communicate
        capability whose every party it resolved to the owner.
        """
        owner = await live_owner(self.connection)
        return owner is not None and bool(
            await self.connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM switchboard.effects WHERE goal_id = $1 "
                "AND id = $2 AND recipient_entity = $3)",
                goal_id,
                effect_id,
                owner,
            )
        )

    async def resolve(self, goal_id: UUID) -> str | None:
        """settling -> completed | partial | failed once no effect can change (D11).

        A goal admitted from an owner message completes only if its reply is an
        owner delivery that committed; otherwise it ends ``partial`` with the
        delivery failure recorded.
        """
        goal = await self.lock(goal_id)
        if goal is None or goal["status"] != "settling":
            return None
        if await self.connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM switchboard.effects WHERE goal_id = $1 "
            "AND status = ANY($2::text[]))",
            goal_id,
            list(_UNSETTLED),
        ):
            return None
        row = await self.connection.fetchrow(
            "SELECT o.outcome, o.reply_effect_id, g.source_kind, e.status AS reply_status "
            "FROM switchboard.goal_outcomes o JOIN switchboard.goals g ON g.id = o.goal_id "
            "LEFT JOIN switchboard.effects e "
            "ON (e.goal_id, e.id) = (o.goal_id, o.reply_effect_id) WHERE o.goal_id = $1",
            goal_id,
        )
        if row is None:
            final, failure = "failed", None
        else:
            final, failure = row["outcome"], None
            owner_reply = goal["origin"] == "owner" and row["source_kind"] == "message"
            if final == "completed" and owner_reply:
                if row["reply_effect_id"] is None:
                    final, failure = "partial", "reply_missing"
                elif not await self._owner_delivery(goal_id, row["reply_effect_id"]):
                    final, failure = "partial", "reply_not_owner_delivery"
                elif row["reply_status"] != "committed":
                    final, failure = "partial", "reply_not_committed"
            await self.connection.execute(
                "UPDATE switchboard.goal_outcomes SET final_status = $2, delivery_failure = $3, "
                "resolved_at = clock_timestamp() WHERE goal_id = $1",
                goal_id,
                final,
                failure,
            )
        await self.connection.execute(
            "UPDATE switchboard.goals SET status = $2, updated_at = now() WHERE id = $1",
            goal_id,
            final,
        )
        return final

    # -- Stop, fencing and recovery (D14) -------------------------------------

    async def _end_live(
        self, goal_id: UUID, status: str, reason: str, *, depth: int | None = None
    ) -> list[UUID]:
        rows = await self.connection.fetch(
            "UPDATE switchboard.invocations SET status = $2, end_reason = $3, "
            "ended_at = clock_timestamp(), token_hash = NULL, updated_at = now() "
            "WHERE goal_id = $1 AND status = ANY($4::text[]) AND ($5::smallint IS NULL "
            "OR depth = $5::smallint) RETURNING id",
            goal_id,
            status,
            reason,
            list(LIVE),
            depth,
        )
        return [row["id"] for row in rows]

    async def stop(self, goal_id: UUID) -> tuple[bool, list[UUID]]:
        """Owner Stop in one transaction: stopped, every token revoked (D14).

        Returns whether this Stop ended the goal and the invocations whose
        process domains must be terminated. Stop is idempotent: an already
        ended goal keeps its status, and anything of it still live is fenced.
        """
        goal = await self.lock(goal_id)
        if goal is None:
            raise BrokerRefused(Reason.GOAL)
        if goal["status"] not in _STOPPABLE:
            return False, await self._end_live(goal_id, "stopped", "goal_ended")
        await self.connection.execute(
            "UPDATE switchboard.goals SET status = 'stopped', updated_at = now() WHERE id = $1",
            goal_id,
        )
        live = await self._end_live(goal_id, "stopped", "stopped")
        await self.connection.execute(
            "UPDATE switchboard.invocations SET token_hash = NULL, updated_at = now() "
            "WHERE goal_id = $1 AND token_hash IS NOT NULL",
            goal_id,
        )
        return True, live

    async def end_goal(self, goal_id: UUID, status: str, reason: str) -> list[UUID]:
        """End an active goal ``failed`` or ``budget_exhausted`` and fence it."""
        if status not in ("failed", "budget_exhausted"):
            raise ValueError("goal_end_status")
        goal = await self.lock(goal_id)
        if goal is None:
            return []
        if goal["status"] in _ACTIVE:
            await self.connection.execute(
                "UPDATE switchboard.goals SET status = $2, updated_at = now() WHERE id = $1",
                goal_id,
                status,
            )
        elif goal["status"] not in _TERMINAL:
            return []
        return await self._end_live(goal_id, "stopped", reason)

    async def renew(self, goal_id: UUID, invocation_id: UUID, lease_seconds: float) -> bool:
        """Extend a live lease; a lapsed lease is never revived."""
        renewed = await self.connection.fetchval(
            "UPDATE switchboard.invocations SET "
            "lease_expires_at = clock_timestamp() + make_interval(secs => $3::float8), "
            "updated_at = now() WHERE goal_id = $1 AND id = $2 AND status = 'running' "
            "AND lease_expires_at > clock_timestamp() RETURNING true",
            goal_id,
            invocation_id,
            lease_seconds,
        )
        return renewed is True

    async def lapsed(self) -> list[tuple[UUID, UUID]]:
        rows = await self.connection.fetch(
            "SELECT goal_id, id FROM switchboard.invocations WHERE status = 'running' "
            "AND lease_expires_at <= clock_timestamp() ORDER BY goal_id, id"
        )
        return [(row["goal_id"], row["id"]) for row in rows]

    async def expire(self, goal_id: UUID, invocation_id: UUID) -> Expired | None:
        """A lapsed lease ends its invocation; a lapsed lead puts the goal in recovery."""
        goal = await self.lock(goal_id)
        if goal is None:
            return None
        row = await self.connection.fetchrow(
            "UPDATE switchboard.invocations SET status = 'timed_out', end_reason = 'lease_lapsed', "
            "ended_at = clock_timestamp(), token_hash = NULL, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2 AND status = 'running' "
            "AND lease_expires_at <= clock_timestamp() "
            "RETURNING depth, model_role, model_tier",
            goal_id,
            invocation_id,
        )
        if row is None:
            return None
        recover = row["depth"] == 0 and goal["status"] in ("running", "awaiting_approval")
        if recover:
            await self.connection.execute(
                "UPDATE switchboard.goals SET status = 'recovering', updated_at = now() "
                "WHERE id = $1",
                goal_id,
            )
        return Expired(
            invocation_id, row["depth"], row["model_role"] or "lead", row["model_tier"], recover
        )

    async def overdue(self) -> list[tuple[UUID, UUID]]:
        """Running children past their D9 wall-time share."""
        rows = await self.connection.fetch(
            "SELECT goal_id, id FROM switchboard.invocations WHERE status = ANY($1::text[]) "
            "AND deadline <= clock_timestamp() ORDER BY goal_id, id",
            list(LIVE),
        )
        return [(row["goal_id"], row["id"]) for row in rows]

    async def goals_to_fence(self) -> list[tuple[UUID, str]]:
        """Goals past their deadline, or ended while an invocation still lives.

        Returns ``(goal_id, status_to_set)``; an already-ended goal keeps its
        status (``""``), a running goal past its deadline becomes
        ``budget_exhausted`` (D10).
        """
        rows = await self.connection.fetch(
            "SELECT g.id, g.status, b.deadline <= clock_timestamp() AS overdue "
            "FROM switchboard.goals g JOIN switchboard.budgets b ON b.goal_id = g.id "
            "WHERE (g.status = ANY($1::text[]) AND b.deadline <= clock_timestamp()) "
            "OR (g.status = ANY($2::text[]) AND EXISTS (SELECT 1 FROM switchboard.invocations i "
            "WHERE i.goal_id = g.id AND i.status = ANY($3::text[]))) ORDER BY g.id",
            list(_ACTIVE),
            list(_TERMINAL),
            list(LIVE),
        )
        return [(row["id"], "budget_exhausted" if row["status"] in _ACTIVE else "") for row in rows]

    async def fence_ended(self, goal_id: UUID) -> list[UUID]:
        goal = await self.lock(goal_id)
        if goal is None or goal["status"] not in _TERMINAL:
            return []
        return await self._end_live(goal_id, "stopped", "goal_ended")

    async def stopped_with_parked(self) -> list[UUID]:
        """Stopped goals whose parked effects a failed cancellation left (D14)."""
        rows = await self.connection.fetch(
            "SELECT DISTINCT g.id FROM switchboard.goals g JOIN switchboard.effects e "
            "ON e.goal_id = g.id WHERE g.status = 'stopped' "
            "AND e.status IN ('requested', 'parked', 'approved') ORDER BY g.id"
        )
        return [row["id"] for row in rows]

    async def lead_wanted(self, goal_id: UUID) -> bool:
        """A lead may open: the goal is runnable and no lead of it is live.

        A pre-check only; ``open_lead`` decides under the goal-row lock.
        """
        return bool(
            await self.connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM switchboard.goals g WHERE g.id = $1 "
                "AND g.status IN ('admitted', 'running', 'recovering') "
                "AND NOT EXISTS (SELECT 1 FROM switchboard.invocations i "
                "WHERE i.goal_id = g.id AND i.depth = 0 AND i.status = ANY($2::text[])))",
                goal_id,
                list(LIVE),
            )
        )

    async def child_wanted(self, goal_id: UUID, child_id: UUID) -> bool:
        """A delegated child still waits for its launch (pre-check only)."""
        return (
            await self.connection.fetchval(
                "SELECT status FROM switchboard.invocations WHERE goal_id = $1 AND id = $2 "
                "AND depth = 1",
                goal_id,
                child_id,
            )
            == "launching"
        )

    async def leaderless(self, grace_seconds: float) -> list[Leaderless]:
        """Running or recovering goals whose latest lead ended over ``grace_seconds`` ago.

        A runner that dies between ending a lead (lease expiry, escalation, a
        runtime failure) and launching its successor leaves the goal here; an
        in-process successor opens well inside the grace.
        """
        rows = await self.connection.fetch(
            "SELECT g.id AS goal_id, l.id, l.end_reason, l.model_role, l.model_tier, "
            "l.relaunch_of FROM switchboard.goals g "
            "JOIN LATERAL (SELECT * FROM switchboard.invocations i WHERE i.goal_id = g.id "
            "AND i.depth = 0 ORDER BY i.lead_epoch DESC, i.created_at DESC LIMIT 1) l ON true "
            "WHERE g.status IN ('running', 'recovering') "
            "AND l.status <> ALL($1::text[]) "
            "AND l.ended_at <= clock_timestamp() - make_interval(secs => $2::float8) "
            "AND NOT EXISTS (SELECT 1 FROM switchboard.goal_outcomes o WHERE o.goal_id = g.id) "
            "ORDER BY g.id",
            list(LIVE),
            grace_seconds,
        )
        return [
            Leaderless(
                row["goal_id"],
                row["id"],
                row["end_reason"],
                row["model_role"] or "lead",
                row["model_tier"],
                row["relaunch_of"],
            )
            for row in rows
        ]

    async def end_leaderless(
        self, goal_id: UUID, lead_id: UUID, status: str, reason: str
    ) -> list[UUID]:
        """End a leaderless goal, only if ``lead_id`` is still its latest, ended lead."""
        await self.lock(goal_id)
        latest = await self.connection.fetchrow(
            "SELECT id, status FROM switchboard.invocations WHERE goal_id = $1 AND depth = 0 "
            "ORDER BY lead_epoch DESC, created_at DESC LIMIT 1",
            goal_id,
        )
        if latest is None or latest["id"] != lead_id or latest["status"] in LIVE:
            return []
        return await self.end_goal(goal_id, status, reason)

    async def settling(self) -> list[UUID]:
        rows = await self.connection.fetch(
            "SELECT id FROM switchboard.goals WHERE status = 'settling' ORDER BY id"
        )
        return [row["id"] for row in rows]

    async def summary(self, goal_id: UUID) -> LedgerSummary:
        """The effect ledger and returned child results, identifiers only (D14)."""
        effects = await self.connection.fetch(
            "SELECT id, capability, status FROM switchboard.effects WHERE goal_id = $1 "
            "ORDER BY created_at, id",
            goal_id,
        )
        children = await self.connection.fetch(
            "SELECT id, status FROM switchboard.invocations WHERE goal_id = $1 AND depth = 1 "
            "ORDER BY created_at, id",
            goal_id,
        )
        return LedgerSummary(
            tuple((str(e["id"]), e["capability"], e["status"]) for e in effects),
            tuple((str(c["id"]), c["status"]) for c in children),
        )

    # -- usage metering (D10) -------------------------------------------------

    async def meter(
        self,
        goal_id: UUID,
        invocation_id: UUID,
        *,
        turns: int,
        tokens: int,
        spend: Decimal,
    ) -> tuple[str, list[UUID]]:
        """Charge model usage; fail closed on any limit.

        Returns ``("ok", [])``, ``("invocation_exhausted", [invocation])`` when
        the invocation's turns or tokens are spent, or ``("goal_exhausted",
        live)`` when the goal's tokens or spend are, which ends the goal
        ``budget_exhausted`` with whatever results and effects exist. The
        counters never pass their limits (sw_047 checks); usage beyond a limit
        is recorded at the limit and ends the work instead.
        """
        if turns < 0 or tokens < 0 or spend < 0 or not spend.is_finite():
            raise ValueError("usage")
        goal = await self.lock(goal_id)
        if goal is None:
            raise BrokerRefused(Reason.GOAL)
        budget = await self.connection.fetchrow(
            "SELECT used_tokens + $2 <= max_tokens - reserved_tokens "
            "AND used_spend + $3 <= max_spend - reserved_spend AS fits "
            "FROM switchboard.budgets WHERE goal_id = $1 FOR UPDATE",
            goal_id,
            tokens,
            spend,
        )
        invocation = await self.connection.fetchrow(
            "SELECT used_turns + $3 <= max_turns AS turns_fit, "
            "used_tokens + $4 <= max_tokens AS tokens_fit FROM switchboard.invocations "
            "WHERE goal_id = $1 AND id = $2 FOR UPDATE",
            goal_id,
            invocation_id,
            turns,
            tokens,
        )
        if budget is None or invocation is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        await self.connection.execute(
            "UPDATE switchboard.budgets SET "
            "used_tokens = least(used_tokens + $2, max_tokens - reserved_tokens), "
            "used_spend = least(used_spend + $3, max_spend - reserved_spend), updated_at = now() "
            "WHERE goal_id = $1",
            goal_id,
            tokens,
            spend,
        )
        await self.connection.execute(
            "UPDATE switchboard.invocations SET used_turns = least(used_turns + $3, max_turns), "
            "used_tokens = least(used_tokens + $4, max_tokens), updated_at = now() "
            "WHERE goal_id = $1 AND id = $2",
            goal_id,
            invocation_id,
            turns,
            tokens,
        )
        if budget["fits"] is not True:
            return "goal_exhausted", await self.end_goal(
                goal_id, "budget_exhausted", "budget_exhausted"
            )
        if invocation["turns_fit"] is not True or invocation["tokens_fit"] is not True:
            reason = (
                "turns_exhausted" if invocation["turns_fit"] is not True else "tokens_exhausted"
            )
            ended = await self.end(goal_id, invocation_id, "failed", reason)
            return "invocation_exhausted", [invocation_id] if ended else []
        return "ok", []

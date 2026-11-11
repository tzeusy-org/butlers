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
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from butlers.capability_broker.boundaries import BoundaryLedger
from butlers.capability_broker.contracts import BrokerRefused, Catalog, Principal, Reason
from butlers.capability_broker.ledger import BrokerLedger
from butlers.capability_broker.recipients import live_owner
from butlers.capability_broker.typed import TypedSchema, parse_schema
from butlers.conditions import ConditionEvaluator, ConditionsUnavailable, RegistrationRefused
from butlers.conditions.predicate import digest as predicate_digest
from butlers.conditions.predicate import sources as predicate_sources
from butlers.conditions.service import StoreConflict
from butlers.conditions.sql import ConnectionConditionStore
from butlers.core.goal_admission import Grant, decode_grants, digest, encode_grants, intersect
from butlers.core.goal_labels import Label
from butlers.core.goal_waiting import WaitLedger
from butlers.core.goal_wake import WakeAdmission

from .contracts import (
    GoalBudgetView,
    GoalEnded,
    InvocationBudgetExhausted,
    InvocationShare,
    LaunchFacts,
    LedgerSummary,
    PlanSummary,
    Role,
)
from .delegation import DelegationRequest, child_grant
from .own_conditions import HORIZON, OwnEffect, grant_for, named, snapshot_for

logger = logging.getLogger(__name__)

LIVE = ("launching", "running")
# Goal states a Stop fences (RFC 0038 S2: a waiting goal is stoppable too).
_STOPPABLE = ("admitted", "running", "waiting")
# Goal states a failed lead or exhausted budget ends (never waiting: its lead is done).
_ACTIVE = ("admitted", "running")
_TERMINAL = ("completed", "partial", "failed", "stopped", "budget_exhausted")
# Pending inbox ids a summary lead's packet lists; the count covers the rest.
INBOX_LISTED = 20
# Effects that may still change: settling waits for them (D11).
_UNSETTLED = ("requested", "parked", "approved", "dispatched")
Outcome = Literal["completed", "partial", "failed"]
# goal.plan: references per list (the sw_063 column bound is 64) and the goal-owned rows each
# reference kind names. A reference is ``<kind>:<id>``; every id is checked against this goal.
MAX_PLAN_REFS = 32
_PLAN_REF_ROWS = {
    "effect": "SELECT id::text FROM switchboard.effects WHERE goal_id = $1 AND id = ANY($2)",
    "inbox": "SELECT id::text FROM switchboard.goal_inbox WHERE goal_id = $1 AND id = ANY($2)",
    "child": "SELECT id::text FROM switchboard.invocations "
    "WHERE goal_id = $1 AND depth = 1 AND id = ANY($2)",
}


def plan_refs(value: Any) -> tuple[str, ...]:
    """One ``goal.plan`` reference list, canonical and bounded, or a POLICY refusal.

    Only the closed ``effect:|inbox:|child:<uuid>`` and ``plan:<version>`` spellings are
    references; anything else (free text included) is refused, as is a repeat or a list past
    ``MAX_PLAN_REFS``. Ownership is the ledger's check, in the goal's transaction.
    """
    if type(value) is not list or len(value) > MAX_PLAN_REFS:
        raise BrokerRefused(Reason.POLICY)
    refs: list[str] = []
    for item in value:
        kind, _, ident = item.partition(":") if type(item) is str else ("", "", "")
        if kind == "plan":
            valid = ident.isascii() and ident.isdigit() and not ident.startswith("0")
        elif kind in _PLAN_REF_ROWS:
            try:
                valid = str(UUID(ident)) == ident
            except ValueError:
                valid = False
        else:
            valid = False
        if not valid or item in refs:
            raise BrokerRefused(Reason.POLICY)
        refs.append(item)
    return tuple(refs)


def published(grants: tuple[Grant, ...], catalog: Catalog) -> tuple[Grant, ...]:
    """The grants current policy still allows: published, and not transact (admission's set)."""
    # Kept equal to admission's eligible set (goal_admission ``c.presentable and not transact``):
    # the broker catalog holds only ``item.grantable`` descriptors (BrokerCatalogReader.load in
    # core/capability_catalog.py), the same flag admission reads as ``presentable``.
    return tuple(
        grant
        for grant in grants
        if (capability := catalog.get(grant.capability)) is not None
        and capability.effect_class != "transact"
    )


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
class WakeClaim:
    """A claimed wake intent: the one successor epoch a fire minted (RFC 0038 S2.2)."""

    goal_id: UUID
    intent_id: UUID
    epoch: int


@dataclass(frozen=True, slots=True)
class Leaderless:
    """A running or recovering goal whose latest lead ended with no successor (D14)."""

    goal_id: UUID
    lead_id: UUID
    end_reason: str | None
    role: Role
    tier: str | None
    relaunch_of: UUID | None
    deferred_seconds: float | None = None
    """For a lead the host had no room for: how long since the first such lead in a row."""
    relaunch_spent: bool = False
    """A lead in that row was a D14 relaunch, so its replacement must not relaunch again."""


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

    async def _lead_grants(
        self, goal, wake: WakeClaim | None, catalog: Catalog | None
    ) -> tuple[Grant, ...]:
        """The new lead's grant: the ceiling narrowed by its wake or its predecessor (S2.2)."""
        ceiling = decode_grants(goal["ceiling"])
        if wake is not None:
            assert catalog is not None
            snapshot = await self.connection.fetchval(
                "SELECT e.grant_snapshot FROM switchboard.wake_intents w "
                "JOIN switchboard.wait_envelopes e ON e.goal_id = w.goal_id "
                "AND e.id = w.envelope_id WHERE w.goal_id = $1 AND w.id = $2",
                goal["id"],
                wake.intent_id,
            )
            if snapshot is None:
                raise GoalEnded("wake_envelope_missing")
            return published(intersect(ceiling, decode_grants(snapshot)), catalog)
        latest = await self.connection.fetchval(
            "SELECT grant_snapshot FROM switchboard.invocations WHERE goal_id = $1 "
            "AND depth = 0 ORDER BY lead_epoch DESC, created_at DESC LIMIT 1",
            goal["id"],
        )
        if latest is None:
            return ceiling
        return intersect(ceiling, decode_grants(latest))

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
        wake: WakeClaim | None = None,
        catalog: Catalog | None = None,
    ) -> Started:
        """Launch a lead: on admission, wake, recovery, relaunch or escalation (D8, D14, D16).

        A wake lead runs at the epoch its fire minted and marks that claimed wake intent
        launched in the same transaction, so a crash either leaves the intent claimed (the
        sweep retries the same intent) or the lead recorded (recovery owns it): never two.

        The epoch is one past every earlier lead of the goal, so the new lead's
        insert is what revokes any predecessor's token (D14). Exposure and labels come from
        the ledger triggers. The grant only ever narrows (RFC 0038 S2.2, bu-dhloi1.305):

        - the goal's first lead takes the goal ceiling;
        - a wake lead takes the ceiling ∩ the fired envelope's grant snapshot ∩ current policy
          (``catalog``, the live published catalog: what is still published and not transact,
          admission's eligible set), so a policy change while the goal waited narrows the wake;
        - any other successor (relaunch, recovery, escalation) takes the ceiling ∩ the latest
          lead's grant, so a failed narrowed lead's replacement stays narrowed.
        """
        if wake is not None and catalog is None:
            raise ValueError("a wake lead is narrowed by the live catalog")
        goal = await self._goal(goal_id, ("admitted", "running"))
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
        if wake is not None and wake.epoch != epoch:
            raise GoalEnded("wake_epoch_mismatch")
        if wake is not None and not await self.connection.fetchval(
            "UPDATE switchboard.wake_intents SET state = 'launched', claimed_by = NULL, "
            "claim_expires_at = NULL, updated_at = now() WHERE goal_id = $1 AND id = $2 "
            "AND state = 'claimed' AND claim_expires_at > clock_timestamp() RETURNING id",
            goal_id,
            wake.intent_id,
        ):
            raise GoalEnded("wake_not_claimed")
        grants = await self._lead_grants(goal, wake, catalog)
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
            grants=grants,
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

    async def defer(self, goal_id: UUID, invocation_id: UUID) -> bool:
        """End a launch the session host had no room for; nothing of it ran.

        The invocation ends ``launch_failed`` and its token is revoked, and the
        invocation it drew goes back to the budget (D10 charges only launches that
        ran). False when the ledger had already ended it (Stop, a lapsed lease),
        which keeps that end and its charge.
        """
        if not await self.end(goal_id, invocation_id, "failed", "launch_failed"):
            return False
        await self.connection.execute(
            "UPDATE switchboard.budgets SET used_invocations = used_invocations - 1, "
            "updated_at = now() WHERE goal_id = $1 AND used_invocations > 0",
            goal_id,
        )
        return True

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
        enters ``waiting`` on a settle envelope and reaches its final state once no effect
        can still change; ``resolve`` runs at once for a goal with nothing outstanding.
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
        epoch = await self.connection.fetchval(
            "SELECT lead_epoch FROM switchboard.invocations WHERE goal_id = $1 AND id = $2",
            principal.goal_id,
            principal.invocation_id,
        )
        if await WaitLedger(self.connection).open_settle(principal.goal_id, epoch) is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        await self.connection.execute(
            "UPDATE switchboard.goals SET status = 'waiting', updated_at = now() WHERE id = $1",
            principal.goal_id,
        )
        await self.resolve(principal.goal_id)
        return children

    async def wait_approval(
        self, principal: Principal, effect_id: UUID
    ) -> list[UUID] | BrokerRefused:
        """``goal.wait_approval``: the lead ends and the goal waits on one parked effect.

        The effect must be parked for this goal with an approval to wait on. Live children are
        stopped with the lead (returned for termination). The goal enters ``waiting`` on an
        ``approval`` envelope bound to the exact effect; ``approval_waits`` finds it settled.
        """
        return await self._wait(principal, effect_id, approval=True)

    async def wait_owner(self, principal: Principal, effect_id: UUID) -> list[UUID] | BrokerRefused:
        """``goal.wait_owner``: the lead ends and the goal waits for the owner's answer.

        The effect must be this goal's committed owner delivery that accepts input and has not
        expired. The goal enters ``waiting`` on an ``owner_answer`` envelope bound to that prompt;
        the exact reply, admitted by the owner thread binding, is the wait's one wake.
        """
        return await self._wait(principal, effect_id, approval=False)

    async def wait_until(
        self, principal: Principal, predicate: dict, deadline: datetime, max_wakes: int
    ) -> tuple[list[UUID], bool] | BrokerRefused:
        """``goal.wait_until``: the lead ends and the goal waits on its own conditions (S2.2).

        ``predicate`` is a canonical goal-own predicate (``own_predicate``). Every decision it
        names must be one of this goal's approval effects and every effect one of its effects,
        else RESOURCE; a deadline past ``HORIZON`` from admission or not in the future is POLICY.
        Nothing is recorded on a refusal. Otherwise, in this transaction, live children stop,
        the lead ends, a ``conditions`` envelope opens and its condition instance registers
        against a snapshot read under the goal lock. A predicate that already holds fires at
        registration and is admitted here as the wait's wake, exactly once. Returns the stopped
        children and whether the goal was woken.
        """
        goal = await self.lock(principal.goal_id)
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        if principal.depth != 0:
            raise BrokerRefused(Reason.CAPABILITY)
        rows = await self.connection.fetch(
            "SELECT id::text AS id, status, approval_ref IS NOT NULL AS approval "
            "FROM switchboard.effects WHERE goal_id = $1",
            principal.goal_id,
        )
        effects = [OwnEffect(r["id"], r["status"], r["approval"]) for r in rows]
        decisions, named_effects = named(predicate)
        if not decisions <= {e.id for e in effects if e.approval} or not named_effects <= {
            e.id for e in effects
        }:
            raise BrokerRefused(Reason.RESOURCE)
        bounds = await self.connection.fetchrow(
            "SELECT now() AS now, b.deadline AS budget, g.created_at + $2::interval AS horizon, "
            "g.label_version, g.label_instr, g.label_data, g.label_sens "
            "FROM switchboard.goals g JOIN switchboard.budgets b ON b.goal_id = g.id "
            "WHERE g.id = $1",
            principal.goal_id,
            HORIZON,
        )
        if bounds is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        if deadline > bounds["horizon"]:
            raise BrokerRefused(Reason.POLICY)
        expires = min(deadline, bounds["budget"])
        if expires <= bounds["now"]:
            raise BrokerRefused(Reason.POLICY)
        exhausted = await self.broker.charge_call(principal)
        if exhausted is not None:
            return exhausted
        children = await self._end_live(principal.goal_id, "stopped", "goal_ended", depth=1)
        await self.end(principal.goal_id, principal.invocation_id, "returned", "completed")
        epoch = await self.connection.fetchval(
            "SELECT lead_epoch FROM switchboard.invocations WHERE goal_id = $1 AND id = $2",
            principal.goal_id,
            principal.invocation_id,
        )
        # Generation-owned and not derived from the envelope id, which the ledger mints.
        instance = f"goal:{principal.goal_id}:conditions:{uuid4()}"
        opened = await WaitLedger(self.connection).open_conditions(
            principal.goal_id,
            epoch,
            predicate,
            predicate_digest(predicate),
            sorted(predicate_sources(predicate)),
            expires,
            max_wakes,
            instance,
        )
        if opened is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        await self.connection.execute(
            "UPDATE switchboard.goals SET status = 'waiting', updated_at = now() WHERE id = $1",
            principal.goal_id,
        )
        evaluator = ConditionEvaluator(ConnectionConditionStore(self.connection))
        try:
            registered = await evaluator.register(
                instance_id=instance,
                predicate=predicate,
                grant=grant_for(str(principal.goal_id), predicate, effects),
                goal_id=str(principal.goal_id),
                label=Label.from_row(
                    bounds["label_version"],
                    bounds["label_instr"],
                    bounds["label_data"],
                    bounds["label_sens"],
                ),
                snapshot=snapshot_for(bounds["now"], effects),
                expires_at=expires,
            )
        except RegistrationRefused:
            raise BrokerRefused(Reason.RESOURCE) from None
        except ConditionsUnavailable:
            raise BrokerRefused(Reason.UNAVAILABLE) from None
        woken = False
        if registered.intent is not None:
            wake = await WakeAdmission(self.connection).admit(registered.intent)
            woken = wake.state == "admitted"
        return children, woken

    async def _wait(
        self, principal: Principal, effect_id: UUID, *, approval: bool
    ) -> list[UUID] | BrokerRefused:
        goal = await self.lock(principal.goal_id)
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        if principal.depth != 0:
            raise BrokerRefused(Reason.CAPABILITY)
        if approval:
            digest = await self.connection.fetchval(
                "SELECT args_digest FROM switchboard.effects WHERE goal_id = $1 AND id = $2 "
                "AND status = 'parked' AND approval_ref IS NOT NULL",
                principal.goal_id,
                effect_id,
            )
            expires = None
        else:
            digest = None
            expires = await self.connection.fetchval(
                "SELECT prompt_expires_at FROM switchboard.effects WHERE goal_id = $1 AND id = $2 "
                "AND status = 'committed' AND prompt_accepts_input "
                "AND prompt_expires_at > now()",
                principal.goal_id,
                effect_id,
            )
            if expires is not None and not await self._owner_delivery(principal.goal_id, effect_id):
                expires = None
        if digest is None and expires is None:
            raise BrokerRefused(Reason.RESOURCE)
        exhausted = await self.broker.charge_call(principal)
        if exhausted is not None:
            return exhausted
        children = await self._end_live(principal.goal_id, "stopped", "goal_ended", depth=1)
        await self.end(principal.goal_id, principal.invocation_id, "returned", "completed")
        epoch = await self.connection.fetchval(
            "SELECT lead_epoch FROM switchboard.invocations WHERE goal_id = $1 AND id = $2",
            principal.goal_id,
            principal.invocation_id,
        )
        waits = WaitLedger(self.connection)
        if digest is not None:
            opened = await waits.open_approval(principal.goal_id, epoch, effect_id, digest)
        else:
            opened = await waits.open_owner_answer(principal.goal_id, epoch, effect_id, expires)
        if opened is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        await self.connection.execute(
            "UPDATE switchboard.goals SET status = 'waiting', updated_at = now() WHERE id = $1",
            principal.goal_id,
        )
        return children

    async def plan(
        self,
        principal: Principal,
        premise: tuple[str, ...],
        evidence: tuple[str, ...],
        steps: tuple[str, ...],
    ) -> int | BrokerRefused:
        """``goal.plan``: record the lead's plan as the goal's next version (RFC 0038 S2).

        Only a running goal's lead plans. Every reference must name a row this goal owns, else
        RESOURCE and nothing is recorded. Identical content keeps the current version (a retry);
        changed content appends the next version with its predecessor, so the version an open
        or fired wait bound stays as it was approved. The version records the lead's epoch.
        """
        goal = await self.lock(principal.goal_id)
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)
        if principal.depth != 0:
            raise BrokerRefused(Reason.CAPABILITY)
        named: dict[str, set[str]] = {}
        for ref in (*premise, *evidence, *steps):
            kind, _, ident = ref.partition(":")
            named.setdefault(kind, set()).add(ident)
        for kind, idents in named.items():
            if kind == "plan":
                found = await self.connection.fetchval(
                    "SELECT count(*) FROM switchboard.goal_plans "
                    "WHERE goal_id = $1 AND version = ANY($2::bigint[])",
                    principal.goal_id,
                    sorted(int(ident) for ident in idents),
                )
            else:
                rows = await self.connection.fetch(
                    _PLAN_REF_ROWS[kind], principal.goal_id, [UUID(i) for i in idents]
                )
                found = len(rows)
            if found != len(idents):
                raise BrokerRefused(Reason.RESOURCE)
        exhausted = await self.broker.charge_call(principal)
        if exhausted is not None:
            return exhausted
        epoch = await self.connection.fetchval(
            "SELECT lead_epoch FROM switchboard.invocations WHERE goal_id = $1 AND id = $2",
            principal.goal_id,
            principal.invocation_id,
        )
        version = await WaitLedger(self.connection).append_plan(
            principal.goal_id, epoch, premise, evidence, steps
        )
        if version is None:
            raise BrokerRefused(Reason.GOAL)
        return version

    async def approval_waits(self) -> list[UUID]:
        """Waiting goals whose open approval envelope is bound to a parked effect (to sync)."""
        rows = await self.connection.fetch(
            "SELECT g.id FROM switchboard.goals g WHERE g.status = 'waiting' AND EXISTS "
            "(SELECT 1 FROM switchboard.wait_envelopes e WHERE e.goal_id = g.id "
            "AND e.kind = 'approval' AND e.state = 'open') ORDER BY g.id"
        )
        return [row["id"] for row in rows]

    async def decided_approvals(self, goal_id: UUID) -> tuple[UUID, UUID, str, str | None] | None:
        """``(envelope_id, effect_id, status, instance_id)`` once the waited-on effect settled.

        Settled means past ``requested``, ``parked``, ``approved`` and ``dispatched``: the
        approval ran to a terminal effect state, or the domain ended it. ``instance_id`` is the
        envelope's current-generation condition instance id, the one name its wake is keyed on:
        ``approval:<effect>`` for a wait opened by ``goal.wait_approval`` and
        ``legacy:awaiting_approval:<goal>`` for one ``sw_064`` converted.
        """
        row = await self.connection.fetchrow(
            "SELECT e.id AS envelope_id, f.id AS effect_id, f.status, "
            "g.condition_instance_ids[1] AS instance_id FROM "
            "switchboard.wait_envelopes e JOIN switchboard.effects f "
            "ON (f.goal_id, f.id) = (e.goal_id, e.approval_effect_id) "
            "LEFT JOIN switchboard.wait_generations g ON (g.goal_id, g.envelope_id, g.generation) "
            "= (e.goal_id, e.id, e.generation) "
            "WHERE e.goal_id = $1 AND e.kind = 'approval' AND e.state = 'open' "
            "AND f.status <> ALL($2::text[])",
            goal_id,
            list(_UNSETTLED),
        )
        if row is None:
            return None
        return row["envelope_id"], row["effect_id"], row["status"], row["instance_id"]

    async def expired_waits(self) -> list[UUID]:
        """Waiting goals whose open envelope passed its deadline without firing (S2.2)."""
        rows = await self.connection.fetch(
            "SELECT g.id FROM switchboard.goals g WHERE g.status = 'waiting' AND EXISTS "
            "(SELECT 1 FROM switchboard.wait_envelopes e WHERE e.goal_id = g.id "
            "AND e.state = 'open' AND e.deadline <= now()) ORDER BY g.id"
        )
        return [row["id"] for row in rows]

    async def expire_wait(self, goal_id: UUID) -> str | None:
        """End a waiting goal whose envelope expired: ``failed``, or ``partial`` after an outcome.

        The envelope closes ``expired`` and its undelivered wake intents cancel ``expired``. A
        settle wait expiring leaves its recorded outcome resolved ``partial``; every other kind
        (conditions, owner answer, approval, a held legacy approval) never produced one, so the
        goal ``failed``. Returns the final status, or None when nothing had expired.
        """
        goal = await self.lock(goal_id)
        if goal is None or goal["status"] != "waiting":
            return None
        expired = await self.connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM switchboard.wait_envelopes WHERE goal_id = $1 "
            "AND state = 'open' AND deadline <= now())",
            goal_id,
        )
        if not expired:
            return None
        await WaitLedger(self.connection).cancel_waits(goal_id, "expired")
        resolved = await self.connection.fetchval(
            "UPDATE switchboard.goal_outcomes SET final_status = 'partial', "
            "resolved_at = clock_timestamp() WHERE goal_id = $1 AND final_status IS NULL "
            "RETURNING true",
            goal_id,
        )
        final = "partial" if resolved else "failed"
        await self.connection.execute(
            "UPDATE switchboard.goals SET status = $2, updated_at = now() WHERE id = $1",
            goal_id,
            final,
        )
        return final

    async def end_approval_wait(self, goal_id: UUID, envelope_id: UUID, final: str) -> bool:
        """Close a wait that will launch nothing and end the goal ``partial`` or ``failed``.

        A decline, a domain expiry or a cancel ends ``partial``; a wake refused because the
        envelope's deadline passed ends ``failed``. An envelope already closed stays as it is.
        """
        if final not in ("partial", "failed"):
            raise ValueError("an approval wait ends partial or failed")
        goal = await self.lock(goal_id)
        if goal is None or goal["status"] != "waiting":
            return False
        await WaitLedger(self.connection).close_envelope(goal_id, envelope_id, "cancelled")
        await self.connection.execute(
            "UPDATE switchboard.goals SET status = $2, updated_at = now() WHERE id = $1",
            goal_id,
            final,
        )
        return True

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
        """waiting (settle) -> completed | partial | failed once no effect can change (D11).

        A goal admitted from an owner message completes only if its reply is an
        owner delivery that committed; otherwise it ends ``partial`` with the
        delivery failure recorded.
        """
        goal = await self.lock(goal_id)
        if goal is None or goal["status"] != "waiting":
            return None
        if await self.connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM switchboard.effects WHERE goal_id = $1 "
            "AND status = ANY($2::text[]))",
            goal_id,
            list(_UNSETTLED),
        ):
            return None
        # The settle fire: one wake, resolved here with no LLM epoch (RFC 0038 S2.2).
        if not await WaitLedger(self.connection).fire_settle(goal_id):
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
        await self._cancel_conditions(goal_id)
        await WaitLedger(self.connection).cancel_waits(goal_id, "stopped")
        live = await self._end_live(goal_id, "stopped", "stopped")
        await self.connection.execute(
            "UPDATE switchboard.invocations SET token_hash = NULL, updated_at = now() "
            "WHERE goal_id = $1 AND token_hash IS NOT NULL",
            goal_id,
        )
        return True, live

    async def _cancel_conditions(self, goal_id: UUID) -> None:
        """Stop cancels the condition instances of the goal's live waits, in Stop's transaction.

        Nothing is left for the evaluator to fire. A generation id with no stored instance of
        this goal (one admitted without the evaluator) is skipped. If the store cannot cancel, Stop
        still commits: a later fire is refused ``stopped`` by wake admission.
        """
        instances = await self.connection.fetch(
            "SELECT unnest(g.condition_instance_ids) AS id FROM switchboard.wait_envelopes e "
            "JOIN switchboard.wait_generations g "
            "ON (g.goal_id, g.envelope_id, g.generation) = (e.goal_id, e.id, e.generation) "
            "WHERE e.goal_id = $1 AND e.kind = 'conditions' AND e.state IN ('open', 'fired')",
            goal_id,
        )
        if not instances:
            return
        now = await self.connection.fetchval("SELECT now()")
        store = ConnectionConditionStore(self.connection)
        evaluator = ConditionEvaluator(store)
        for row in instances:
            try:
                async with store.transaction() as tx:
                    stored = await tx.get_instance(row["id"])
                # Instance ids are global; only this goal's own instance is its to cancel.
                if stored is None or stored.goal_id != str(goal_id):
                    continue
                await evaluator.cancel(row["id"], now=now)
            except (ConditionsUnavailable, ConnectionError, StoreConflict):
                logger.warning("goal stop could not cancel condition goal=%s", goal_id)

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
        recover = row["depth"] == 0 and goal["status"] == "running"
        if recover:
            # The recovery intent is this ended lead with no successor (see ``leaderless``).
            await self.connection.execute(
                "UPDATE switchboard.goals SET status = 'admitted', updated_at = now() "
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
                "AND g.status IN ('admitted', 'running') "
                "AND NOT EXISTS (SELECT 1 FROM switchboard.invocations i "
                "WHERE i.goal_id = g.id AND i.depth = 0 AND i.status = ANY($2::text[])))",
                goal_id,
                list(LIVE),
            )
        )

    async def wakes_due(self) -> list[tuple[UUID, UUID]]:
        """Wake intents ready to launch: new, failed before an invocation, or claim lapsed."""
        rows = await self.connection.fetch(
            "SELECT w.goal_id, w.id FROM switchboard.wake_intents w "
            "JOIN switchboard.goals g ON g.id = w.goal_id JOIN switchboard.wait_envelopes e "
            "ON (e.goal_id, e.id) = (w.goal_id, w.envelope_id) "
            "WHERE g.status = 'admitted' AND w.epoch IS NOT NULL AND e.deadline > now() "
            "AND (w.state IN ('pending', 'failed') OR "
            "(w.state = 'claimed' AND w.claim_expires_at <= clock_timestamp())) "
            "ORDER BY w.created_at, w.id"
        )
        return [(row["goal_id"], row["id"]) for row in rows]

    async def claim_wake(
        self, goal_id: UUID, intent_id: UUID, worker: str, lease_seconds: float
    ) -> WakeClaim | None:
        """Claim one due intent for ``worker`` under the goal lock; None if not claimable."""
        goal = await self.lock(goal_id)
        if goal is None or goal["status"] != "admitted":
            return None
        await self.connection.execute(
            # A lapsed claim returns to pending first: the intent never skips its state machine.
            "UPDATE switchboard.wake_intents SET state = 'pending', claimed_by = NULL, "
            "claim_expires_at = NULL, updated_at = now() WHERE goal_id = $1 AND id = $2 "
            "AND state = 'claimed' AND claim_expires_at <= clock_timestamp()",
            goal_id,
            intent_id,
        )
        epoch = await self.connection.fetchval(
            "UPDATE switchboard.wake_intents SET state = 'claimed', claimed_by = $3, "
            "claim_expires_at = clock_timestamp() + make_interval(secs => $4::float8), "
            "attempts = attempts + 1, updated_at = now() WHERE goal_id = $1 AND id = $2 "
            "AND state IN ('pending', 'failed') AND epoch IS NOT NULL RETURNING epoch",
            goal_id,
            intent_id,
            worker,
            lease_seconds,
        )
        return None if epoch is None else WakeClaim(goal_id, intent_id, epoch)

    async def fail_wake(self, goal_id: UUID, intent_id: UUID) -> None:
        """A launch that opened no invocation: the same intent is tried again by the sweep."""
        await self.lock(goal_id)
        await self.connection.execute(
            "UPDATE switchboard.wake_intents SET state = 'failed', claimed_by = NULL, "
            "claim_expires_at = NULL, updated_at = now() WHERE goal_id = $1 AND id = $2 "
            "AND state = 'claimed'",
            goal_id,
            intent_id,
        )

    async def lapse_wakes(self) -> int:
        """Cancel undelivered wake intents whose envelope deadline passed (S2.2 expiry).

        A claim whose lease lapsed is undelivered too: its runner died, and a goal must not sit
        admitted behind an intent nobody can launch.
        """
        rows = await self.connection.fetch(
            "UPDATE switchboard.wake_intents w SET state = 'cancelled', "
            "cancelled_reason = 'expired', claimed_by = NULL, claim_expires_at = NULL, "
            "updated_at = now() FROM switchboard.wait_envelopes e "
            "WHERE (e.goal_id, e.id) = (w.goal_id, w.envelope_id) AND e.deadline <= now() "
            "AND (w.state IN ('pending', 'failed') OR "
            "(w.state = 'claimed' AND w.claim_expires_at <= clock_timestamp())) RETURNING w.id"
        )
        return len(rows)

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

    async def unlaunched(self, grace_seconds: float) -> list[UUID]:
        """Admitted goals no lead was ever opened for, admitted over ``grace_seconds`` ago.

        Admission commits before the runner starts the goal, so a process that
        dies between the two leaves the goal admitted with no invocation; the
        start right after admission opens well inside the grace (bu-dhloi1.206).
        """
        rows = await self.connection.fetch(
            "SELECT g.id FROM switchboard.goals g WHERE g.status = 'admitted' "
            "AND g.created_at <= clock_timestamp() - make_interval(secs => $1::float8) "
            "AND NOT EXISTS (SELECT 1 FROM switchboard.invocations i "
            "WHERE i.goal_id = g.id AND i.depth = 0) ORDER BY g.id",
            grace_seconds,
        )
        return [row["id"] for row in rows]

    async def leaderless(self, grace_seconds: float) -> list[Leaderless]:
        """Running goals, or admitted ones recovering, whose latest lead ended long enough ago.

        A runner that dies between ending a lead (lease expiry, escalation, a
        runtime failure) and launching its successor leaves the goal here; an
        in-process successor opens well inside the grace.
        """
        rows = await self.connection.fetch(
            "SELECT g.id AS goal_id, l.id, l.end_reason, l.model_role, l.model_tier, "
            "l.relaunch_of, d.deferred_seconds, d.relaunch_spent FROM switchboard.goals g "
            "JOIN LATERAL (SELECT * FROM switchboard.invocations i WHERE i.goal_id = g.id "
            "AND i.depth = 0 ORDER BY i.lead_epoch DESC, i.created_at DESC LIMIT 1) l ON true "
            # The unbroken run of leads the host had no room for, ending at the latest.
            "LEFT JOIN LATERAL (SELECT "
            "extract(epoch FROM clock_timestamp() - min(b.created_at))::float8 "
            "AS deferred_seconds, bool_or(b.relaunch_of IS NOT NULL) AS relaunch_spent "
            "FROM switchboard.invocations b WHERE b.goal_id = g.id AND b.depth = 0 "
            "AND b.end_reason = 'launch_failed' AND b.lead_epoch > COALESCE((SELECT "
            "max(n.lead_epoch) FROM switchboard.invocations n WHERE n.goal_id = g.id "
            "AND n.depth = 0 AND n.end_reason IS DISTINCT FROM 'launch_failed'), -1)) d "
            "ON l.end_reason = 'launch_failed' "
            "WHERE g.status IN ('running', 'admitted') "
            # A fired wait's lead is the wake's to launch, not recovery's to replace.
            "AND NOT EXISTS (SELECT 1 FROM switchboard.wake_intents w WHERE w.goal_id = g.id "
            "AND w.state IN ('pending', 'claimed', 'failed')) "
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
                row["deferred_seconds"],
                bool(row["relaunch_spent"]),
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
        """Waiting goals on an open settle envelope: the runner resolves them as effects settle."""
        return await WaitLedger(self.connection).open_settle_goals()

    async def summary(self, goal_id: UUID, lead_id: UUID) -> LedgerSummary | None:
        """What an earlier lead left for lead ``lead_id``, identifiers only (D14).

        ``None`` when no earlier lead of the goal ran (each ended ``launch_failed``:
        the host had no room for it), so the lead starts as a first lead. Pending
        inbox replies are stated by id and count, never consumed: the lead reads
        them through ``goal.inbox``, which joins their labels.
        """
        ran = await self.connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM switchboard.invocations WHERE goal_id = $1 "
            "AND depth = 0 AND id <> $2 AND end_reason IS DISTINCT FROM 'launch_failed')",
            goal_id,
            lead_id,
        )
        if not ran:
            return None
        inbox = await self.broker.pending_inbox(goal_id, INBOX_LISTED)
        pending = await self.broker.pending_inbox_count(goal_id) if inbox else 0
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
        plan = await self.connection.fetchrow(
            "SELECT p.version, p.premise_refs::text AS premise, p.evidence_refs::text AS evidence, "
            "p.step_refs::text AS steps FROM switchboard.goals g JOIN switchboard.goal_plans p "
            "ON (p.goal_id, p.version) = (g.id, g.plan_version) WHERE g.id = $1",
            goal_id,
        )
        return LedgerSummary(
            tuple((str(e["id"]), e["capability"], e["status"]) for e in effects),
            tuple((str(c["id"]), c["status"]) for c in children),
            tuple(str(row["id"]) for row in inbox),
            pending,
            None
            if plan is None
            else PlanSummary(
                plan["version"],
                tuple(json.loads(plan["premise"])),
                tuple(json.loads(plan["evidence"])),
                tuple(json.loads(plan["steps"])),
            ),
        )

    async def launch_facts(self, goal_id: UUID, invocation_id: UUID) -> LaunchFacts:
        """The invocation's grant and share and the goal's remaining budget, for its packet.

        Read from the ledger rows the broker enforces, so the packet states exactly what a
        call will be checked against. A child's fixed result schema comes with them (D9).
        """
        row = await self.connection.fetchrow(
            "SELECT i.grant_snapshot, i.depth, i.max_calls, i.used_calls, i.max_turns, "
            "i.max_tokens, "
            "ceil(extract(epoch FROM i.deadline - clock_timestamp()))::bigint AS seconds_left, "
            "r.result_schema::text AS schema_text, "
            "b.max_calls AS goal_max_calls, b.used_calls AS goal_used_calls, "
            "b.max_tokens AS goal_max_tokens, b.used_tokens AS goal_used_tokens, "
            "ceil(extract(epoch FROM b.deadline - clock_timestamp()))::bigint AS goal_seconds "
            "FROM switchboard.invocations i "
            "JOIN switchboard.budgets b ON b.goal_id = i.goal_id "
            "LEFT JOIN switchboard.goal_child_results r "
            "ON (r.goal_id, r.invocation_id) = (i.goal_id, i.id) "
            "WHERE i.goal_id = $1 AND i.id = $2",
            goal_id,
            invocation_id,
        )
        if row is None:
            raise GoalEnded("invocation_not_found")
        schema = None if row["schema_text"] is None else json.loads(row["schema_text"])
        return LaunchFacts(
            decode_grants(row["grant_snapshot"]),
            InvocationShare(
                row["max_calls"],
                row["used_calls"],
                row["max_turns"],
                row["max_tokens"],
                row["seconds_left"],
            ),
            GoalBudgetView(
                row["goal_max_calls"],
                row["goal_used_calls"],
                row["goal_max_tokens"],
                row["goal_used_tokens"],
                row["goal_seconds"],
            ),
            schema,
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

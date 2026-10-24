"""Central Stop repository, not an authenticated owner endpoint or a worker.

Every operation has one goal-first local transaction and a total acquire/commit
bound. Local revocation never proves an owning-domain row was cancelled. The
future authenticated owning-receipt consumer must establish that authority before
calling ack_cancel_work; these value objects do not authenticate their caller.
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from butlers.core.goal_ledger import GoalLedger

_TABLES = ("goal_park_associations", "goal_stop_requests", "goal_approval_cancel_work")
_STOPPABLE = {"admitted", "running", "awaiting_approval", "recovering", "settling", "stopped"}
_ELIGIBLE = {"requested", "parked", "approved", "unknown"}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_DOMAIN = re.compile(r"[a-zA-Z0-9_.-]+\Z")


class StopUnavailable(RuntimeError):
    """Closed refusal; exception strings contain no private runtime inputs."""


def _uuid(value):
    # asyncpg decodes PostgreSQL UUIDs as a UUID subclass. Require the typed
    # value, without coercing strings/bytes or caller-supplied wire objects.
    if not isinstance(value, UUID):
        raise ValueError("invalid_stop_identity")


def _digest(value):
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise ValueError("invalid_stop_digest")


@dataclass(frozen=True)
class LocalStop:
    request_id: UUID
    goal_id: UUID
    revoked_tokens: int
    cancellation_work: int

    @property
    def owning_cancellation_pending(self) -> bool:
        return self.cancellation_work > 0


@dataclass(frozen=True)
class CancelIdentity:
    goal_id: UUID
    effect_id: UUID
    owning_domain: str
    action_id: UUID
    capability: str
    args_digest: str

    def __post_init__(self):
        for value in (self.goal_id, self.effect_id, self.action_id):
            _uuid(value)
        _digest(self.args_digest)
        if (
            self.action_id != self.effect_id
            or type(self.owning_domain) is not str
            or not _DOMAIN.fullmatch(self.owning_domain)
            or "__" in self.owning_domain
            or type(self.capability) is not str
            or not self.capability.startswith(self.owning_domain + "__")
            or not _DOMAIN.fullmatch(self.capability[len(self.owning_domain) + 2 :])
            or "__" in self.capability[len(self.owning_domain) + 2 :]
        ):
            raise ValueError("invalid_cancel_binding")


@dataclass(frozen=True)
class CancelClaim:
    identity: CancelIdentity
    claim_id: UUID
    generation: int

    def __post_init__(self):
        if type(self.identity) is not CancelIdentity:
            raise ValueError("invalid_cancel_binding")
        _uuid(self.claim_id)
        if type(self.generation) is not int or self.generation <= 0:
            raise ValueError("invalid_cancel_generation")


# The select is shared by claim and ACK. It derives call identity from the
# immutable effect, not an alleged owning response or mutable caller arguments.
_WORK = """
    SELECT w.*, a.owning_domain,a.action_id,a.dispatch_intent_id,
           e.capability,e.args_digest,e.status AS effect_status,
           w.lease_expires_at>clock_timestamp() AS lease_valid
    FROM switchboard.goal_approval_cancel_work w
    JOIN switchboard.goal_park_associations a USING(goal_id,effect_id)
    JOIN switchboard.effects e ON e.goal_id=w.goal_id AND e.id=w.effect_id
    WHERE w.goal_id=$1 AND w.effect_id=$2 FOR UPDATE OF w,a,e
"""


def _identity(row):
    return CancelIdentity(
        row["goal_id"],
        row["effect_id"],
        row["owning_domain"],
        row["action_id"],
        row["capability"],
        row["args_digest"],
    )


class GoalStop:
    def __init__(self, pool, *, timeout: float = 5):
        if type(timeout) not in (int, float) or not 0 < timeout <= 5:
            raise ValueError("finite_stop_bound_required")
        self.pool, self.timeout = pool, timeout

    @asynccontextmanager
    async def _transaction(self):
        deadline = time.monotonic() + self.timeout
        async with asyncio.timeout(self.timeout):
            async with self.pool.acquire() as connection, connection.transaction():
                milliseconds = max(1, int((deadline - time.monotonic()) * 1000))
                await connection.execute(
                    "SELECT set_config('lock_timeout',$1,true),"
                    "set_config('statement_timeout',$1,true)",
                    f"{milliseconds}ms",
                )
                await GoalLedger(connection).verify_role()
                schema = await connection.fetch(
                    """
                    SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
                           pg_has_role(current_user,c.relowner,'MEMBER') AS owner_member,
                           count(p.oid) AS policies,
                           bool_and(COALESCE(p.polpermissive AND p.polroles=ARRAY[
                               (SELECT oid FROM pg_roles WHERE rolname='butler_switchboard_rw')]
                               AND p.polname='goal_stop_' || CASE p.polcmd
                                   WHEN 'r' THEN 'select' WHEN 'a' THEN 'insert'
                                   WHEN 'w' THEN 'update' WHEN 'd' THEN 'delete' END
                               AND CASE WHEN p.polcmd='a' THEN p.polqual IS NULL ELSE
                                   lower(regexp_replace(pg_get_expr(p.polqual,p.polrelid),
                                       '[()[:space:]]|::(name|text)','','g')) =
                                   'current_user=''butler_switchboard_rw''' || CASE
                                       WHEN p.polcmd='d' OR
                                           (c.relname='goal_stop_requests' AND p.polcmd='w')
                                       THEN 'andfalse' ELSE '' END END
                               AND CASE WHEN p.polcmd IN ('a','w') THEN
                                   lower(regexp_replace(pg_get_expr(p.polwithcheck,p.polrelid),
                                       '[()[:space:]]|::(name|text)','','g')) =
                                   'current_user=''butler_switchboard_rw''' || CASE
                                       WHEN c.relname='goal_stop_requests' AND p.polcmd='w'
                                       THEN 'andfalse' ELSE '' END
                                   ELSE p.polwithcheck IS NULL END,false)) AS policy_valid
                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    LEFT JOIN pg_policy p ON p.polrelid=c.oid
                    WHERE n.nspname='switchboard' AND c.relname=ANY($1::text[]) AND c.relkind='r'
                    GROUP BY c.oid,c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relowner
                """,
                    list(_TABLES),
                )
                if {r["relname"] for r in schema} != set(_TABLES) or any(
                    not r["relrowsecurity"]
                    or r["relforcerowsecurity"]
                    or r["owner_member"]
                    or r["policies"] != 4
                    or r["policy_valid"] is not True
                    for r in schema
                ):
                    raise StopUnavailable("stop_schema_unavailable")
                yield connection

    @staticmethod
    async def _goal(connection, goal_id):
        row = await connection.fetchrow(
            "SELECT id,status FROM switchboard.goals WHERE id=$1 FOR UPDATE OF goals", goal_id
        )
        if not row or row["status"] not in _STOPPABLE:
            raise StopUnavailable("goal_not_stoppable")
        return row

    async def stop_goal(self, goal_id: UUID, request_id: UUID, provenance_digest: str) -> LocalStop:
        """Revoke locally and enqueue durable lookup/cancel work; no network I/O."""
        _uuid(goal_id)
        _uuid(request_id)
        _digest(provenance_digest)
        async with self._transaction() as connection:
            await self._goal(connection, goal_id)
            existing = await connection.fetchrow(
                "SELECT * FROM switchboard.goal_stop_requests WHERE request_id=$1", request_id
            )
            if existing:
                if (
                    existing["goal_id"] != goal_id
                    or existing["provenance_digest"] != provenance_digest
                ):
                    raise StopUnavailable("stop_request_conflict")
                return LocalStop(
                    request_id, goal_id, existing["revoked_tokens"], existing["cancellation_work"]
                )
            invocations = await connection.fetch(
                "SELECT id,token_hash FROM switchboard.invocations WHERE goal_id=$1 "
                "ORDER BY id FOR UPDATE",
                goal_id,
            )
            revoked = sum(row["token_hash"] is not None for row in invocations)
            await connection.execute(
                "UPDATE switchboard.invocations SET token_hash=NULL, "
                "status=CASE WHEN status IN ('launching','running') THEN 'stopped' ELSE status END,"
                "updated_at=now() WHERE goal_id=$1",
                goal_id,
            )
            await connection.execute(
                "UPDATE switchboard.goals SET status='stopped',updated_at=now() WHERE id=$1",
                goal_id,
            )
            await connection.fetch(
                """
                INSERT INTO switchboard.goal_approval_cancel_work(effect_id,goal_id,request_id)
                SELECT a.effect_id,a.goal_id,$2 FROM switchboard.goal_park_associations a
                JOIN switchboard.effects e ON (e.goal_id,e.id)=(a.goal_id,a.effect_id)
                WHERE a.goal_id=$1 AND a.dispatch_intent_id IS NULL
                    AND e.status IN ('requested','parked','approved','unknown')
                ON CONFLICT(effect_id) DO NOTHING RETURNING effect_id
            """,
                goal_id,
                request_id,
            )
            # A second Stop request must not mistake zero newly-inserted
            # rows for completion of an earlier request's owning cancellation.
            pending = await connection.fetchval(
                "SELECT count(*) FROM switchboard.goal_approval_cancel_work "
                "WHERE goal_id=$1 AND state<>'acknowledged'",
                goal_id,
            )
            await connection.execute(
                "INSERT INTO switchboard.goal_stop_requests "
                "(request_id,goal_id,provenance_digest,revoked_tokens,cancellation_work) "
                "VALUES($1,$2,$3,$4,$5)",
                request_id,
                goal_id,
                provenance_digest,
                revoked,
                pending,
            )
            return LocalStop(request_id, goal_id, revoked, pending)

    async def claim_cancel_work(self, identity: CancelIdentity, *, lease_seconds: int = 30):
        """Claim one exact effect. Discovery/worker/transport are intentionally absent."""
        if (
            type(identity) is not CancelIdentity
            or type(lease_seconds) is not int
            or not 0 < lease_seconds <= 60
        ):
            raise ValueError("invalid_cancel_claim")
        async with self._transaction() as connection:
            goal = await self._goal(connection, identity.goal_id)
            row = await connection.fetchrow(_WORK, identity.goal_id, identity.effect_id)
            if (
                goal["status"] != "stopped"
                or not row
                or _identity(row) != identity
                or row["dispatch_intent_id"] is not None
                or row["effect_status"] not in _ELIGIBLE
            ):
                raise StopUnavailable("cancel_binding_unavailable")
            if row["state"] == "acknowledged" or (row["state"] == "claimed" and row["lease_valid"]):
                return None
            claim_id = uuid4()
            claimed = await connection.fetchrow(
                """
                UPDATE switchboard.goal_approval_cancel_work
                SET state='claimed',generation=generation+1,claim_id=$3,
                    lease_expires_at=clock_timestamp()+make_interval(secs=>$4),updated_at=now()
                WHERE goal_id=$1 AND effect_id=$2 AND generation=$5
                    AND (state='pending' OR
                         (state='claimed' AND lease_expires_at<=clock_timestamp()))
                RETURNING generation
            """,
                identity.goal_id,
                identity.effect_id,
                claim_id,
                lease_seconds,
                row["generation"],
            )
            if not claimed:
                raise StopUnavailable("cancel_claim_conflict")
            return CancelClaim(identity, claim_id, claimed["generation"])

    async def cancel_candidates(
        self, *, limit: int = 4, domains: tuple[str, ...] | None = None
    ) -> tuple[CancelIdentity, ...]:
        """Read bounded stale-able work hints; only claim_cancel_work authorizes work.

        No row locks or mutations precede the later goal-first claim transaction.
        Discovery releases its connection before any owning-domain request.
        A fixed transport owner may exclude unconfigured domains before LIMIT;
        this queue filter grants no claim, cancellation or caller authority.
        """
        if type(limit) is not int or not 1 <= limit <= 4:
            raise ValueError("invalid_cancel_limit")
        if domains is not None and (
            type(domains) is not tuple
            or len(domains) > 32
            or any(type(d) is not str or not _DOMAIN.fullmatch(d) or "__" in d for d in domains)
            or len(set(domains)) != len(domains)
        ):
            raise ValueError("invalid_cancel_domains")
        domain_filter = "" if domains is None else "AND a.owning_domain=ANY($2::text[]) "
        parameters = (limit,) if domains is None else (limit, list(domains))
        async with self._transaction() as connection:
            rows = await connection.fetch(
                "SELECT w.goal_id,w.effect_id,a.owning_domain,a.action_id,"
                "e.capability,e.args_digest FROM switchboard.goal_approval_cancel_work w "
                "JOIN switchboard.goal_park_associations a USING(goal_id,effect_id) "
                "JOIN switchboard.effects e ON (e.goal_id,e.id)=(w.goal_id,w.effect_id) "
                "JOIN switchboard.goals g ON g.id=w.goal_id "
                "WHERE g.status='stopped' AND a.dispatch_intent_id IS NULL "
                "AND e.status IN ('requested','parked','approved','unknown') "
                "AND (w.state='pending' OR "
                "(w.state='claimed' AND w.lease_expires_at<=clock_timestamp())) "
                + domain_filter
                + "ORDER BY w.updated_at,w.effect_id LIMIT $1",
                *parameters,
            )
            return tuple(_identity(row) for row in rows)

    async def ack_cancel_work(self, claim: CancelClaim, receipt_digest: str, outcome: str) -> None:
        """Persist an already-authenticated owning cancel/fence result, never no-row alone."""
        if type(claim) is not CancelClaim or outcome not in {"cancelled", "fenced_absent"}:
            raise ValueError("invalid_cancel_ack")
        _digest(receipt_digest)
        identity = claim.identity
        async with self._transaction() as connection:
            goal = await self._goal(connection, identity.goal_id)
            row = await connection.fetchrow(_WORK, identity.goal_id, identity.effect_id)
            if (
                goal["status"] != "stopped"
                or not row
                or _identity(row) != identity
                or row["dispatch_intent_id"] is not None
                or row["claim_id"] != claim.claim_id
                or row["generation"] != claim.generation
            ):
                raise StopUnavailable("cancel_ack_conflict")
            if row["state"] == "acknowledged":
                if row["receipt_digest"] != receipt_digest or row["outcome"] != outcome:
                    raise StopUnavailable("cancel_ack_conflict")
                return
            if (
                row["state"] != "claimed"
                or not row["lease_valid"]
                or row["effect_status"] not in _ELIGIBLE
            ):
                raise StopUnavailable("cancel_ack_expired")
            updated = await connection.fetchrow(
                """
                UPDATE switchboard.goal_approval_cancel_work
                SET state='acknowledged',receipt_digest=$5,outcome=$6,updated_at=now()
                WHERE goal_id=$1 AND effect_id=$2 AND claim_id=$3 AND generation=$4
                    AND state='claimed' AND lease_expires_at>clock_timestamp() RETURNING effect_id
            """,
                identity.goal_id,
                identity.effect_id,
                claim.claim_id,
                claim.generation,
                receipt_digest,
                outcome,
            )
            if not updated:
                raise StopUnavailable("cancel_ack_expired")
            await connection.execute(
                "UPDATE switchboard.effects SET status='cancelled',updated_at=now() "
                "WHERE id=$1 AND goal_id=$2",
                identity.effect_id,
                identity.goal_id,
            )

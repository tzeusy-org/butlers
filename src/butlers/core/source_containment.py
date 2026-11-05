"""Owner containment of one admission source (RFC 0038 D13 Contain, M7 bu-4c7nvp.1.2).

Containing a source partition is an owner-only action that requires the
dashboard step-up of bu-dhloi1.5: a fresh passkey assertion bound to this
exact action, consumed atomically with the write in the owning transaction.
A missing, refused or unavailable step-up writes nothing and returns the same
open-dashboard handoff as rule creation.

The record keeps evidence: it names the goals that already descend from the
source (admitted or joined from it, and goals caused by those, transitively),
and changes none of them. Every later envelope from the partition is admitted
record-only (``recorded_without_goal``): it keeps its durable decision and
creates no goal, inbox row or wake. Containment, admission and replay take the
same per-partition transaction lock, so an envelope either committed before
the containment (and is one of its descendants) or sees it.

There is no release here, so a record-only decision is never replayed into a
goal. Nothing constructs a caller until the runtime switch; the trusted
step-up interface admits only the rule kinds until its installer gains
``contain`` (bu-dhloi1.29), so today every call returns the handoff.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.core.dashboard_stepup import dashboard_stepup_proof
from butlers.core.goal_admission import digest
from butlers.core.goal_ledger import GoalLedger

KIND = "contain"
STEP_UP_REQUIRED = {"error": "dashboard_step_up_required", "open_dashboard": "/approvals"}


class _Refused(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Containment:
    id: UUID
    source_partition: str
    descendant_goals: tuple[UUID, ...]
    contained_at: datetime


def lock_key(source_partition: str) -> str:
    """The advisory lock admission and containment share for one partition."""
    return "contain:" + source_partition


async def contained(connection: Any, source_partition: str) -> bool:
    """Whether a partition is contained; a ledger before sw_058 contains nothing."""
    if await connection.fetchval("SELECT to_regclass('switchboard.source_containments')") is None:
        return False
    return bool(
        await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM switchboard.source_containments "
            "WHERE source_partition = $1)",
            source_partition,
        )
    )


async def _consume(connection: Any, phase: str, binding: dict[str, str], result=None) -> dict:
    proof = dashboard_stepup_proof.get()
    if proof is None:
        raise _Refused
    # No SQL span may export the nonce or session digest.
    with suppress_instrumentation():
        raw = await connection.fetchval(
            "SELECT dashboard_stepup.consume($1, $2, $3, $4, $5, $6, $7, ($8::text)::jsonb)",
            phase,
            proof.nonce,
            proof.session_digest,
            KIND,
            binding["target"],
            binding["request_digest"],
            binding["action_digest"],
            json.dumps(result) if result is not None else None,
        )
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict):
        raise RuntimeError("Step-up storage unavailable")
    return value


def _row(row) -> Containment:
    return Containment(
        row["id"], row["source_partition"], tuple(row["descendant_goals"]), row["contained_at"]
    )


async def _existing(connection: Any, source_partition: str):
    return await connection.fetchrow(
        "SELECT id, source_partition, descendant_goals, contained_at "
        "FROM switchboard.source_containments WHERE source_partition = $1",
        source_partition,
    )


async def contain_source(pool: Any, source_partition: str) -> Containment | dict[str, str]:
    """Contain one source partition under a verified dashboard step-up.

    Returns the containment (a retried, already-completed action reads back
    the same record) or the open-dashboard handoff, never a partial write.
    """
    if (
        type(source_partition) is not str
        or not 1 <= len(source_partition) <= 256
        or dashboard_stepup_proof.get() is None
    ):
        return dict(STEP_UP_REQUIRED)
    try:
        async with pool.acquire() as connection, connection.transaction():
            ledger = GoalLedger(connection)
            await ledger.verify_role()
            await ledger.lock_keys([lock_key(source_partition)])
            namespace = await connection.fetchval(
                "SELECT current_database() || ':' || current_schema()"
            )
            target = hashlib.sha256(source_partition.encode()).hexdigest()
            request = {"kind": KIND, "target": target, "namespace": namespace, "version": 1}
            existing = await _existing(connection, source_partition)
            binding = {
                "target": target,
                "request_digest": digest(request),
                # The action binds the current state: a confirmation issued
                # before the source was contained cannot be spent after.
                "action_digest": digest([request, str(existing["id"]) if existing else None]),
            }
            admission = await _consume(connection, "begin", binding)
            if admission.get("state") == "completed":
                result = admission.get("result") or {}
                row = await _existing(connection, source_partition)
                if row is None or str(row["id"]) != result.get("id"):
                    raise RuntimeError("Containment receipt unavailable")
                return _row(row)
            if admission.get("state") != "admitted":
                raise _Refused
            if existing is None:
                descendants = await connection.fetchval(
                    "WITH RECURSIVE d(id) AS ("
                    " SELECT goal_id FROM switchboard.admission_decisions"
                    " WHERE source_partition = $1 AND goal_id IS NOT NULL"
                    " UNION"
                    " SELECT g.id FROM switchboard.goals g JOIN d ON g.caused_by_goal = d.id"
                    ") SELECT COALESCE(array_agg(id ORDER BY id), '{}') FROM d",
                    source_partition,
                )
                existing = await connection.fetchrow(
                    "INSERT INTO switchboard.source_containments "
                    "(id, source_partition, request_digest, descendant_goals) "
                    "VALUES ($1, $2, $3, $4::uuid[]) "
                    "RETURNING id, source_partition, descendant_goals, contained_at",
                    uuid4(),
                    source_partition,
                    binding["request_digest"],
                    list(descendants),
                )
            identity = str(existing["id"])
            retained = await _consume(
                connection,
                "finish",
                binding,
                {"version": 1, "kind": KIND, "id": identity, "rule_id": identity},
            )
            if retained.get("state") != "completed":
                raise RuntimeError("Step-up receipt unavailable")
            return _row(existing)
    except _Refused:
        return dict(STEP_UP_REQUIRED)
    except (
        asyncpg.InvalidSchemaNameError,
        asyncpg.UndefinedTableError,
        asyncpg.UndefinedFunctionError,
        asyncpg.InsufficientPrivilegeError,
    ):
        # An unavailable trusted interface never falls back to an unconfirmed write.
        return dict(STEP_UP_REQUIRED)

"""Waiting records on the sw_047 goal ledger (RFC 0038 S2, S2.2), one locked goal row per step.

Dark: nothing calls this yet. Every method runs on the caller's transaction connection and takes
the goal row ``FOR UPDATE`` first (the lock owner Stop takes), then the envelope row, so a Stop,
a rearm and a wake serialise on the same goal. The database constraints (sw_063) are the second
line: they hold the envelope's immutable bounds, the one-wake-per-fire counter and the unique
successor per generation whatever a caller does.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from butlers.core.goal_labels import Label

_TERMINAL = ("completed", "partial", "failed", "stopped", "budget_exhausted")


@dataclass(frozen=True)
class EnvelopeSpec:
    """What a wait is bound to. The caller derives every field server-side, none from a model."""

    kind: str
    goal_id: UUID
    original_epoch: int
    plan_version: int
    plan_digest: str
    predicate: dict[str, Any]
    predicate_digest: str
    source_set: Sequence[str]
    permitted_sources: Sequence[str]
    grant_snapshot: Sequence[Any]
    grant_digest: str
    label: Label
    deadline: Any  # a finite aware datetime
    max_wakes: int
    condition_instance_ids: Sequence[str]
    approval_effect_id: UUID | None = None
    approval_digest: str | None = None


def plan_digest(
    premise_refs: Sequence[Any], evidence_refs: Sequence[Any], step_refs: Sequence[Any]
):
    """Digest of a plan version's content (identifiers only), stable across processes."""
    body = json.dumps(
        {"premise": premise_refs, "evidence": evidence_refs, "steps": step_refs},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


class WaitLedger:
    def __init__(self, connection: asyncpg.Connection | PoolConnectionProxy) -> None:
        self.connection = connection

    async def _goal(self, goal_id: UUID):
        return await self.connection.fetchrow(
            "SELECT id, status FROM switchboard.goals WHERE id = $1 FOR UPDATE", goal_id
        )

    # -- threads and plans ---------------------------------------------------------------

    async def thread(self, owner_entity_id: UUID, channel: str, conversation_key: str) -> UUID:
        """The one registry row for an owner conversation, created on first use."""
        row = await self.connection.fetchrow(
            "INSERT INTO switchboard.owner_threads (id, owner_entity_id, channel, "
            "conversation_key) VALUES ($1, $2, $3, $4) ON CONFLICT ON CONSTRAINT uq_owner_thread "
            "DO NOTHING RETURNING id",
            uuid4(),
            owner_entity_id,
            channel,
            conversation_key,
        )
        if row is None:
            row = await self.connection.fetchrow(
                "SELECT id FROM switchboard.owner_threads WHERE owner_entity_id = $1 "
                "AND channel = $2 AND conversation_key = $3",
                owner_entity_id,
                channel,
                conversation_key,
            )
        if row is None:  # a registry row is never deleted, so a conflict always finds it
            raise RuntimeError("owner thread vanished after conflict")
        return row["id"]

    async def append_plan(
        self,
        goal_id: UUID,
        epoch: int,
        premise_refs: Sequence[Any],
        evidence_refs: Sequence[Any],
        step_refs: Sequence[Any],
    ) -> int | None:
        """Append the next plan version and bind the goal to it; None when the goal is gone.

        A changed premise is a new version, never an edit of the approved one. Returns the
        existing version when the same content is already the goal's latest (a retry).
        """
        goal = await self._goal(goal_id)
        if goal is None:
            return None
        digest = plan_digest(premise_refs, evidence_refs, step_refs)
        latest = await self.connection.fetchrow(
            "SELECT version, digest FROM switchboard.goal_plans WHERE goal_id = $1 "
            "ORDER BY version DESC LIMIT 1",
            goal_id,
        )
        if latest is not None and latest["digest"] == digest:
            return latest["version"]
        version = 1 if latest is None else latest["version"] + 1
        await self.connection.execute(
            "INSERT INTO switchboard.goal_plans (goal_id, version, predecessor_version, "
            "premise_refs, evidence_refs, step_refs, created_epoch, digest) "
            "VALUES ($1, $2, $3, $4::text::jsonb, $5::text::jsonb, $6::text::jsonb, $7, $8)",
            goal_id,
            version,
            None if latest is None else latest["version"],
            json.dumps(list(premise_refs)),
            json.dumps(list(evidence_refs)),
            json.dumps(list(step_refs)),
            epoch,
            digest,
        )
        await self.connection.execute(
            "UPDATE switchboard.goals SET plan_version = $2, updated_at = now() WHERE id = $1",
            goal_id,
            version,
        )
        return version

    # -- envelopes ------------------------------------------------------------------------

    async def open_envelope(self, spec: EnvelopeSpec) -> UUID | None:
        """Create an envelope and its generation-0 registration; None when the goal cannot wait.

        A goal waits only from a live, non-terminal state. A fired predecessor that was never
        rearmed is superseded (cancelled) in the same transaction: a new envelope never inherits
        or erases the old one's wake history, it replaces it.
        """
        goal = await self._goal(spec.goal_id)
        if goal is None or goal["status"] in _TERMINAL:
            return None
        await self.connection.execute(
            "UPDATE switchboard.wait_envelopes SET state = 'cancelled', updated_at = now() "
            "WHERE goal_id = $1 AND state = 'fired'",
            spec.goal_id,
        )
        envelope_id = uuid4()
        _, instr, data, sens = spec.label.to_row()
        await self.connection.execute(
            "INSERT INTO switchboard.wait_envelopes (id, goal_id, kind, original_epoch, "
            "plan_version, plan_digest, predicate, predicate_digest, source_set, "
            "permitted_sources, grant_snapshot, grant_digest, label_version, label_instr, "
            "label_data, label_sens, deadline, max_wakes, approval_effect_id, approval_digest) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7::text::jsonb, $8, $9, $10, $11::text::jsonb, $12, "
            "1, $13, $14, $15, $16, $17, $18, $19)",
            envelope_id,
            spec.goal_id,
            spec.kind,
            spec.original_epoch,
            spec.plan_version,
            spec.plan_digest,
            json.dumps(spec.predicate),
            spec.predicate_digest,
            list(spec.source_set),
            list(spec.permitted_sources),
            json.dumps(list(spec.grant_snapshot)),
            spec.grant_digest,
            instr,
            data,
            sens,
            spec.deadline,
            spec.max_wakes,
            spec.approval_effect_id,
            spec.approval_digest,
        )
        await self.connection.execute(
            "INSERT INTO switchboard.wait_generations (goal_id, envelope_id, generation, "
            "condition_instance_ids) VALUES ($1, $2, 0, $3)",
            spec.goal_id,
            envelope_id,
            list(spec.condition_instance_ids),
        )
        return envelope_id

    async def close_envelope(self, goal_id: UUID, envelope_id: UUID, state: str) -> bool:
        """Expire or cancel an envelope that is still open or fired (Stop, deadline, supersede)."""
        if state not in ("expired", "cancelled"):
            raise ValueError("an envelope closes as expired or cancelled")
        if await self._goal(goal_id) is None:
            return False
        row = await self.connection.fetchrow(
            "UPDATE switchboard.wait_envelopes SET state = $3, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2 AND state IN ('open','fired') RETURNING id",
            goal_id,
            envelope_id,
            state,
        )
        return row is not None

    async def rearm(
        self,
        goal_id: UUID,
        envelope_id: UUID,
        condition_instance_ids: Sequence[str],
        registration_cursor: dict[str, Any],
    ) -> int | None:
        """Open the next generation after its successor ran and the goal waits again (S2.2).

        Returns the new generation, or None when any bound forbids it: the goal is Stopped or
        terminal, a lead is still live, the successor of the fired generation has not launched,
        the cumulative cap is reached or the original deadline passed. A retry after commit
        returns the generation already opened; nothing is counted twice.
        """
        goal = await self._goal(goal_id)
        if goal is None or goal["status"] in _TERMINAL:
            return None
        env = await self.connection.fetchrow(
            "SELECT * FROM switchboard.wait_envelopes WHERE goal_id = $1 AND id = $2 FOR UPDATE",
            goal_id,
            envelope_id,
        )
        if env is None:
            return None
        if env["state"] == "open" and env["generation"] > 0:
            opened = await self.connection.fetchrow(
                "SELECT predecessor_fire_event_id FROM switchboard.wait_generations "
                "WHERE goal_id = $1 AND envelope_id = $2 AND generation = $3",
                goal_id,
                envelope_id,
                env["generation"],
            )
            return env["generation"] if opened is not None else None
        if env["state"] != "fired" or env["consumed_wakes"] >= env["max_wakes"]:
            return None
        if await self.connection.fetchval("SELECT now() >= $1", env["deadline"]):
            return None
        if await self.connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM switchboard.invocations WHERE goal_id = $1 "
            "AND depth = 0 AND status IN ('launching','running'))",
            goal_id,
        ):
            return None
        successor = await self.connection.fetchrow(
            "SELECT epoch, fire_event_id FROM switchboard.wake_intents "
            "WHERE goal_id = $1 AND envelope_id = $2 AND generation = $3 AND state = 'launched'",
            goal_id,
            envelope_id,
            env["generation"],
        )
        if successor is None:
            return None
        generation = env["generation"] + 1
        await self.connection.execute(
            "INSERT INTO switchboard.wait_generations (goal_id, envelope_id, generation, "
            "condition_instance_ids, predecessor_epoch, predecessor_fire_event_id, "
            "registration_cursor) VALUES ($1, $2, $3, $4, $5, $6, $7::text::jsonb)",
            goal_id,
            envelope_id,
            generation,
            list(condition_instance_ids),
            successor["epoch"],
            successor["fire_event_id"],
            json.dumps(registration_cursor),
        )
        await self.connection.execute(
            "UPDATE switchboard.wait_envelopes SET state = 'open', generation = $3, "
            "updated_at = now() WHERE goal_id = $1 AND id = $2",
            goal_id,
            envelope_id,
            generation,
        )
        await self.connection.execute(
            "UPDATE switchboard.goals SET wait_generation = $2, updated_at = now() WHERE id = $1",
            goal_id,
            generation,
        )
        return generation

    # -- the runner's own waits ----------------------------------------------------------

    async def open_settle(self, goal_id: UUID, epoch: int) -> UUID | None:
        """Wait on the effects that can still change; resolving needs no successor epoch.

        The envelope's bounds are the goal's own: its label and budget deadline, one wake, no
        grants (nothing is ever launched from a settle wait). The goal's plan is the latest
        version, or an empty v1 when the lead never planned.
        """
        return await self._open_own_wait(
            goal_id, epoch, "settle", {"kind": "settle"}, f"settle:{goal_id}"
        )

    async def open_approval(
        self, goal_id: UUID, epoch: int, effect_id: UUID, args_digest: str
    ) -> UUID | None:
        """Wait on the owner's decision about one parked effect (RFC 0038 S2.2, bu-dhloi1.204).

        Same bounds as a settle wait, bound to the exact effect and its argument digest. Its one
        wake launches a successor summary lead once the approved effect has settled.
        """
        digest = hashlib.sha256(args_digest.encode()).hexdigest()
        return await self._open_own_wait(
            goal_id,
            epoch,
            "approval",
            {"kind": "approval"},
            f"approval:{effect_id}",
            effect_id=effect_id,
            approval_digest=digest,
        )

    async def open_owner_answer(
        self, goal_id: UUID, epoch: int, prompt_effect_id: UUID, expires_at: Any
    ) -> UUID | None:
        """Wait only for the owner's answer to one delivered prompt (RFC 0038 S2.1, S2.2).

        The wait needs no approval: the answer is authenticated by admission's exact reply
        binding and cannot approve an unrelated effect. The envelope ends with the prompt, at the
        goal's budget deadline if that is sooner. Its one wake is the joined reply.
        """
        return await self._open_own_wait(
            goal_id,
            epoch,
            "owner_answer",
            {"kind": "owner_answer", "prompt_effect_id": str(prompt_effect_id)},
            f"owner_answer:{prompt_effect_id}",
            source="goal.inbox",
            latest=expires_at,
        )

    async def _open_own_wait(
        self,
        goal_id: UUID,
        epoch: int,
        kind: str,
        predicate: dict[str, Any],
        instance: str,
        *,
        effect_id: UUID | None = None,
        approval_digest: str | None = None,
        source: str = "effects",
        latest: Any = None,
    ) -> UUID | None:
        goal = await self.connection.fetchrow(
            "SELECT plan_version, label_version, label_instr, label_data, label_sens "
            "FROM switchboard.goals WHERE id = $1",
            goal_id,
        )
        deadline = await self.connection.fetchval(
            "SELECT deadline FROM switchboard.budgets WHERE goal_id = $1", goal_id
        )
        if goal is None or deadline is None:
            return None
        if latest is not None and latest < deadline:
            deadline = latest
        version = goal["plan_version"] or await self.append_plan(goal_id, epoch, [], [], [])
        if version is None:
            return None
        plan = await self.connection.fetchval(
            "SELECT digest FROM switchboard.goal_plans WHERE goal_id = $1 AND version = $2",
            goal_id,
            version,
        )
        if plan is None:
            return None
        return await self.open_envelope(
            EnvelopeSpec(
                kind=kind,
                goal_id=goal_id,
                original_epoch=epoch,
                plan_version=version,
                plan_digest=plan,
                predicate=predicate,
                predicate_digest=hashlib.sha256(
                    json.dumps(predicate, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                source_set=[source],
                permitted_sources=[source],
                grant_snapshot=[],
                grant_digest=hashlib.sha256(b"[]").hexdigest(),
                label=Label.from_row(
                    goal["label_version"],
                    goal["label_instr"],
                    goal["label_data"],
                    goal["label_sens"],
                ),
                deadline=deadline,
                max_wakes=1,
                condition_instance_ids=[instance],
                approval_effect_id=effect_id,
                approval_digest=approval_digest,
            )
        )

    async def fire_settle(self, goal_id: UUID) -> bool:
        """The deterministic settle fire: open -> fired with its one wake, no epoch or intent."""
        row = await self.connection.fetchrow(
            "UPDATE switchboard.wait_envelopes SET state = 'fired', "
            "consumed_wakes = consumed_wakes + 1, updated_at = now() "
            "WHERE goal_id = $1 AND kind = 'settle' AND state = 'open' RETURNING id",
            goal_id,
        )
        return row is not None

    async def open_settle_goals(self) -> list[UUID]:
        rows = await self.connection.fetch(
            "SELECT g.id FROM switchboard.goals g WHERE g.status = 'waiting' AND EXISTS "
            "(SELECT 1 FROM switchboard.wait_envelopes e WHERE e.goal_id = g.id "
            "AND e.kind = 'settle' AND e.state = 'open') ORDER BY g.id"
        )
        return [row["id"] for row in rows]

    async def cancel_waits(self, goal_id: UUID, reason: str) -> None:
        """Stop or expiry: close the goal's envelopes and cancel wake intents not yet launched."""
        if reason not in ("stopped", "expired"):
            raise ValueError("a wait is cancelled for Stop or expiry")
        await self.connection.execute(
            "UPDATE switchboard.wake_intents SET state = 'cancelled', cancelled_reason = $2, "
            "claimed_by = NULL, claim_expires_at = NULL, updated_at = now() "
            "WHERE goal_id = $1 AND state IN ('pending','failed','claimed')",
            goal_id,
            reason,
        )
        await self.connection.execute(
            "UPDATE switchboard.wait_envelopes SET state = $2, updated_at = now() "
            "WHERE goal_id = $1 AND state IN ('open','fired')",
            goal_id,
            "expired" if reason == "expired" else "cancelled",
        )

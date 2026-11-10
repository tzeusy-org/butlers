"""Wake admission: a condition fire becomes one successor epoch, or a terminal refusal.

RFC 0038 S2.1 / S2.2, bu-dhloi1.7 PR B2. The evaluator commits its fire intent in
``public.condition_fire_intents`` (its own transaction); this module is the second committed
step. ``admit`` runs in one transaction on the Switchboard runtime connection, takes the goal row
``FOR UPDATE`` (the lock Stop takes) and then either

* admits the wake: the envelope's conditional ``open -> fired`` CAS and wake count, the
  ``condition_joined`` admission decision, the ``condition_fire`` inbox row, the ``wake`` epoch
  record and the ``pending`` wake intent commit together, and the waiting goal is ``admitted``
  again; or
* refuses it: a terminal ``not_admitted`` wake intent with its reason is recorded and nothing
  else changes (Stop, expiry, the cap, a stale generation and a goal that is not waiting).

The unique ``wake_intents.fire_key`` bridges the two steps: a crash after the fire intent
commits and before admission leaves a fire intent with no wake intent, which ``unadmitted`` finds
and ``admit`` completes. Replaying ``admit`` for a fire it already settled returns the stored
outcome and writes nothing, so a crash at any point mints at most one successor and counts at most
one wake.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from butlers.conditions import FireIntent
from butlers.conditions.sql import intent_from_row
from butlers.core.goal_admission import SourceRef
from butlers.core.goal_labels import Label

_REFUSAL_REASONS = (
    "stopped",
    "expired",
    "cap_reached",
    "goal_not_waiting",
    "envelope_not_open",
    "stale_generation",
    "admit_error",
)
_LAUNCHING_KINDS = ("conditions", "owner_answer", "approval")


@dataclass(frozen=True, slots=True)
class WakeResult:
    """How a fire ended: ``admitted``, ``not_admitted``, or ``unmapped`` (not a goal's wait)."""

    state: Literal["admitted", "not_admitted", "unmapped"]
    reason: str | None = None
    goal_id: UUID | None = None
    intent_id: UUID | None = None
    epoch: int | None = None
    replayed: bool = False


def condition_source(envelope_id: UUID, generation: int, event_id: UUID) -> SourceRef:
    """The evaluator's SourceRef for a fire, built from its durable receipt alone."""
    return SourceRef("occurrence", f"condition:{envelope_id}", f"gen:{generation}:event:{event_id}")


class WakeAdmission:
    def __init__(self, connection: asyncpg.Connection | PoolConnectionProxy) -> None:
        self.connection = connection

    async def unadmitted(self, grace_seconds: float, limit: int = 50) -> list[FireIntent]:
        """Fire intents of goals' waits with no wake intent yet, older than the grace.

        The grace keeps the sweeper off a fire whose admission is still in flight; a fire it
        races anyway is harmless because ``admit`` is idempotent on the fire key.
        """
        rows = await self.connection.fetch(
            "SELECT fi.fire_key, fi.instance_id, fi.generation, fi.event_id, fi.reason, "
            "fi.fired_at, fi.snapshot_generation, fi.cursors::text AS cursors, fi.claimed_by, "
            "fi.label_version, fi.label_instr, fi.label_data, fi.label_sens "
            "FROM public.condition_fire_intents fi "
            "WHERE fi.fired_at <= clock_timestamp() - make_interval(secs => $1::float8) "
            "AND NOT EXISTS (SELECT 1 FROM switchboard.wake_intents w "
            "WHERE w.fire_key = fi.fire_key) "
            # The same event settled this generation under another key (a replayed fire).
            "AND NOT EXISTS (SELECT 1 FROM switchboard.wake_intents w "
            "JOIN switchboard.wait_generations g ON (g.goal_id, g.envelope_id, g.generation) = "
            "(w.goal_id, w.envelope_id, w.generation) "
            "WHERE w.fire_event_id = fi.event_id "
            "AND fi.instance_id = ANY(g.condition_instance_ids)) "
            "AND EXISTS (SELECT 1 FROM switchboard.wait_generations wg "
            "WHERE fi.instance_id = ANY(wg.condition_instance_ids)) "
            "ORDER BY fi.fired_at, fi.fire_key LIMIT $2",
            grace_seconds,
            limit,
        )
        return [intent_from_row(row) for row in rows]

    async def _stored(self, fire_key: UUID) -> WakeResult | None:
        row = await self.connection.fetchrow(
            "SELECT id, goal_id, state, not_admitted_reason, epoch FROM switchboard.wake_intents "
            "WHERE fire_key = $1",
            fire_key,
        )
        if row is None:
            return None
        return WakeResult(
            "not_admitted" if row["state"] == "not_admitted" else "admitted",
            row["not_admitted_reason"],
            row["goal_id"],
            row["id"],
            row["epoch"],
            replayed=True,
        )

    async def answer_envelope(self, goal_id: UUID, prompt_effect_id: UUID) -> Any:
        """The goal's open owner-answer wait on exactly this prompt, locked, if it can still wake.

        None unless the goal is waiting and the envelope is an open ``owner_answer`` bound to the
        prompt that is inside its deadline and wake cap. Takes the goal and envelope rows
        ``FOR UPDATE``; the caller holds the goal lock already (admission locks it first).
        """
        return await self.connection.fetchrow(
            "SELECT e.* FROM switchboard.wait_envelopes e JOIN switchboard.goals g "
            "ON g.id = e.goal_id WHERE e.goal_id = $1 AND g.status = 'waiting' "
            "AND e.kind = 'owner_answer' AND e.state = 'open' AND e.deadline > now() "
            "AND e.consumed_wakes < e.max_wakes "
            "AND e.predicate->>'prompt_effect_id' = $2 FOR UPDATE OF e",
            goal_id,
            str(prompt_effect_id),
        )

    async def admit_answer(self, envelope: Any, decision_id: UUID, inbox_id: UUID) -> WakeResult:
        """The joined reply is the wait's one wake: its successor epoch and pending intent.

        Admission already committed (in this transaction) the ``reply_joined`` decision and the
        ``owner_reply`` inbox row; this adds the envelope's ``open -> fired`` CAS, the ``wake``
        epoch record naming that inbox row, the pending wake intent and the goal's return to
        ``admitted``. The fire key and event id derive from the decision, so a replayed
        admission (which returns the stored decision) never reaches here twice.
        """
        goal_id, envelope_id, generation = (
            envelope["goal_id"],
            envelope["id"],
            envelope["generation"],
        )
        predecessor = await self.connection.fetchval(
            "SELECT id FROM switchboard.invocations WHERE goal_id = $1 AND depth = 0 "
            "ORDER BY lead_epoch DESC, created_at DESC LIMIT 1",
            goal_id,
        )
        fired = await self.connection.fetchval(
            "UPDATE switchboard.wait_envelopes SET state = 'fired', "
            "consumed_wakes = consumed_wakes + 1, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2 AND state = 'open' AND generation = $3 "
            "AND consumed_wakes < max_wakes RETURNING id",
            goal_id,
            envelope_id,
            generation,
        )
        if predecessor is None or fired is None:
            raise RuntimeError("an open owner-answer wait could not be fired")
        epoch = await self.connection.fetchval(
            "SELECT COALESCE(max(epoch) + 1, 0) FROM switchboard.goal_epochs WHERE goal_id = $1",
            goal_id,
        )
        await self.connection.execute(
            "INSERT INTO switchboard.goal_epochs (goal_id, epoch, predecessor_invocation_id, "
            "reason, input_refs, label_version, label_instr, label_data, label_sens, "
            "grant_digest, state) VALUES ($1, $2, $3, 'wake', $4::text::jsonb, $5, $6, $7, $8, "
            "$9, 'pending')",
            goal_id,
            epoch,
            predecessor,
            json.dumps([f"inbox:{inbox_id}"]),
            envelope["label_version"],
            envelope["label_instr"],
            envelope["label_data"],
            envelope["label_sens"],
            envelope["grant_digest"],
        )
        intent_id = uuid4()
        await self.connection.execute(
            "INSERT INTO switchboard.wake_intents (id, goal_id, envelope_id, generation, "
            "fire_key, fire_event_id, fire_cursors, epoch, state) "
            "VALUES ($1, $2, $3, $4, $5, $6, '{}'::jsonb, $7, 'pending')",
            intent_id,
            goal_id,
            envelope_id,
            generation,
            uuid5(NAMESPACE_OID, f"answer:{decision_id}"),
            uuid5(NAMESPACE_OID, f"answer-event:{inbox_id}"),
            epoch,
        )
        await self.connection.execute(
            "UPDATE switchboard.goals SET status = 'admitted', updated_at = now() WHERE id = $1",
            goal_id,
        )
        return WakeResult("admitted", None, goal_id, intent_id, epoch)

    async def abandon(self, fire: FireIntent) -> WakeResult:
        """Close a fire whose admission keeps raising with a terminal ``admit_error`` receipt."""
        if (stored := await self._stored(fire.fire_key)) is not None:
            return stored
        generation = await self.connection.fetchrow(
            "SELECT goal_id, envelope_id, generation FROM switchboard.wait_generations "
            "WHERE $1 = ANY(condition_instance_ids) ORDER BY created_at DESC LIMIT 1",
            fire.instance_id,
        )
        if generation is None:
            return WakeResult("unmapped")
        return await self._refuse(
            fire,
            generation["goal_id"],
            generation["envelope_id"],
            generation["generation"],
            "admit_error",
        )

    async def admit(self, fire: FireIntent) -> WakeResult:
        """Complete one fire: admit its successor, or record why it cannot be one."""
        if (stored := await self._stored(fire.fire_key)) is not None:
            return stored
        generation = await self.connection.fetchrow(
            "SELECT goal_id, envelope_id, generation FROM switchboard.wait_generations "
            "WHERE $1 = ANY(condition_instance_ids) ORDER BY created_at DESC LIMIT 1",
            fire.instance_id,
        )
        if generation is None:
            return WakeResult("unmapped")
        # Condition instance ids are generation-owned, so the id alone names the envelope
        # generation; the evaluator's own instance generation is a different counter.
        goal_id, envelope_id = generation["goal_id"], generation["envelope_id"]
        wake_generation = generation["generation"]
        goal = await self.connection.fetchrow(
            "SELECT id, status, label_version, label_instr, label_data, label_sens "
            "FROM switchboard.goals WHERE id = $1 FOR UPDATE",
            goal_id,
        )
        if (stored := await self._stored(fire.fire_key)) is not None:
            return stored  # another admitter won the goal lock first
        envelope = await self.connection.fetchrow(
            "SELECT *, now() >= deadline AS past_deadline FROM switchboard.wait_envelopes "
            "WHERE goal_id = $1 AND id = $2 FOR UPDATE",
            goal_id,
            envelope_id,
        )
        reason = self._refusal(goal, envelope, wake_generation)
        if reason == "expired":
            await self.connection.execute(
                "UPDATE switchboard.wait_envelopes SET state = 'expired', updated_at = now() "
                "WHERE goal_id = $1 AND id = $2 AND state IN ('open','fired')",
                goal_id,
                envelope_id,
            )
        if reason is not None:
            return await self._refuse(fire, goal_id, envelope_id, wake_generation, reason)
        return await self._admit(fire, goal, envelope, wake_generation)

    @staticmethod
    def _refusal(goal: Any, envelope: Any, wake_generation: int) -> str | None:
        if goal["status"] == "stopped":
            return "stopped"
        if goal["status"] != "waiting":
            return "goal_not_waiting"
        if envelope["generation"] != wake_generation:
            return "stale_generation"
        if envelope["kind"] not in _LAUNCHING_KINDS or envelope["state"] != "open":
            return "envelope_not_open"
        if envelope["past_deadline"]:
            return "expired"
        if envelope["consumed_wakes"] >= envelope["max_wakes"]:
            return "cap_reached"
        return None

    async def _refuse(
        self, fire: FireIntent, goal_id: UUID, envelope_id: UUID, generation: int, reason: str
    ) -> WakeResult:
        assert reason in _REFUSAL_REASONS
        intent_id = uuid4()
        row = await self.connection.fetchrow(
            "INSERT INTO switchboard.wake_intents (id, goal_id, envelope_id, generation, "
            "fire_key, fire_event_id, fire_cursors, epoch, state, not_admitted_reason) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7::text::jsonb, NULL, 'not_admitted', $8) "
            "ON CONFLICT DO NOTHING RETURNING id",
            intent_id,
            goal_id,
            envelope_id,
            generation,
            fire.fire_key,
            fire.event_id,
            json.dumps(dict(fire.cursors), sort_keys=True),
            reason,
        )
        if row is None:  # the same event already settled this generation under another key
            existing = await self.connection.fetchrow(
                "SELECT id, state, not_admitted_reason, epoch FROM switchboard.wake_intents "
                "WHERE envelope_id = $1 AND generation = $2 AND fire_event_id = $3",
                envelope_id,
                generation,
                fire.event_id,
            )
            if existing is None:  # a conflict names a stored row, so this cannot happen
                raise RuntimeError("wake intent vanished after conflict")
            return WakeResult(
                "not_admitted" if existing["state"] == "not_admitted" else "admitted",
                existing["not_admitted_reason"],
                goal_id,
                existing["id"],
                existing["epoch"],
                replayed=True,
            )
        return WakeResult("not_admitted", reason, goal_id, intent_id)

    async def _admit(
        self, fire: FireIntent, goal: Any, envelope: Any, generation: int
    ) -> WakeResult:
        goal_id, envelope_id = goal["id"], envelope["id"]
        predecessor = await self.connection.fetchval(
            "SELECT id FROM switchboard.invocations WHERE goal_id = $1 AND depth = 0 "
            "ORDER BY lead_epoch DESC, created_at DESC LIMIT 1",
            goal_id,
        )
        if predecessor is None:  # a waiting goal always ran a lead; refuse rather than guess
            return await self._refuse(fire, goal_id, envelope_id, generation, "goal_not_waiting")
        fired = await self.connection.fetchval(
            "UPDATE switchboard.wait_envelopes SET state = 'fired', "
            "consumed_wakes = consumed_wakes + 1, updated_at = now() "
            "WHERE goal_id = $1 AND id = $2 AND state = 'open' AND generation = $3 "
            "AND consumed_wakes < max_wakes RETURNING id",
            goal_id,
            envelope_id,
            generation,
        )
        if fired is None:
            return await self._refuse(fire, goal_id, envelope_id, generation, "envelope_not_open")
        source = condition_source(envelope_id, generation, fire.event_id)
        decision_id, inbox_id, intent_id = uuid4(), uuid4(), uuid4()
        await self.connection.execute(
            "INSERT INTO switchboard.admission_decisions (id, attempt_id, source_key, source_ref, "
            "source_kind, goal_id, result, reason, input_kind, occurrence_key) "
            "VALUES ($1, $2, $3, $4, 'occurrence', $5, 'joined', 'condition_joined', "
            "'condition_fire', $6)",
            decision_id,
            fire.fire_key,
            source.key,
            source.id,
            goal_id,
            source.occurrence,
        )
        await self.connection.execute(
            "INSERT INTO switchboard.admission_attempt_bindings (attempt_id, source_key, "
            "decision_id) VALUES ($1, $2, $3)",
            fire.fire_key,
            source.key,
            decision_id,
        )
        # The wake's lead is exposed by what woke it: the fire's label joins the envelope's.
        label = Label.from_row(
            envelope["label_version"],
            envelope["label_instr"],
            envelope["label_data"],
            envelope["label_sens"],
        ).join(fire.label)
        version, instr, data, sens = label.to_row()
        epoch = await self.connection.fetchval(
            "SELECT COALESCE(max(epoch) + 1, 0) FROM switchboard.goal_epochs WHERE goal_id = $1",
            goal_id,
        )
        await self.connection.execute(
            "INSERT INTO switchboard.goal_epochs (goal_id, epoch, predecessor_invocation_id, "
            "reason, input_refs, label_version, label_instr, label_data, label_sens, "
            "grant_digest, state) VALUES ($1, $2, $3, 'wake', $4::text::jsonb, $5, $6, $7, $8, "
            "$9, 'pending')",
            goal_id,
            epoch,
            predecessor,
            json.dumps([f"inbox:{inbox_id}"]),
            version,
            instr,
            data,
            sens,
            envelope["grant_digest"],
        )
        # The inbox row's foreign key names the wake intent, so the intent goes first.
        await self.connection.execute(
            "INSERT INTO switchboard.wake_intents (id, goal_id, envelope_id, generation, "
            "fire_key, fire_event_id, fire_cursors, epoch, state) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7::text::jsonb, $8, 'pending')",
            intent_id,
            goal_id,
            envelope_id,
            generation,
            fire.fire_key,
            fire.event_id,
            json.dumps(dict(fire.cursors), sort_keys=True),
            epoch,
        )
        await self.connection.execute(
            "INSERT INTO switchboard.goal_inbox (id, goal_id, decision_id, source_key, "
            "source_ref, input_kind, event_id, envelope_id, envelope_generation, label_version, "
            "label_instr, label_data, label_sens) VALUES ($1, $2, $3, $4, $5, 'condition_fire', "
            "$6, $7, $8, $9, $10, $11, $12)",
            inbox_id,
            goal_id,
            decision_id,
            source.key,
            source.id,
            fire.event_id,
            envelope_id,
            generation,
            version,
            instr,
            data,
            sens,
        )
        await self.connection.execute(
            "UPDATE switchboard.goals SET status = 'admitted', updated_at = now() WHERE id = $1",
            goal_id,
        )
        return WakeResult("admitted", None, goal_id, intent_id, epoch)

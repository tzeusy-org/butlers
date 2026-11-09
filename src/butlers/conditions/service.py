"""register / evaluate / consume_fire: the one conditions API (RFC 0038 S4).

Deterministic infrastructure, not an LLM tool. The store seam is transactional: instance
state, receipts and the fire intent (the outbox row the goal runner consumes) commit together.
The runner (bu-u5s4w8 / bu-dhloi1.7) consumes ``consume_fire``; it never evaluates predicates.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from butlers.conditions import predicate as ast
from butlers.conditions.evaluate import advance, combine, initial_leaves
from butlers.conditions.model import (
    ClockReading,
    ConditionEvent,
    FireIntent,
    Grant,
    Instance,
    Receipt,
    Snapshot,
    State,
    Tri,
)

MAX_LIFETIME = timedelta(days=90)
DEPENDENCY = "system.dep.conditions"


class ConditionsUnavailable(RuntimeError):
    """Persistence/evaluation is unavailable; accepted work is retained, nothing was invented."""


class RegistrationRefused(ValueError):
    """The request is outside its grant, boundary or the closed language."""


class ConditionTx(Protocol):
    async def get_instance(self, instance_id: str) -> Instance | None:
        """Read with a row lock (``FOR UPDATE``) held to commit."""

    async def insert_instance(self, instance: Instance) -> Instance:
        """Idempotent by id; same digest returns the stored row, different digest raises."""

    async def save_instance(self, instance: Instance, expected_version: int) -> None:
        """CAS on version; raise on mismatch."""

    async def append_receipt(self, receipt: Receipt) -> None: ...

    async def insert_fire_intent(self, intent: FireIntent) -> FireIntent:
        """Unique per fire_key; a second insert returns the original."""

    async def get_fire_intent(self, fire_key: UUID) -> FireIntent | None: ...

    async def claim_fire_intent(self, fire_key: UUID, consumer: str) -> FireIntent | None:
        """First consumer wins; the same consumer re-reads its claim; others get None."""


class ConditionStore(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[ConditionTx]: ...


class DependencyReporter(Protocol):
    """Seam to the .14 system.dep projection; this module never writes perception itself."""

    async def report(self, dependency: str, state: str, reason: str) -> None: ...


@dataclass(frozen=True)
class Outcome:
    instance: Instance
    receipt: Receipt
    intent: FireIntent | None


def check_registration(
    predicate: object,
    grant: Grant,
    *,
    goal_id: str,
    starts_at: datetime,
    expires_at: datetime,
) -> dict:
    """Refuse before any side effect. Returns the canonical predicate."""
    try:
        canonical = ast.validate(predicate)
    except ast.PredicateError as exc:
        raise RegistrationRefused(str(exc)) from None
    if goal_id != grant.goal_id:
        raise RegistrationRefused("goal does not match grant")
    if not starts_at < expires_at <= starts_at + MAX_LIFETIME:
        raise RegistrationRefused("finite bounded expiry required")
    for leaf in ast.leaves(canonical):
        body = leaf.body
        if leaf.source not in grant.sources:
            raise RegistrationRefused(f"undeclared source {leaf.source}")
        if leaf.tag == "decision" and body["subject_id"] not in grant.decisions:
            raise RegistrationRefused("decision does not belong to the bound goal")
        if leaf.tag == "owner_input" and (
            body["goal_id"] != grant.goal_id or body["binding_ref"] not in grant.bindings
        ):
            raise RegistrationRefused("owner input does not belong to the bound goal")
        if leaf.tag == "effect_settled" and body["effect_id"] not in grant.effects:
            raise RegistrationRefused("effect does not belong to the bound goal")
        if leaf.tag == "transition" and body["subject"] not in grant.subjects:
            raise RegistrationRefused("transition subject is outside the grant")
    return canonical


class ConditionEvaluator:
    def __init__(
        self,
        store: ConditionStore,
        *,
        reporter: DependencyReporter | None = None,
        clock: ClockReading | None = None,
    ) -> None:
        self.store = store
        self.reporter = reporter
        self.clock = clock

    async def _guard(self, coro_factory):
        try:
            result = await coro_factory()
        except (ConditionsUnavailable, RegistrationRefused, KeyError):
            raise
        except (OSError, TimeoutError, ConnectionError) as exc:
            if self.reporter is not None:
                await self.reporter.report(DEPENDENCY, "down", type(exc).__name__)
            raise ConditionsUnavailable("conditions store unavailable") from exc
        return result

    async def register(
        self,
        *,
        instance_id: str,
        predicate: object,
        grant: Grant,
        goal_id: str,
        snapshot: Snapshot,
        expires_at: datetime,
        generation: int = 1,
        cursors: dict[str, int] | None = None,
    ) -> Outcome:
        """Validate, evaluate current state (no lost wake), persist, fire if already true."""
        canonical = check_registration(
            predicate, grant, goal_id=goal_id, starts_at=snapshot.now, expires_at=expires_at
        )
        inst = Instance(
            id=instance_id,
            predicate=canonical,
            predicate_digest=ast.digest(canonical),
            goal_id=goal_id,
            grant_digest=grant.digest,
            source_boundary=tuple(sorted(ast.sources(canonical))),
            starts_at=snapshot.now,
            expires_at=expires_at,
            generation=generation,
            leaves=initial_leaves(canonical, snapshot),
            cursors=dict(cursors or {}),
        )
        inst = replace(inst, result=combine(canonical, dict(inst.leaves)))

        async def run() -> Outcome:
            async with self.store.transaction() as tx:
                stored = await tx.insert_instance(inst)
                if stored is not inst and stored != inst:
                    if (stored.predicate_digest, stored.grant_digest, stored.generation) != (
                        inst.predicate_digest,
                        inst.grant_digest,
                        inst.generation,
                    ):
                        raise RegistrationRefused("immutable condition instance changed")
                    return await self._existing(tx, stored, snapshot.now)
                if inst.result is Tri.TRUE:
                    fired = replace(inst, state=State.FIRED, version=1)
                    await tx.save_instance(fired, 0)
                    intent = await tx.insert_fire_intent(
                        self._intent(fired, "registration", "fired:registration", snapshot)
                    )
                    receipt = self._receipt(
                        fired, "register", "fired:registration", "registration", snapshot
                    )
                    await tx.append_receipt(receipt)
                    return Outcome(fired, receipt, intent)
                receipt = self._receipt(inst, "register", "pending", None, snapshot)
                await tx.append_receipt(receipt)
                return Outcome(inst, receipt, None)

        return await self._guard(run)

    async def evaluate(
        self,
        instance_id: str,
        events: Iterable[ConditionEvent],
        *,
        now: datetime,
        unmeasurable: frozenset[str] = frozenset(),
        snapshot_generation: int = 0,
    ) -> Outcome:
        """Advance the durable cursor with trusted events; at most one fire ever commits."""
        events = list(events)

        async def run() -> Outcome:
            async with self.store.transaction() as tx:
                inst = await tx.get_instance(instance_id)
                if inst is None:
                    raise KeyError(instance_id)
                snap = Snapshot(now=now, unmeasurable=unmeasurable, generation=snapshot_generation)
                result = advance(inst, events, now, unmeasurable=unmeasurable)
                if not result.changed:
                    return await self._existing(tx, inst, now, result.reason)
                await tx.save_instance(result.instance, inst.version)
                intent = None
                if result.trigger is not None:
                    intent = await tx.insert_fire_intent(
                        self._intent(result.instance, result.trigger, result.reason, snap)
                    )
                kind = "expire" if result.instance.state is State.EXPIRED else "evaluate"
                receipt = self._receipt(result.instance, kind, result.reason, result.trigger, snap)
                await tx.append_receipt(receipt)
                return Outcome(result.instance, receipt, intent)

        return await self._guard(run)

    async def cancel(self, instance_id: str, *, now: datetime) -> Outcome:
        async def run() -> Outcome:
            async with self.store.transaction() as tx:
                inst = await tx.get_instance(instance_id)
                if inst is None:
                    raise KeyError(instance_id)
                if inst.state is not State.OPEN:
                    return await self._existing(tx, inst, now)
                new = replace(inst, state=State.CANCELLED, version=inst.version + 1)
                await tx.save_instance(new, inst.version)
                receipt = self._receipt(new, "cancel", "cancelled", None, Snapshot(now=now))
                await tx.append_receipt(receipt)
                return Outcome(new, receipt, None)

        return await self._guard(run)

    async def consume_fire(self, instance_id: str, consumer: str) -> FireIntent | None:
        """Claim the unique fire intent. Never an arbitrary launch command."""

        async def run() -> FireIntent | None:
            async with self.store.transaction() as tx:
                inst = await tx.get_instance(instance_id)
                if inst is None or inst.state is not State.FIRED:
                    return None
                return await tx.claim_fire_intent(inst.fire_key, consumer)

        return await self._guard(run)

    async def _existing(
        self, tx: ConditionTx, inst: Instance, now: datetime, reason: str | None = None
    ) -> Outcome:
        intent = await tx.get_fire_intent(inst.fire_key) if inst.state is State.FIRED else None
        receipt = self._receipt(
            inst,
            "evaluate",
            reason or f"existing:{inst.state}",
            intent.trigger if intent else None,
            Snapshot(now=now),
        )
        return Outcome(inst, receipt, intent)

    def _intent(self, inst: Instance, trigger: str, reason: str, snap: Snapshot) -> FireIntent:
        return FireIntent(
            fire_key=inst.fire_key,
            instance_id=inst.id,
            generation=inst.generation,
            trigger=trigger,
            reason=reason,
            fired_at=snap.now,
            snapshot_generation=snap.generation,
            cursors=dict(inst.cursors),
        )

    def _receipt(
        self, inst: Instance, kind: str, reason: str, trigger: str | None, snap: Snapshot
    ) -> Receipt:
        return Receipt(
            instance_id=inst.id,
            generation=inst.generation,
            kind=kind,
            result=inst.result,
            state=inst.state,
            reason=reason,
            trigger=trigger,
            cursors=dict(inst.cursors),
            at=snap.now,
            snapshot_generation=snap.generation,
            clock=self.clock,
        )

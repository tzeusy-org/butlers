"""Store-level contract every ``ConditionStore`` must pass (memory and PostgreSQL).

Subclasses provide ``make_store`` and read-back hooks; the guards are exercised through the
public ``ConditionTx`` surface and the evaluator, never a store's internals.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid5

import pytest

from butlers.conditions import ConditionEvaluator, Instance, RegistrationRefused, Snapshot, State
from butlers.conditions.model import FireIntent
from butlers.conditions.service import StoreConflict
from tests.conditions.test_conditions import (
    DECISION,
    EXPIRES,
    GOAL,
    GRANT,
    NOW,
    NS,
    SUBJECT,
    decision_ev,
    register,
)


class StoreContract:
    def make_store(self):
        raise NotImplementedError

    async def instance(self, store, instance_id: str) -> Instance:
        async with store.transaction() as tx:
            found = await tx.get_instance(instance_id)
        assert found is not None
        return found

    async def intent_count(self, store) -> int:
        raise NotImplementedError

    async def instance_count(self, store) -> int:
        raise NotImplementedError

    async def receipt_reasons(self, store) -> list[str]:
        raise NotImplementedError

    async def seeded(self):
        store = self.make_store()
        await register(ConditionEvaluator(store), DECISION)
        return store, await self.instance(store, "c1")

    def intent_for(self, inst: Instance, tag: str = "a") -> FireIntent:
        return FireIntent(
            fire_key=inst.fire_key,
            instance_id=inst.id,
            generation=inst.generation,
            event_id=uuid5(NS, tag),
            reason="fired:event",
            fired_at=NOW,
            snapshot_generation=0,
            cursors={},
        )

    async def fire(self, store, inst: Instance) -> None:
        async with store.transaction() as tx:
            await tx.save_instance(replace(inst, state=State.FIRED, version=inst.version + 1), 0)

    async def test_insert_instance_reports_whether_it_inserted(self):
        store, inst = await self.seeded()
        async with store.transaction() as tx:
            stored, inserted = await tx.insert_instance(inst)
        assert not inserted and stored.id == "c1"
        async with store.transaction() as tx:
            _, inserted = await tx.insert_instance(replace(inst, id="c2"))
        assert inserted

    async def test_instance_round_trips_every_field(self):
        store, inst = await self.seeded()
        assert await self.instance(store, "c1") == inst
        assert inst.floor == {} and inst.leaves

    async def test_second_fire_intent_returns_the_original(self):
        store, inst = await self.seeded()
        await self.fire(store, inst)
        async with store.transaction() as tx:
            await tx.insert_fire_intent(self.intent_for(inst, "a"))
        async with store.transaction() as tx:
            second = await tx.insert_fire_intent(self.intent_for(inst, "b"))
        assert second.event_id == uuid5(NS, "a")
        assert await self.intent_count(store) == 1

    async def test_stale_version_save_raises_on_an_open_row(self):
        # The row stays open at version 1, so only the CAS (not the frozen-row guard) can
        # refuse. The stale writer targets version 2 = 1 + 1, which a store that merely
        # checks "version advances by one" would accept: only the CAS refuses it.
        store, inst = await self.seeded()
        async with store.transaction() as tx:
            await tx.save_instance(replace(inst, version=1), 0)
        with pytest.raises(StoreConflict):
            async with store.transaction() as tx:
                await tx.save_instance(replace(inst, version=2), 0)
        assert (await self.instance(store, "c1")).version == 1

    @pytest.mark.parametrize("which", ["instance", "goal", "empty"])
    async def test_oversized_or_empty_ids_refuse_without_an_outage(self, which):
        store = self.make_store()
        reports = []

        class Reporter:
            async def report(self, dependency, state, reason):
                reports.append(dependency)

        evaluator = ConditionEvaluator(store, reporter=Reporter())
        instance_id = {"instance": "i" * 129, "goal": "c1", "empty": ""}[which]
        goal_id = "g" * 129 if which == "goal" else GOAL
        with pytest.raises(RegistrationRefused):
            await evaluator.register(
                instance_id=instance_id,
                predicate=DECISION,
                grant=replace(GRANT, goal_id=goal_id),
                goal_id=goal_id,
                snapshot=Snapshot(now=NOW),
                expires_at=EXPIRES,
            )
        assert reports == [] and await self.instance_count(store) == 0

    async def test_longest_allowed_ids_register(self):
        store = self.make_store()
        out = await register(ConditionEvaluator(store), DECISION, instance_id="i" * 128)
        assert out.instance.id == "i" * 128
        assert await self.instance_count(store) == 1

    async def test_claim_is_first_consumer_wins(self):
        store, inst = await self.seeded()
        await self.fire(store, inst)
        async with store.transaction() as tx:
            await tx.insert_fire_intent(self.intent_for(inst))
        async with store.transaction() as tx:
            mine = await tx.claim_fire_intent(inst.fire_key, "a")
        async with store.transaction() as tx:
            again = await tx.claim_fire_intent(inst.fire_key, "a")
            other = await tx.claim_fire_intent(inst.fire_key, "b")
        assert mine is not None and mine.claimed_by == "a"
        assert again is not None and again.claimed_by == "a" and other is None

    async def test_rollback_discards_everything_in_the_transaction(self):
        store, inst = await self.seeded()
        with pytest.raises(RuntimeError):
            async with store.transaction() as tx:
                await tx.save_instance(replace(inst, state=State.FIRED, version=1), 0)
                await tx.insert_fire_intent(self.intent_for(inst))
                raise RuntimeError("crash before commit")
        assert (await self.instance(store, "c1")).state is State.OPEN
        assert await self.intent_count(store) == 0

    async def test_racing_evaluators_fire_once(self):
        store, _ = await self.seeded()
        evaluators = [ConditionEvaluator(store) for _ in range(8)]
        outs = await asyncio.gather(
            *(e.evaluate("c1", [decision_ev()], now=NOW) for e in evaluators)
        )
        assert await self.intent_count(store) == 1
        assert len({o.intent.fire_key for o in outs if o.intent}) == 1
        assert (await self.receipt_reasons(store)).count("fired:event") == 1

    # -- register retry evaluates the caller's fresher snapshot (bu-dhloi1.198) ----------------

    async def test_register_retry_fires_when_the_fresher_snapshot_is_true(self):
        store = self.make_store()
        evaluator = ConditionEvaluator(store)
        first = await register(evaluator, DECISION)
        assert first.intent is None
        fresh = Snapshot(now=NOW + timedelta(minutes=1), decisions={SUBJECT: "approved"})
        retry = await register(evaluator, DECISION, snapshot=fresh)
        assert retry.instance.state is State.FIRED and retry.intent is not None
        assert await self.intent_count(store) == 1
        assert "fired:registration" in await self.receipt_reasons(store)

    async def test_register_retry_never_double_fires(self):
        store = self.make_store()
        evaluator = ConditionEvaluator(store)
        await register(evaluator, DECISION)
        fresh = Snapshot(now=NOW + timedelta(minutes=1), decisions={SUBJECT: "approved"})
        one = await register(evaluator, DECISION, snapshot=fresh)
        two = await register(evaluator, DECISION, snapshot=fresh)
        assert two.intent == one.intent and await self.intent_count(store) == 1

    async def test_racing_register_retries_fire_once(self):
        store = self.make_store()
        await register(ConditionEvaluator(store), DECISION)
        fresh = Snapshot(now=NOW + timedelta(minutes=1), decisions={SUBJECT: "approved"})
        outs = await asyncio.gather(
            *(register(ConditionEvaluator(store), DECISION, snapshot=fresh) for _ in range(4))
        )
        assert await self.intent_count(store) == 1
        assert len({o.intent.fire_key for o in outs if o.intent}) == 1

    async def test_register_retry_with_a_stale_snapshot_never_downgrades(self):
        store = self.make_store()
        evaluator = ConditionEvaluator(store)
        await register(evaluator, DECISION)
        await evaluator.evaluate("c1", [decision_ev()], now=NOW)
        retry = await register(evaluator, DECISION, snapshot=Snapshot(now=NOW))
        assert retry.instance.state is State.FIRED and await self.intent_count(store) == 1

    async def test_register_retry_keeps_the_floor_and_boundary(self):
        store = self.make_store()
        evaluator = ConditionEvaluator(store)
        await register(evaluator, DECISION, cursors={"goal.decision": 7})
        before = await self.instance(store, "c1")
        fresh = Snapshot(now=NOW + timedelta(minutes=1), decisions={SUBJECT: "approved"})
        await register(evaluator, DECISION, snapshot=fresh, cursors={"goal.decision": 7})
        after = await self.instance(store, "c1")
        assert after.floor == before.floor == {"goal.decision": 7}
        assert after.predicate_digest == before.predicate_digest
        assert after.expires_at == before.expires_at
        with pytest.raises(RegistrationRefused):  # a different floor is a changed boundary
            await register(evaluator, DECISION, snapshot=fresh, cursors={"goal.decision": 99})

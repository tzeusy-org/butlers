"""Store-level contract every ``ConditionStore`` must pass (memory and PostgreSQL).

Subclasses provide ``make_store`` and read-back hooks; the guards are exercised through the
public ``ConditionTx`` surface and the evaluator, never a store's internals.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid5

import pytest

from butlers.conditions import ConditionEvaluator, Instance, State
from butlers.conditions.model import FireIntent
from butlers.conditions.service import StoreConflict
from tests.conditions.test_conditions import DECISION, NOW, NS, decision_ev, register


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

    async def test_stale_version_save_raises(self):
        store, inst = await self.seeded()
        await self.fire(store, inst)
        with pytest.raises(StoreConflict):
            async with store.transaction() as tx:
                await tx.save_instance(replace(inst, version=1), 0)

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

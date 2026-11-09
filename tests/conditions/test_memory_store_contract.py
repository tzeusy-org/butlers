"""The in-memory store against the shared contract, plus its optimistic interleaving."""

from __future__ import annotations

from dataclasses import replace

import pytest

from butlers.conditions import State
from butlers.conditions.memory import InMemoryConditionStore
from butlers.conditions.service import StoreConflict
from tests.conditions.store_contract import StoreContract


class TestInMemoryStoreContract(StoreContract):
    def make_store(self):
        return InMemoryConditionStore()

    async def intent_count(self, store) -> int:
        return len(store.intents)

    async def receipt_reasons(self, store) -> list[str]:
        return [r.reason for r in store.receipts]

    async def test_interleaved_transactions_lose_exactly_one(self):
        store, inst = await self.seeded()
        a, b = store.transaction(), store.transaction()
        ta, tb = await a.__aenter__(), await b.__aenter__()
        read_a, read_b = await ta.get_instance("c1"), await tb.get_instance("c1")
        assert read_a is not None and read_a.state is State.OPEN
        assert read_b is not None and read_b.state is State.OPEN
        fired = replace(inst, state=State.FIRED, version=1)
        await ta.save_instance(fired, 0)
        await tb.save_instance(fired, 0)
        await a.__aexit__(None, None, None)
        with pytest.raises(StoreConflict):
            await b.__aexit__(None, None, None)
        assert store.instances["c1"].version == 1

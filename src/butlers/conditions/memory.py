"""In-memory ``ConditionStore`` with the same transactional contract as the SQL store.

Optimistic: a transaction reads a snapshot, buffers its writes and validates them against the
live state at commit under a short lock. Every tx method yields to the event loop, so
concurrent evaluators genuinely interleave and the CAS/unique-key guards are what keep one
fire. A lost race raises ``StoreConflict`` (a SQL store would block on ``FOR UPDATE`` instead,
or map a serialization failure to the same error). A raised exception rolls back instance,
receipts and fire intent together. For consumers' tests and dark shadow runs; not durable.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import UUID

from butlers.conditions.model import FireIntent, Instance, Receipt
from butlers.conditions.service import StoreConflict


class _Tx:
    def __init__(self, store: InMemoryConditionStore) -> None:
        self.instances = dict(store.instances)
        self.intents = dict(store.intents)
        self.receipts: list[Receipt] = []
        self.created: dict[str, Instance] = {}
        self.saves: dict[str, tuple[Instance, int]] = {}
        self.new_intents: dict[UUID, FireIntent] = {}
        self.claims: dict[UUID, str] = {}

    async def get_instance(self, instance_id: str) -> Instance | None:
        await asyncio.sleep(0)
        return self.instances.get(instance_id)

    async def insert_instance(self, instance: Instance) -> tuple[Instance, bool]:
        await asyncio.sleep(0)
        existing = self.instances.get(instance.id)
        if existing is not None:
            return existing, False
        self.instances[instance.id] = instance
        self.created[instance.id] = instance
        return instance, True

    async def save_instance(self, instance: Instance, expected_version: int) -> None:
        await asyncio.sleep(0)
        current = self.instances.get(instance.id)
        if current is None or current.version != expected_version:
            raise StoreConflict(instance.id)
        self.instances[instance.id] = instance
        self.saves[instance.id] = (
            instance,
            self.saves.get(instance.id, (None, expected_version))[1],
        )

    async def append_receipt(self, receipt: Receipt) -> None:
        await asyncio.sleep(0)
        self.receipts.append(receipt)

    async def insert_fire_intent(self, intent: FireIntent) -> FireIntent:
        await asyncio.sleep(0)
        existing = self.intents.get(intent.fire_key)
        if existing is not None:
            return existing
        self.intents[intent.fire_key] = intent
        self.new_intents[intent.fire_key] = intent
        return intent

    async def get_fire_intent(self, fire_key: UUID) -> FireIntent | None:
        await asyncio.sleep(0)
        return self.intents.get(fire_key)

    async def claim_fire_intent(self, fire_key: UUID, consumer: str) -> FireIntent | None:
        await asyncio.sleep(0)
        intent = self.intents.get(fire_key)
        if intent is None:
            return None
        if intent.claimed_by is None:
            intent = replace(intent, claimed_by=consumer)
            self.intents[fire_key] = intent
            self.claims[fire_key] = consumer
        return intent if intent.claimed_by == consumer else None


class InMemoryConditionStore:
    def __init__(self) -> None:
        self.instances: dict[str, Instance] = {}
        self.receipts: list[Receipt] = []
        self.intents: dict[UUID, FireIntent] = {}
        self._commit_lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        tx = _Tx(self)
        yield tx
        async with self._commit_lock:
            self._commit(tx)

    def _commit(self, tx: _Tx) -> None:
        # Validate everything first so a conflict applies nothing (atomic commit).
        for iid in tx.created:
            if iid in self.instances:
                raise StoreConflict(iid)
        for iid, (_, expected) in tx.saves.items():
            live = self.instances.get(iid)
            base = tx.created.get(iid)
            if base is None and (live is None or live.version != expected):
                raise StoreConflict(iid)
        for key in tx.new_intents:
            if key in self.intents:
                raise StoreConflict(str(key))
        for key, consumer in tx.claims.items():
            live_intent = self.intents.get(key)
            if live_intent is not None and live_intent.claimed_by not in (None, consumer):
                raise StoreConflict(str(key))
        for iid, inst in tx.created.items():
            self.instances[iid] = inst
        for iid, (inst, _) in tx.saves.items():
            self.instances[iid] = inst
        for key, intent in tx.new_intents.items():
            self.intents[key] = intent
        for key, consumer in tx.claims.items():
            self.intents[key] = replace(self.intents[key], claimed_by=consumer)
        self.receipts.extend(tx.receipts)

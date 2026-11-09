"""In-memory ``ConditionStore`` with the same transactional contract as the SQL store.

Serialises transactions (one writer, like ``FOR UPDATE``), buffers writes and applies them on
commit, so a raised exception rolls back instance, receipts and fire intent together. For
consumers' tests and dark shadow runs; it is not durable.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import UUID

from butlers.conditions.model import FireIntent, Instance, Receipt


class StaleInstance(RuntimeError):
    pass


class _Tx:
    def __init__(self, store: InMemoryConditionStore) -> None:
        self.s = store
        self.instances = dict(store.instances)
        self.receipts = list(store.receipts)
        self.intents = dict(store.intents)

    async def get_instance(self, instance_id):
        return self.instances.get(instance_id)

    async def insert_instance(self, instance: Instance) -> Instance:
        existing = self.instances.get(instance.id)
        if existing is not None:
            return existing
        self.instances[instance.id] = instance
        return instance

    async def save_instance(self, instance: Instance, expected_version: int) -> None:
        current = self.instances.get(instance.id)
        if current is None or current.version != expected_version:
            raise StaleInstance(instance.id)
        self.instances[instance.id] = instance

    async def append_receipt(self, receipt: Receipt) -> None:
        self.receipts.append(receipt)

    async def insert_fire_intent(self, intent: FireIntent) -> FireIntent:
        return self.intents.setdefault(intent.fire_key, intent)

    async def get_fire_intent(self, fire_key: UUID) -> FireIntent | None:
        return self.intents.get(fire_key)

    async def claim_fire_intent(self, fire_key: UUID, consumer: str) -> FireIntent | None:
        intent = self.intents.get(fire_key)
        if intent is None:
            return None
        if intent.claimed_by is None:
            intent = replace(intent, claimed_by=consumer)
            self.intents[fire_key] = intent
        return intent if intent.claimed_by == consumer else None


class InMemoryConditionStore:
    def __init__(self) -> None:
        self.instances: dict[str, Instance] = {}
        self.receipts: list[Receipt] = []
        self.intents: dict[UUID, FireIntent] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        async with self._lock:
            tx = _Tx(self)
            yield tx
            self.instances, self.receipts, self.intents = tx.instances, tx.receipts, tx.intents

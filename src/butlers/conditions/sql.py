"""PostgreSQL ``ConditionStore`` over the ``public.condition_*`` tables (core_323).

Run it on a pool whose role is ``butler_switchboard_rw``: row-level security admits no other
runtime role. One transaction wraps instance, cursors, receipts and fire intent, so they
commit or roll back together. ``get_instance`` takes ``FOR UPDATE`` and ``save_instance`` is a
version CAS, so racing evaluators serialise on the row and the loser re-reads the winner's
fired state; unique ``fire_key`` plus ``ON CONFLICT DO NOTHING`` make a second intent return the
original.

Error mapping: a serialisation failure or deadlock raises ``StoreConflict`` (the service
retries); a lost CAS raises ``StoreConflict``; any other driver or connection failure raises
``ConnectionError``, which the service reports as ``system.dep.conditions`` and surfaces as
``ConditionsUnavailable``. Nothing is advanced on failure.

JSON is exchanged as text (``::text::jsonb`` / ``::text``) so the store works with or without
the repository's jsonb codec on the connection.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any
from uuid import UUID

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from butlers.conditions.model import FireIntent, Instance, Receipt, State, Tri
from butlers.conditions.service import RegistrationRefused, StoreConflict
from butlers.core.goal_labels import Label

# PostgreSQL's generated names for the inline id/goal_id length CHECKs in core_323.
_ID_LENGTH_CHECKS = frozenset({"condition_instances_id_check", "condition_instances_goal_id_check"})

_INSTANCE_COLUMNS = (
    "id, goal_id, predicate::text AS predicate, predicate_digest, grant_digest, "
    "source_boundary, starts_at, expires_at, generation, state, result, "
    "leaves::text AS leaves, floor::text AS floor, version, "
    "label_version, label_instr, label_data, label_sens"
)
_INTENT_COLUMNS = (
    "fire_key, instance_id, generation, event_id, reason, fired_at, snapshot_generation, "
    "cursors::text AS cursors, claimed_by, label_version, label_instr, label_data, label_sens"
)


def _text(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), default=str)


def _label(row: asyncpg.Record) -> Label:
    return Label.from_row(
        row["label_version"], row["label_instr"], row["label_data"], row["label_sens"]
    )


def _intent(row: asyncpg.Record) -> FireIntent:
    return FireIntent(
        fire_key=row["fire_key"],
        instance_id=row["instance_id"],
        generation=row["generation"],
        event_id=row["event_id"],
        reason=row["reason"],
        fired_at=row["fired_at"],
        snapshot_generation=row["snapshot_generation"],
        cursors=json.loads(row["cursors"]),
        claimed_by=row["claimed_by"],
        label=_label(row),
    )


class _SqlTx:
    def __init__(self, connection: asyncpg.Connection | PoolConnectionProxy) -> None:
        self.c = connection

    async def _cursors(self, instance_id: str) -> dict[str, int]:
        rows = await self.c.fetch(
            "SELECT source, position FROM public.condition_cursors WHERE instance_id = $1",
            instance_id,
        )
        return {r["source"]: r["position"] for r in rows}

    async def _instance(self, row: asyncpg.Record) -> Instance:
        return Instance(
            id=row["id"],
            predicate=json.loads(row["predicate"]),
            predicate_digest=row["predicate_digest"],
            goal_id=row["goal_id"],
            grant_digest=row["grant_digest"],
            source_boundary=tuple(row["source_boundary"]),
            starts_at=row["starts_at"],
            expires_at=row["expires_at"],
            generation=row["generation"],
            state=State(row["state"]),
            result=Tri(row["result"]),
            leaves={k: Tri(v) for k, v in json.loads(row["leaves"]).items()},
            cursors=await self._cursors(row["id"]),
            floor=json.loads(row["floor"]),
            version=row["version"],
            label=_label(row),
        )

    async def get_instance(self, instance_id: str) -> Instance | None:
        row = await self.c.fetchrow(
            f"SELECT {_INSTANCE_COLUMNS} FROM public.condition_instances WHERE id = $1 FOR UPDATE",
            instance_id,
        )
        return None if row is None else await self._instance(row)

    async def insert_instance(self, instance: Instance) -> tuple[Instance, bool]:
        try:
            inserted = await self.c.fetchval(
                "INSERT INTO public.condition_instances (id, goal_id, predicate, "
                "predicate_digest, grant_digest, source_boundary, starts_at, expires_at, "
                "generation, state, result, leaves, floor, version, label_version, label_instr, "
                "label_data, label_sens) VALUES ($1, $2, $3::text::jsonb, $4, $5, $6, $7, $8, "
                "$9, $10, $11, $12::text::jsonb, $13::text::jsonb, $14, $15, $16, $17, $18) "
                "ON CONFLICT (id) DO NOTHING RETURNING id",
                instance.id,
                instance.goal_id,
                _text(instance.predicate),
                instance.predicate_digest,
                instance.grant_digest,
                list(instance.source_boundary),
                instance.starts_at,
                instance.expires_at,
                instance.generation,
                instance.state.value,
                instance.result.value,
                _text({k: v.value for k, v in instance.leaves.items()}),
                _text(instance.floor),
                instance.version,
                *instance.label.to_row(),
            )
        except asyncpg.CheckViolationError as exc:
            # Only the register path's own id-length CHECKs are a refused row; any other
            # constraint trip is a bug and stays on the outage path.
            constraint = getattr(exc, "constraint_name", None)
            if constraint in _ID_LENGTH_CHECKS:
                raise RegistrationRefused(f"condition store refused the row: {constraint}") from exc
            raise
        if inserted is None:
            existing = await self.get_instance(instance.id)
            if existing is None:  # a concurrent rollback removed the conflicting row
                raise StoreConflict(instance.id)
            return existing, False
        await self._write_cursors(instance.id, instance.cursors)
        return instance, True

    async def _write_cursors(self, instance_id: str, cursors: Mapping[str, int]) -> None:
        for source, position in sorted(cursors.items()):
            await self.c.execute(
                "INSERT INTO public.condition_cursors (instance_id, source, position) "
                "VALUES ($1, $2, $3) ON CONFLICT (instance_id, source) "
                "DO UPDATE SET position = GREATEST(excluded.position, "
                "public.condition_cursors.position)",
                instance_id,
                source,
                position,
            )

    async def save_instance(self, instance: Instance, expected_version: int) -> None:
        updated = await self.c.fetchval(
            "UPDATE public.condition_instances SET state = $2, result = $3, "
            "leaves = $4::text::jsonb, version = $5 WHERE id = $1 AND version = $6 "
            "RETURNING id",
            instance.id,
            instance.state.value,
            instance.result.value,
            _text({k: v.value for k, v in instance.leaves.items()}),
            instance.version,
            expected_version,
        )
        if updated is None:
            raise StoreConflict(instance.id)
        await self._write_cursors(instance.id, instance.cursors)

    async def append_receipt(self, receipt: Receipt) -> None:
        clock = None
        if receipt.clock is not None:
            clock = _text({**asdict(receipt.clock), "observed_at": receipt.clock.observed_at})
        await self.c.execute(
            "INSERT INTO public.condition_receipts (instance_id, generation, kind, result, "
            "state, reason, trigger_event_id, cursors, at, snapshot_generation, clock, "
            "label_version, label_instr, label_data, label_sens) VALUES ($1, $2, $3, $4, $5, "
            "$6, $7, $8::text::jsonb, $9, $10, $11::text::jsonb, $12, $13, $14, $15)",
            receipt.instance_id,
            receipt.generation,
            receipt.kind,
            receipt.result.value,
            receipt.state.value,
            receipt.reason,
            UUID(receipt.trigger) if receipt.trigger else None,
            _text(receipt.cursors),
            receipt.at,
            receipt.snapshot_generation,
            clock,
            *receipt.label.to_row(),
        )

    async def insert_fire_intent(self, intent: FireIntent) -> FireIntent:
        await self.c.execute(
            "INSERT INTO public.condition_fire_intents (fire_key, instance_id, generation, "
            "event_id, reason, fired_at, snapshot_generation, cursors, label_version, label_instr, "
            "label_data, label_sens) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::text::jsonb, $9, "
            "$10, $11, $12) ON CONFLICT DO NOTHING",
            intent.fire_key,
            intent.instance_id,
            intent.generation,
            intent.event_id,
            intent.reason,
            intent.fired_at,
            intent.snapshot_generation,
            _text(intent.cursors),
            *intent.label.to_row(),
        )
        stored = await self.get_fire_intent(intent.fire_key)
        if stored is None:
            raise StoreConflict(str(intent.fire_key))
        return stored

    async def get_fire_intent(self, fire_key: UUID) -> FireIntent | None:
        row = await self.c.fetchrow(
            f"SELECT {_INTENT_COLUMNS} FROM public.condition_fire_intents WHERE fire_key = $1",
            fire_key,
        )
        return None if row is None else _intent(row)

    async def claim_fire_intent(self, fire_key: UUID, consumer: str) -> FireIntent | None:
        row = await self.c.fetchrow(
            "UPDATE public.condition_fire_intents SET claimed_by = $2, "
            "claimed_at = clock_timestamp() WHERE fire_key = $1 AND claimed_by IS NULL "
            f"RETURNING {_INTENT_COLUMNS}",
            fire_key,
            consumer,
        )
        intent = _intent(row) if row is not None else await self.get_fire_intent(fire_key)
        return intent if intent is not None and intent.claimed_by == consumer else None


class PostgresConditionStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_SqlTx]:
        try:
            async with self._pool.acquire() as connection, connection.transaction():
                yield _SqlTx(connection)
        except (asyncpg.SerializationError, asyncpg.DeadlockDetectedError) as exc:
            raise StoreConflict(str(exc)) from exc
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError) as exc:
            if isinstance(exc, ConnectionError):
                raise
            raise ConnectionError(f"conditions store unavailable: {type(exc).__name__}") from exc

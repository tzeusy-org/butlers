"""REQ-condition-evaluation-002 over migrated PostgreSQL: one fire, recoverable, fenced.

Runs the shared store contract plus the cases only a real database can prove: FOR UPDATE
serialisation across two connections, crash/restart cursor replay, the role fence that
survives an init-db re-grant, trigger-enforced immutability, and driver-error mapping.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest

from butlers.conditions import (
    ConditionEvaluator,
    ConditionsUnavailable,
    FireIntent,
    RegistrationRefused,
    State,
)
from butlers.conditions.sql import PostgresConditionStore
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.conditions.store_contract import StoreContract
from tests.conditions.test_conditions import (
    DECISION,
    NOW,
    decision_ev,
    register,
)

pytestmark = [pytest.mark.integration, pytest.mark.db, pytest.mark.asyncio(loop_scope="session")]

TABLES = (
    "condition_fire_intents",
    "condition_receipts",
    "condition_cursors",
    "condition_instances",
)


@pytest.fixture(scope="module")
def conditions_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core"],
        schemas={"core": "switchboard"},
    )


async def _as_switchboard(connection):
    await connection.execute("SET ROLE butler_switchboard_rw")


@pytest.fixture
async def pools(conditions_url, postgres_container):
    # The reset lifts the immutability guards, so it needs the privileged control login.
    name = urlparse(conditions_url).path.lstrip("/")
    admin_url = migration_bootstrap_db_url(postgres_container, name).replace("+psycopg2", "")
    admin = await asyncpg.connect(admin_url)
    try:
        async with admin.transaction():
            for table in TABLES:
                await admin.execute(f"ALTER TABLE public.{table} DISABLE TRIGGER ALL")
            await admin.execute("TRUNCATE " + ", ".join(f"public.{t}" for t in TABLES))
            for table in TABLES:
                await admin.execute(f"ALTER TABLE public.{table} ENABLE TRIGGER ALL")
    finally:
        await admin.close()
    async with asyncpg.create_pool(conditions_url, min_size=1, max_size=3) as owner:
        async with asyncpg.create_pool(
            conditions_url, setup=_as_switchboard, min_size=1, max_size=8
        ) as runtime:
            yield owner, runtime


class TestPostgresStoreContract(StoreContract):
    @pytest.fixture(autouse=True)
    def _bind(self, pools):
        self.owner, self.runtime = pools

    def make_store(self):
        return PostgresConditionStore(self.runtime)

    async def intent_count(self, store) -> int:
        return await self.owner.fetchval("SELECT count(*) FROM public.condition_fire_intents")

    async def instance_count(self, store) -> int:
        return await self.owner.fetchval("SELECT count(*) FROM public.condition_instances")

    async def test_integrity_violation_is_a_refusal_not_an_outage(self):
        # Backstop: a row the SQL CHECKs refuse that service validation did not catch.
        store, inst = await self.seeded()
        with pytest.raises(RegistrationRefused):
            async with store.transaction() as tx:
                await tx.insert_instance(replace(inst, id="i" * 129))

    async def test_intent_fence_trip_is_an_outage_not_a_refusal(self):
        # A 23514 from the core_323 intent-fence trigger is a bug, not a refused registration.
        store, inst = await self.seeded()
        intent = FireIntent(
            fire_key=uuid4(),
            instance_id=inst.id,
            generation=inst.generation,
            event_id=uuid4(),
            reason="x",
            fired_at=NOW,
            snapshot_generation=0,
            cursors={},
        )
        with pytest.raises(ConnectionError) as raised:
            async with store.transaction() as tx:
                await tx.insert_fire_intent(intent)
        assert not isinstance(raised.value, RegistrationRefused)

    async def receipt_reasons(self, store) -> list[str]:
        rows = await self.owner.fetch("SELECT reason FROM public.condition_receipts ORDER BY id")
        return [r["reason"] for r in rows]

    async def test_for_update_serialises_two_connections(self):
        store, inst = await self.seeded()
        gate, order = asyncio.Event(), []

        async def first():
            async with store.transaction() as tx:
                read = await tx.get_instance("c1")
                assert read is not None
                order.append("a-read")
                gate.set()
                await asyncio.sleep(0.3)
                await tx.save_instance(replace(read, state=State.FIRED, version=1), 0)
                order.append("a-save")

        async def second():
            await gate.wait()
            async with store.transaction() as tx:
                read = await tx.get_instance("c1")
                order.append("b-read")
                return read

        _, seen = await asyncio.gather(first(), second())
        assert order == ["a-read", "a-save", "b-read"]
        assert seen is not None and seen.state is State.FIRED and seen.version == 1

    async def test_crash_after_commit_replays_the_same_intent_after_restart(self):
        store, _ = await self.seeded()
        first = await ConditionEvaluator(store).evaluate("c1", [decision_ev()], now=NOW)
        # The process dies before the runner acknowledges: new store, new evaluator.
        restarted = ConditionEvaluator(PostgresConditionStore(self.runtime))
        replay = await restarted.evaluate("c1", [decision_ev()], now=NOW)
        assert first.intent is not None and replay.intent == first.intent
        claim = await restarted.consume_fire("c1", "runner")
        assert claim is not None and claim.event_id == first.intent.event_id
        assert await self.intent_count(store) == 1

    async def test_cursor_is_persisted_as_a_high_water_mark(self):
        store, _ = await self.seeded()
        evaluator = ConditionEvaluator(store)
        await evaluator.evaluate("c1", [decision_ev(7, outcome="rejected")], now=NOW)
        await evaluator.evaluate("c1", [decision_ev(4, outcome="rejected")], now=NOW)
        rows = await self.owner.fetch("SELECT source, position FROM public.condition_cursors")
        assert [(r["source"], r["position"]) for r in rows] == [("goal.decision", 7)]
        loaded = await self.instance(store, "c1")
        assert dict(loaded.cursors) == {"goal.decision": 7}

    async def test_registration_with_replay_floor_persists_it(self):
        store = self.make_store()
        evaluator = ConditionEvaluator(store)
        await register(evaluator, DECISION, instance_id="g2", generation=2, cursors={"x": 9})
        row = await self.owner.fetchrow(
            "SELECT floor::text AS floor, generation FROM public.condition_instances WHERE id='g2'"
        )
        assert row is not None and json.loads(row["floor"]) == {"x": 9} and row["generation"] == 2

    async def test_concurrent_registration_inserts_once(self):
        store = self.make_store()
        outs = await asyncio.gather(
            *(register(ConditionEvaluator(store), DECISION) for _ in range(6))
        )
        assert len({o.instance.id for o in outs}) == 1
        assert await self.owner.fetchval("SELECT count(*) FROM public.condition_instances") == 1
        reasons = await self.receipt_reasons(store)
        assert reasons.count("pending") == 1

    async def test_driver_failure_is_unavailable_and_reported(self):
        calls = []

        class Reporter:
            async def report(self, dependency, state, reason):
                calls.append((dependency, state))

        class DeadPool:
            def acquire(self):
                raise asyncpg.InterfaceError("pool is closed")

        evaluator = ConditionEvaluator(PostgresConditionStore(DeadPool()), reporter=Reporter())  # type: ignore[arg-type]
        with pytest.raises(ConditionsUnavailable):
            await evaluator.evaluate("c1", [decision_ev()], now=NOW)
        assert calls == [("system.dep.conditions", "down")]


class TestRoleFenceAndGuards:
    @pytest.fixture(autouse=True)
    def _bind(self, pools):
        self.owner, self.runtime = pools

    async def _seed(self):
        store = PostgresConditionStore(self.runtime)
        await register(ConditionEvaluator(store), DECISION)
        return store

    async def test_other_runtime_roles_are_fenced_even_after_a_broad_regrant(self):
        await self._seed()
        async with self.owner.acquire() as connection:
            tx = connection.transaction()
            await tx.start()
            try:
                # What an init-db.sql replay does: blanket DML on every public table.
                await connection.execute(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON "
                    + ", ".join(f"public.{t}" for t in TABLES)
                    + " TO butler_general_rw"
                )
                await connection.execute("SET LOCAL ROLE butler_general_rw")
                seen = await connection.fetchval("SELECT count(*) FROM public.condition_instances")
                assert seen == 0
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(
                        "INSERT INTO public.condition_cursors (instance_id, source, position) "
                        "VALUES ('c1', 'x', 1)"
                    )
            finally:
                await tx.rollback()

    async def test_domain_role_holds_no_privilege_on_a_fresh_migration(self):
        rows = await self.owner.fetch(
            "SELECT t, has_table_privilege('butler_general_rw', 'public.' || t, "
            "'SELECT,INSERT,UPDATE,DELETE') AS domain_priv, "
            "has_table_privilege('connector_writer', 'public.' || t, "
            "'SELECT,INSERT,UPDATE,DELETE') AS connector_priv, "
            "has_table_privilege('butler_switchboard_rw', 'public.' || t, 'SELECT') AS board "
            "FROM unnest($1::text[]) t",
            list(TABLES),
        )
        assert len(rows) == len(TABLES)
        assert all(r["board"] for r in rows)
        assert not any(r["domain_priv"] or r["connector_priv"] for r in rows)

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE public.condition_instances SET predicate = '{}'::jsonb",
            "UPDATE public.condition_instances SET floor = '{\"x\": 1}'::jsonb",
            "UPDATE public.condition_instances SET expires_at = expires_at + interval '1 day'",
            "UPDATE public.condition_instances SET version = version + 5",
            "DELETE FROM public.condition_instances",
            "TRUNCATE public.condition_instances CASCADE",
            "DELETE FROM public.condition_receipts",
            "UPDATE public.condition_receipts SET reason = 'edited'",
            "TRUNCATE public.condition_receipts",
        ],
    )
    async def test_durable_tables_refuse_mutation_even_for_the_owner(self, statement):
        store = await self._seed()
        async with store.transaction() as tx:
            await tx.append_receipt(
                (await ConditionEvaluator(store).evaluate("c1", [], now=NOW)).receipt
            )
        with pytest.raises(asyncpg.PostgresError):
            await self.owner.execute(statement)

    async def test_closed_instance_is_frozen_and_cursor_cannot_move_back(self):
        store = await self._seed()
        evaluator = ConditionEvaluator(store)
        await evaluator.evaluate("c1", [decision_ev(5, outcome="rejected")], now=NOW)
        with pytest.raises(asyncpg.PostgresError):
            await self.owner.execute("UPDATE public.condition_cursors SET position = 1")
        await evaluator.evaluate("c1", [decision_ev(6)], now=NOW)
        with pytest.raises(asyncpg.PostgresError):
            await self.owner.execute(
                "UPDATE public.condition_instances SET state = 'open', version = version + 1"
            )

    async def test_fire_intent_requires_its_fired_instance(self):
        await self._seed()
        with pytest.raises(asyncpg.PostgresError):
            await self.owner.execute(
                "INSERT INTO public.condition_fire_intents (fire_key, instance_id, generation, "
                "event_id, reason, fired_at, cursors) VALUES (gen_random_uuid(), 'c1', 1, "
                "gen_random_uuid(), 'x', now(), '{}'::jsonb)"
            )

    async def test_fire_intent_claim_is_set_once(self):
        store = await self._seed()
        await ConditionEvaluator(store).evaluate("c1", [decision_ev()], now=NOW)
        assert await ConditionEvaluator(store).consume_fire("c1", "a") is not None
        with pytest.raises(asyncpg.PostgresError):
            await self.owner.execute("UPDATE public.condition_fire_intents SET claimed_by = 'b'")
        with pytest.raises(asyncpg.PostgresError):
            await self.owner.execute("UPDATE public.condition_fire_intents SET reason = 'edited'")

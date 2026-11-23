"""REQ-owner-watches-003/004 over migrated PostgreSQL: durable, fenced, capped.

Runs the shared store contract plus what only a real database proves: the role fence that
survives an init-db re-grant, trigger-enforced immutability and the state rules, the cap
backstop below the Python store, and the per-schema two-chain install.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest

from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from butlers.watches import WatchState
from butlers.watches.sql import PostgresWatchStore
from tests.watches.store_contract import WatchStoreContract, make_watch

pytestmark = [pytest.mark.integration, pytest.mark.db, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def watches_url(postgres_container):
    # Two schemas: the core chain runs once per butler schema over the shared table.
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core"],
        schemas={"core": "switchboard"},
    )


async def _as_switchboard(connection):
    await connection.execute("SET ROLE butler_switchboard_rw")


@pytest.fixture
async def pools(watches_url, postgres_container):
    name = urlparse(watches_url).path.lstrip("/")
    admin_url = migration_bootstrap_db_url(postgres_container, name).replace("+psycopg2", "")
    admin = await asyncpg.connect(admin_url)
    try:
        async with admin.transaction():
            await admin.execute("ALTER TABLE public.owner_watches DISABLE TRIGGER ALL")
            await admin.execute("TRUNCATE public.owner_watches")
            await admin.execute("ALTER TABLE public.owner_watches ENABLE TRIGGER ALL")
    finally:
        await admin.close()
    async with asyncpg.create_pool(watches_url, min_size=1, max_size=3) as owner:
        async with asyncpg.create_pool(
            watches_url, setup=_as_switchboard, min_size=1, max_size=8
        ) as runtime:
            yield owner, runtime


@pytest.fixture
async def lock_pool(watches_url):
    async with asyncpg.create_pool(
        watches_url, setup=_as_switchboard, min_size=1, max_size=4
    ) as locks:
        yield locks


class TestPostgresWatchStoreContract(WatchStoreContract):
    @pytest.fixture(autouse=True)
    def _bind(self, pools, lock_pool):
        self.owner, self.runtime = pools
        self.locks = lock_pool

    def make_store(self):
        return PostgresWatchStore(self.runtime, lock_pool=self.locks)


class TestLockPoolIsRequired:
    @pytest.fixture(autouse=True)
    def _bind(self, pools):
        self.owner, self.runtime = pools

    async def test_it_cannot_be_omitted(self):
        with pytest.raises(TypeError):
            PostgresWatchStore(self.runtime)  # type: ignore[call-arg]

    async def test_it_cannot_be_the_store_pool(self):
        with pytest.raises(ValueError, match="pool of its own"):
            PostgresWatchStore(self.runtime, lock_pool=self.runtime)


class TestCrossProcessSerialization:
    @pytest.fixture(autouse=True)
    def _bind(self, pools, lock_pool):
        self.owner, self.runtime = pools
        self.locks = lock_pool

    async def test_two_store_instances_exclude_each_other_through_the_advisory_lock(self):
        first = PostgresWatchStore(self.runtime, lock_pool=self.locks)
        second = PostgresWatchStore(self.runtime, lock_pool=self.locks)
        inside, overlap = 0, 0

        async def hold(store):
            nonlocal inside, overlap
            async with store.serialized("w1"):
                inside += 1
                overlap = max(overlap, inside)
                await asyncio.sleep(0.05)
                inside -= 1

        await asyncio.gather(hold(first), hold(second), hold(first), hold(second))
        assert overlap == 1


class TestNoPoolDeadlock:
    @pytest.fixture(autouse=True)
    def _bind(self, pools, watches_url):
        self.owner, self.runtime = pools
        self.url = watches_url

    async def test_a_transition_does_not_deadlock_with_every_pool_at_size_one(self):
        from datetime import UTC, datetime
        from uuid import UUID, uuid5

        from butlers.conditions import ConditionEvaluator, Grant, Snapshot
        from butlers.conditions.sql import PostgresConditionStore
        from butlers.watches import OwnerApproval, WatchService

        ns = UUID("22222222-2222-2222-2222-222222222222")
        routine = str(uuid5(ns, "routine"))
        source = "perception:expect.window"

        def grant_for(watch_id):
            return Grant(
                goal_id=watch_id, sources=frozenset({source}), subjects=frozenset({routine})
            )

        async def single_connection_pool():
            return await asyncpg.create_pool(
                self.url, setup=_as_switchboard, min_size=1, max_size=1
            )

        store_pool, lock_pool, condition_pool = (
            await single_connection_pool(),
            await single_connection_pool(),
            await single_connection_pool(),
        )
        try:
            service = WatchService(
                PostgresWatchStore(store_pool, lock_pool=lock_pool),
                ConditionEvaluator(PostgresConditionStore(condition_pool)),
                grant_for,
            )
            now = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
            card = service.propose_create(
                {"predicate": {"tag": "routine_deviation", "routine_id": routine}}, now=now
            )

            async def run():
                approval = OwnerApproval(card_digest=card.digest, origin="owner")
                watch = await service.approve(card, approval, Snapshot(now=now))
                return await service.pause(watch.id, origin="owner", now=now)

            paused = await asyncio.wait_for(run(), timeout=30)
            assert paused.state is WatchState.PAUSED
        finally:
            for p in (store_pool, lock_pool, condition_pool):
                await p.close()


class _SlowLookup:
    """A pool whose connections pause after the duplicate lookup, forcing the race window open."""

    def __init__(self, pool):
        self._pool = pool

    def __getattr__(self, name):
        return getattr(self._pool, name)

    @asynccontextmanager
    async def acquire(self):
        async with self._pool.acquire() as connection:
            yield _SlowConnection(connection)


class _SlowConnection:
    def __init__(self, connection):
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    async def fetchval(self, query, *args):
        result = await self._connection.fetchval(query, *args)
        if query.startswith("SELECT id FROM insight_candidates WHERE dedup_key"):
            await asyncio.sleep(0.3)
        return result


class TestInsightDeliveryOnPostgres:
    """The broker adapter against the real insight table: one proposal per fire, and withdrawal."""

    @pytest.fixture(autouse=True)
    def _bind(self, watches_url):
        self.url = watches_url

    @pytest.fixture
    async def broker_pool(self):
        from butlers.db import register_jsonb_codec
        from butlers.tools.switchboard.insight.broker import create_insight_tables

        schema = f"watch_broker_{uuid4().hex[:8]}"
        admin = await asyncpg.connect(self.url)
        try:
            await admin.execute(f"CREATE SCHEMA {schema}")
        finally:
            await admin.close()
        async with asyncpg.create_pool(
            self.url,
            init=register_jsonb_codec,
            server_settings={"search_path": schema},
            min_size=1,
            max_size=4,
        ) as pool:
            await create_insight_tables(pool)
            yield pool

    def notice(self, key="owner_watch:race:g1"):
        from butlers.watches.runner import WatchNotice

        return WatchNotice(
            "11111111-1111-1111-1111-111111111111", 1, "digest", key, datetime.now(UTC)
        )

    async def test_two_concurrent_deliveries_of_one_fire_queue_one_candidate(self, broker_pool):
        from butlers.watches.delivery import InsightDelivery

        deliver = InsightDelivery(_SlowLookup(broker_pool))
        notice = self.notice()
        await asyncio.wait_for(asyncio.gather(*(deliver(notice) for _ in range(4))), timeout=30)
        count = await broker_pool.fetchval(
            "SELECT count(*) FROM insight_candidates WHERE dedup_key = $1", notice.dedup_key
        )
        assert count == 1

    async def test_withdraw_takes_back_only_pending_unclaimed_candidates(self, broker_pool):
        from butlers.watches.delivery import InsightDelivery

        deliver = InsightDelivery(broker_pool)
        watch_id = "11111111-1111-1111-1111-111111111111"
        await deliver(self.notice(f"owner_watch:{watch_id}:g1"))
        await deliver(self.notice(f"owner_watch:{watch_id}:g2"))
        await broker_pool.execute(
            "UPDATE insight_candidates SET claim_id = gen_random_uuid() WHERE dedup_key LIKE $1",
            f"owner_watch:{watch_id}:g2",
        )
        await deliver.withdraw(type("W", (), {"id": watch_id})())  # only .id is read
        rows = {
            r["dedup_key"]: r["status"]
            for r in await broker_pool.fetch("SELECT dedup_key, status FROM insight_candidates")
        }
        assert rows == {
            f"owner_watch:{watch_id}:g1": "withdrawn",
            f"owner_watch:{watch_id}:g2": "pending",  # claimed for sending: left alone
        }


class TestTableGuards:
    @pytest.fixture(autouse=True)
    def _bind(self, pools, lock_pool):
        self.owner, self.runtime = pools
        self.locks = lock_pool

    async def seed(self, watch_id="w1", **changes):
        store = PostgresWatchStore(self.runtime, lock_pool=self.locks)
        return await store.write(make_watch(watch_id, **changes), expected_version=None, cap=50)

    @pytest.mark.parametrize(
        "statement",
        [
            'UPDATE public.owner_watches SET predicate = \'{"tag":"all","children":[]}\'',
            "UPDATE public.owner_watches SET sources = '{other}', version = version + 1",
            "UPDATE public.owner_watches SET categories = '{}', version = version + 1",
            "UPDATE public.owner_watches SET grant_digest = repeat('a', 64), version = version + 1",
            "UPDATE public.owner_watches SET created_at = created_at - interval '1 day', "
            "version = version + 1",
            "DELETE FROM public.owner_watches",
            "TRUNCATE public.owner_watches",
        ],
    )
    async def test_the_boundary_is_immutable_and_rows_are_never_deleted(self, statement):
        await self.seed()
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with self.owner.acquire() as connection:
                await connection.execute(statement)

    async def test_the_grant_binding_moves_only_on_a_resume(self):
        rebind = "grant_digest = repeat('b', 64), version = version + 1"
        await self.seed(state=WatchState.PAUSED)
        for statement in (
            f"UPDATE public.owner_watches SET {rebind}",  # still paused
            f"UPDATE public.owner_watches SET {rebind}, state = 'active'",  # same generation
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await self.owner.execute(statement)
        await self.owner.execute(
            f"UPDATE public.owner_watches SET {rebind}, state = 'active', generation = generation + 1"
        )
        assert (
            await self.owner.fetchval("SELECT grant_digest FROM public.owner_watches") == "b" * 64
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):  # an active watch cannot rebind
            await self.owner.execute(
                "UPDATE public.owner_watches SET grant_digest = repeat('c', 64), version = version + 1"
            )

    async def test_version_must_advance_by_exactly_one(self):
        await self.seed()
        with pytest.raises(asyncpg.SerializationError):
            await self.owner.execute("UPDATE public.owner_watches SET version = 5")

    @pytest.mark.parametrize("state", ["expired", "deleted"])
    async def test_a_closed_watch_is_frozen(self, state):
        await self.seed(state=WatchState(state))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await self.owner.execute(
                "UPDATE public.owner_watches SET delivery = 'right_away', version = version + 1"
            )

    async def test_expiry_moves_forward_only_with_a_new_generation(self):
        await self.seed()
        for statement in (
            "UPDATE public.owner_watches SET expires_at = expires_at - interval '1 day', "
            "version = version + 1",
            "UPDATE public.owner_watches SET expires_at = expires_at + interval '1 day', "
            "version = version + 1",
            "UPDATE public.owner_watches SET generation = generation + 2, version = version + 1",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await self.owner.execute(statement)
        await self.owner.execute(
            "UPDATE public.owner_watches SET expires_at = expires_at + interval '1 day', "
            "generation = generation + 1, version = version + 1"
        )

    async def test_the_cap_holds_below_the_python_store(self):
        async with self.runtime.acquire() as connection:
            for index in range(50):
                watch = make_watch(f"w{index}")
                await connection.execute(
                    "INSERT INTO public.owner_watches (id, version, generation, predicate, "
                    "predicate_digest, grant_digest, sources, categories, delivery, state, "
                    "created_at, expires_at, approval_digest) VALUES ($1, 1, 1, "
                    '\'{"tag":"bundle","bundle_id":"x"}\'::jsonb, $2, $2, \'{a}\', \'{}\', '
                    "'digest', 'active', $3, $4, $2)",
                    watch.id,
                    watch.predicate_digest,
                    watch.created_at,
                    watch.expires_at,
                )
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute(
                    "INSERT INTO public.owner_watches (id, version, generation, predicate, "
                    "predicate_digest, grant_digest, sources, categories, delivery, state, "
                    "created_at, expires_at, approval_digest) VALUES ('w51', 1, 1, "
                    '\'{"tag":"bundle","bundle_id":"x"}\'::jsonb, repeat(\'a\', 64), '
                    "repeat('a', 64), '{a}', '{}', 'digest', 'active', now(), "
                    "now() + interval '1 day', repeat('a', 64))"
                )

    async def test_only_the_switchboard_role_may_touch_the_table(self):
        await self.seed()
        async with self.owner.acquire() as connection:
            await connection.execute("SET ROLE butler_general_rw")
            try:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.fetch("SELECT id FROM public.owner_watches")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute("DELETE FROM public.owner_watches")
            finally:
                await connection.execute("RESET ROLE")

    async def test_the_switchboard_role_cannot_delete_even_after_an_init_db_regrant(self):
        await self.seed()
        with pytest.raises(asyncpg.PostgresError):
            await self.runtime.execute("DELETE FROM public.owner_watches")


class TestConcurrencyNits:
    @pytest.fixture(autouse=True)
    def _bind(self, pools, lock_pool):
        self.owner, self.runtime = pools
        self.locks = lock_pool

    async def test_idle_local_locks_are_evicted(self):
        store = PostgresWatchStore(self.runtime, lock_pool=self.locks)
        async with store.serialized("evict-me"):
            assert "evict-me" in store._local
        assert store._local == {}

    async def test_a_concurrent_insert_of_one_id_replays_not_a_connection_error(self):
        store = PostgresWatchStore(self.runtime, lock_pool=self.locks)
        watch = make_watch("racing")
        results = await asyncio.gather(
            *(store.write(watch, expected_version=None, cap=50) for _ in range(8))
        )
        assert {r.id for r in results} == {"racing"}
        assert len(await store.list_all()) == 1

    async def test_a_unique_violation_on_a_versioned_write_is_a_conflict(self, monkeypatch):
        import asyncpg as driver

        from butlers.watches import WatchConflict

        store = PostgresWatchStore(self.runtime, lock_pool=self.locks)

        async def violate(*args, **kwargs):
            raise driver.UniqueViolationError("duplicate key")

        monkeypatch.setattr(store, "_write", violate)
        with pytest.raises(WatchConflict):
            await store.write(make_watch("x"), expected_version=1, cap=50)

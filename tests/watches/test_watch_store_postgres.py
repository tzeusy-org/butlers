"""REQ-owner-watches-003/004 over migrated PostgreSQL: durable, fenced, capped.

Runs the shared store contract plus what only a real database proves: the role fence that
survives an init-db re-grant, trigger-enforced immutability and the state rules, the cap
backstop below the Python store, and the per-schema two-chain install.
"""

from __future__ import annotations

from urllib.parse import urlparse

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


class TestPostgresWatchStoreContract(WatchStoreContract):
    @pytest.fixture(autouse=True)
    def _bind(self, pools):
        self.owner, self.runtime = pools

    def make_store(self):
        return PostgresWatchStore(self.runtime)


class TestTableGuards:
    @pytest.fixture(autouse=True)
    def _bind(self, pools):
        self.owner, self.runtime = pools

    async def seed(self, watch_id="w1", **changes):
        store = PostgresWatchStore(self.runtime)
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

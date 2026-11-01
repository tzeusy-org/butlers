"""Migrated ``public.ambient_projections`` proof (core_291, REQ-ambient-projections-001).

The owning migration login mints, stamps and revokes; a runtime role reads but
cannot write, even if a bootstrap rerun re-grants it the DML privileges.
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest

from butlers.api.projection_grants import PostgresProjectionGrantStore, resolve_grant
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def projections_db_url(postgres_container):
    return create_migrated_test_db(
        postgres_container, migration_db_name(), ["core"], revisions={"core": "core_291"}
    )


async def test_owner_login_lifecycle_and_runtime_role_is_read_only(projections_db_url):
    pool = await asyncpg.create_pool(projections_db_url, min_size=1, max_size=2)
    try:
        store = PostgresProjectionGrantStore(pool)
        grant, token = await store.mint(
            label="Hall display",
            surface_kind="eink",
            audience_class="household",
            route_scope="glance",
            expected_fetch_interval=timedelta(minutes=5),
        )
        stored = await pool.fetchval(
            "SELECT token_digest FROM public.ambient_projections WHERE id = $1", grant.id
        )
        assert stored == grant.token_digest and token not in stored
        assert await resolve_grant(store, grant.id, "glance", token) == grant
        assert await resolve_grant(store, grant.id, "glance", token + "x") is None

        await store.record_fetch(grant.id, "ok")
        stamped = await store.get_active(grant.id)
        assert stamped is not None and stamped.last_fetch_status == "ok"

        async with pool.acquire() as conn:
            # A bootstrap rerun re-grants DML; row-level security still refuses writes.
            await conn.execute(
                "GRANT INSERT, UPDATE, DELETE ON public.ambient_projections TO butler_general_rw"
            )
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE butler_general_rw")
                assert await conn.fetchval("SELECT count(*) FROM public.ambient_projections") == 1
                updated = await conn.execute(
                    "UPDATE public.ambient_projections SET revoked_at = NULL"
                )
                assert updated == "UPDATE 0"
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute(
                        "INSERT INTO public.ambient_projections "
                        "(id, label, surface_kind, audience_class, route_scope, token_digest) "
                        "VALUES (gen_random_uuid(), 'x', 'other', 'household', 'glance', $1)",
                        "0" * 64,
                    )

        revoked = await store.revoke(grant.id)
        assert revoked is not None and revoked.revoked_at is not None
        assert await store.get_active(grant.id) is None
        assert await store.revoke(grant.id) is None
    finally:
        await pool.close()

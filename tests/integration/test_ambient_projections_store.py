"""Migrated ``public.ambient_projections`` proof (core_291, REQ-ambient-projections-001).

The owning migration login mints, stamps and revokes; a runtime role reads but
cannot write, even if a bootstrap rerun re-grants it the DML privileges.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import asyncpg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.api.projection_grants import PostgresProjectionGrantStore, resolve_grant
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

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


async def _grant_boundary(conn):
    """Keep complete row and authority evidence in memory, never test properties."""
    return {
        "oid": await conn.fetchval("SELECT to_regclass('public.ambient_projections')::oid"),
        "rows": await conn.fetchval(
            "SELECT coalesce(jsonb_agg(to_jsonb(grant_row) ORDER BY id), '[]'::jsonb) "
            "FROM public.ambient_projections grant_row"
        ),
        "authority": await conn.fetchval(
            "SELECT jsonb_build_object('owner', pg_get_userbyid(relowner), "
            "'rls', relrowsecurity, 'force_rls', relforcerowsecurity, 'acl', relacl::text) "
            "FROM pg_class WHERE oid='public.ambient_projections'::regclass"
        ),
        "columns": [
            tuple(row)
            for row in await conn.fetch(
                "SELECT attname, format_type(atttypid, atttypmod), attnotnull, attacl::text, "
                "pg_get_expr(adbin, adrelid) FROM pg_attribute "
                "LEFT JOIN pg_attrdef ON adrelid=attrelid AND adnum=attnum "
                "WHERE attrelid='public.ambient_projections'::regclass "
                "AND attnum>0 AND NOT attisdropped ORDER BY attnum"
            )
        ],
        "constraints": [
            tuple(row)
            for row in await conn.fetch(
                "SELECT conname, condeferrable, condeferred, convalidated, "
                "pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid='public.ambient_projections'::regclass ORDER BY conname"
            )
        ],
        "indexes": [
            tuple(row)
            for row in await conn.fetch(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname='public' AND tablename='ambient_projections' ORDER BY indexname"
            )
        ],
        "policies": [
            tuple(row)
            for row in await conn.fetch(
                "SELECT polname, polcmd, polpermissive, polroles::text, "
                "pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid) "
                "FROM pg_policy WHERE polrelid='public.ambient_projections'::regclass "
                "ORDER BY polname"
            )
        ],
        "privileges": [
            tuple(row)
            for row in await conn.fetch(
                r"""
                SELECT rolname,
                  has_table_privilege(oid, 'public.ambient_projections', 'SELECT'),
                  has_table_privilege(oid, 'public.ambient_projections', 'INSERT'),
                  has_table_privilege(oid, 'public.ambient_projections', 'UPDATE'),
                  has_table_privilege(oid, 'public.ambient_projections', 'DELETE'),
                  has_table_privilege(oid, 'public.ambient_projections', 'TRUNCATE'),
                  has_table_privilege(oid, 'public.ambient_projections', 'REFERENCES'),
                  has_table_privilege(oid, 'public.ambient_projections', 'TRIGGER')
                FROM pg_roles WHERE rolname=current_user OR rolname='connector_writer'
                  OR rolname LIKE 'butler\_%\_rw' ESCAPE '\'
                ORDER BY rolname
                """
            )
        ],
    }


async def _assert_grant_boundary(conn, baseline):
    # Check existence before reading the table: the old path must reach the
    # preservation assertion, rather than raising an incidental missing-table error.
    assert (
        await conn.fetchval("SELECT to_regclass('public.ambient_projections')::oid")
        == baseline["oid"]
    ), "ambient grant boundary was removed"
    assert await _grant_boundary(conn) == baseline, "ambient grant authority or rows changed"


def _restore_finalized_interface_sql() -> str:
    """Read the unchanged protected predicate without executing migration code."""
    boundary = Path(__file__).resolve().parents[2] / (
        "alembic/versions/core/core_196_restore_drill_executor_boundary.py"
    )
    return next(
        ast.literal_eval(node.value)
        for node in ast.parse(boundary.read_text()).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_TRUSTED_FINALIZED_INTERFACE_SQL"
            for target in node.targets
        )
    )


def _assert_old_namespace_predicate_removes_empty_boundary(url: str, expected_oid: int) -> None:
    """Run the actual downgrade with only its old predicate, then roll back."""
    source = Path(__file__).resolve().parents[2] / (
        "alembic/versions/core/core_291_ambient_projections.py"
    )
    module = ast.parse(source.read_text())
    downgrade = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
    )
    lock = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_LOCK" for target in node.targets)
    )
    current = "left(namespace.nspname, 3) <> 'pg_'"
    old = "namespace.nspname NOT LIKE 'pg_%%'"
    query = next(
        node
        for node in ast.walk(downgrade)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and current in node.value
    )
    assert query.value.count(current) == 1
    predicate = query.value.rsplit("AND ", 1)[1].strip()
    query.value = query.value.replace(current, old)
    code = compile(ast.Module(body=[downgrade], type_ignores=[]), str(source), "exec")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                # PostgreSQL interprets the actual source clause. Supported pg
                # names survive; only the literal reserved pg_ prefix is excluded.
                names = conn.exec_driver_sql(
                    "SELECT namespace.nspname FROM (VALUES ('general'), ('pga'), "
                    "('pgpeer'), ('pg_catalog'), ('pg_toast')) AS namespace(nspname) "
                    f"WHERE {predicate} ORDER BY 1"
                ).scalars()
                assert list(names) == ["general", "pga", "pgpeer"]
                namespace: dict[str, Any] = {
                    "op": Operations(MigrationContext.configure(conn)),
                    "_LOCK": lock,
                }
                exec(code, namespace)
                namespace["downgrade"]()
                with pytest.raises(AssertionError, match="ambient grant boundary was removed"):
                    assert (
                        conn.exec_driver_sql(
                            "SELECT to_regclass('public.ambient_projections')::oid"
                        ).scalar()
                        == expected_oid
                    ), "ambient grant boundary was removed"
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


async def test_schema_downgrade_preserves_grants_until_final_empty_consumer(
    postgres_container, record_property
):
    """Real A/B migrations, old destructive SQL RED, rollback and restored PASS."""
    name = migration_db_name()
    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        name,
        ["core"],
        revisions={"core": "core_291"},
    )
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    # Initial A genuinely traversed the complete chain as its normal login.
    # Full-chain B replay uses the same ordinary caller so core196 can prove
    # its exact finalized interface and avoid the absence-only installer.
    # Managed rollback/catalogue observation does not replace that caller.
    bootstrap = migration_bootstrap_db_url(postgres_container, name)
    control = None
    first = _build_alembic_config(bootstrap, chains=["core"])
    second = _build_alembic_config(url, chains=["core"], target_schema="general")
    try:
        role = await pool.fetchrow(
            "SELECT current_user, rolsuper, rolcreaterole, rolcreatedb "
            "FROM pg_roles WHERE rolname=current_user"
        )
        assert role["current_user"] == urlparse(url).username
        assert not role["rolsuper"] and not role["rolcreaterole"] and not role["rolcreatedb"]
        owner = await pool.fetchval(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE oid='public.ambient_projections'::regclass"
        )
        assert owner == role["current_user"]
        control = await asyncpg.connect(
            bootstrap.replace("postgresql+psycopg2://", "postgresql://")
        )
        # Exercise the actual unchanged protected predicate, rather than a
        # copied or weakened interpretation of its caller/ACL conditions.
        finalized_sql = await asyncio.to_thread(_restore_finalized_interface_sql)
        assert await pool.fetchval(finalized_sql)
        assert not await control.fetchval(finalized_sql)
        record_property("ambient_peer_interface", "PASS:ordinary_true_bootstrap_false")
        await asyncio.to_thread(command.upgrade, second, "core_291")
        # pinned-revision: the bounded 291/290 lifetime boundary, not the current head.
        assert (
            await control.fetchval("SELECT version_num FROM public.alembic_version") == "core_291"
        )
        # pinned-revision: the bounded 291/290 lifetime boundary, not the current head.
        assert (
            await control.fetchval("SELECT version_num FROM general.alembic_version") == "core_291"
        )
        assert (
            await pool.fetchval(
                "SELECT pg_get_userbyid(relowner) FROM pg_class "
                "WHERE oid='public.ambient_projections'::regclass"
            )
        ) == owner
        store = PostgresProjectionGrantStore(pool)
        grant, token = await store.mint(
            label="Lifetime sentinel",
            surface_kind="eink",
            audience_class="household",
            route_scope="glance",
            expected_fetch_interval=timedelta(minutes=5),
        )
        await store.record_fetch(grant.id, "ok")
        async with pool.acquire() as conn:
            baseline = await _grant_boundary(conn)
            served = await conn.fetchval("SELECT to_regclass('public.model_served_usage')::oid")
            assert served is not None
            assert await resolve_grant(store, grant.id, "glance", token) is not None
            transaction = conn.transaction()
            await transaction.start()
            try:
                # Exact destructive SQL from the published 291 downgrade, with
                # the new protection neutralized in this owned transaction only.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended('butlers:core_291:ambient-projections', 0))"
                )
                await conn.execute("DROP TABLE IF EXISTS public.ambient_projections")
                with pytest.raises(AssertionError, match="ambient grant boundary was removed"):
                    await _assert_grant_boundary(conn, baseline)
                record_property("ambient_old_destructive_path", "ASSERTION_RED:boundary_removed")
            finally:
                await transaction.rollback()
            await _assert_grant_boundary(conn, baseline)
            record_property("ambient_control_rollback", "PASS:exact_baseline_restored")

        await asyncio.to_thread(command.downgrade, second, "core_290")
        async with pool.acquire() as conn:
            await _assert_grant_boundary(conn, baseline)
        # pinned-revision: the bounded 291/290 lifetime boundary, not the current head.
        assert (
            await control.fetchval("SELECT version_num FROM general.alembic_version") == "core_290"
        )
        # pinned-revision: the bounded 291/290 lifetime boundary, not the current head.
        assert (
            await control.fetchval("SELECT version_num FROM public.alembic_version") == "core_291"
        )
        await asyncio.to_thread(command.upgrade, second, "core_291")
        async with pool.acquire() as conn:
            await _assert_grant_boundary(conn, baseline)
        # Exercise the opposite schema order too; B is now the last consumer.
        await asyncio.to_thread(command.downgrade, first, "core_290")
        async with pool.acquire() as conn:
            await _assert_grant_boundary(conn, baseline)
        for revoked in (False, True):
            if revoked:
                assert await store.revoke(grant.id) is not None
                async with pool.acquire() as conn:
                    baseline = await _grant_boundary(conn)
            with pytest.raises(DBAPIError, match="ambient projection grants exist; roll forward"):
                await asyncio.to_thread(command.downgrade, second, "core_290")
            async with pool.acquire() as conn:
                await _assert_grant_boundary(conn, baseline)
            # pinned-revision: the bounded 291/290 lifetime boundary, not the current head.
            assert (
                await control.fetchval("SELECT version_num FROM general.alembic_version")
                == "core_291"
            )
        record_property("ambient_final_nonempty", "PASS:live_and_revoked_refusal")
        # Only fixture-owned rows are removed, by the original owning login.
        await pool.execute("DELETE FROM public.ambient_projections WHERE id=$1", grant.id)
        assert await pool.fetchval("SELECT count(*) FROM public.ambient_projections") == 0
        await asyncio.to_thread(command.downgrade, second, "core_290")
        assert await pool.fetchval("SELECT to_regclass('public.ambient_projections')") is None
        # pinned-revision: the bounded 291/290 lifetime boundary, not the current head.
        assert (
            await control.fetchval("SELECT version_num FROM public.alembic_version") == "core_290"
        )
        # pinned-revision: the bounded 291/290 lifetime boundary, not the current head.
        assert (
            await control.fetchval("SELECT version_num FROM general.alembic_version") == "core_290"
        )
        assert await pool.fetchval("SELECT to_regclass('public.model_served_usage')::oid") == served
        # Recreate through the real ordinary owner, without crossing fixed
        # installers again or inventing ownership privileges for a bootstrap role.
        ordinary = _build_alembic_config(url, chains=["core"])
        await asyncio.to_thread(command.upgrade, ordinary, "core_291")
        async with pool.acquire() as conn:
            restored = await _grant_boundary(conn)
        assert restored["rows"] == "[]"
        for key in ("authority", "columns", "constraints", "indexes", "policies", "privileges"):
            assert restored[key] == baseline[key]
        replacement, replacement_token = await store.mint(
            label="Reupgrade sentinel",
            surface_kind="eink",
            audience_class="household",
            route_scope="glance",
            expected_fetch_interval=timedelta(minutes=5),
        )
        assert (
            await resolve_grant(store, replacement.id, "glance", replacement_token) == replacement
        )
        record_property("ambient_final_empty_reupgrade", "PASS:authority_and_owner_mint_restored")

        # Keep the complete general-peer path above. A supported pg-prefixed
        # peer adds the separate empty-store case that a LIKE pg_ wildcard missed.
        await pool.execute("DELETE FROM public.ambient_projections WHERE id=$1", replacement.id)
        assert await pool.fetchval("SELECT count(*) FROM public.ambient_projections") == 0
        pgpeer = _build_alembic_config(url, chains=["core"], target_schema="pgpeer")
        await asyncio.to_thread(command.upgrade, pgpeer, "core_291")
        # pinned-revision: the bounded supported-schema 291/290 lifetime boundary.
        assert await pool.fetchval("SELECT version_num FROM pgpeer.alembic_version") == "core_291"
        async with pool.acquire() as conn:
            empty_boundary = await _grant_boundary(conn)
        assert empty_boundary["rows"] == "[]"
        await asyncio.to_thread(
            _assert_old_namespace_predicate_removes_empty_boundary, url, empty_boundary["oid"]
        )
        record_property("ambient_old_namespace_predicate", "ASSERTION_RED:boundary_removed")
        async with pool.acquire() as conn:
            await _assert_grant_boundary(conn, empty_boundary)
        record_property("ambient_namespace_rollback", "PASS:exact_empty_boundary_restored")
        await asyncio.to_thread(command.downgrade, ordinary, "core_290")
        async with pool.acquire() as conn:
            await _assert_grant_boundary(conn, empty_boundary)
        # pinned-revision: genuine pgpeer remains the last 291 consumer.
        assert await pool.fetchval("SELECT version_num FROM pgpeer.alembic_version") == "core_291"
        # pinned-revision: the actual public departure completed below 291.
        assert await pool.fetchval("SELECT version_num FROM public.alembic_version") == "core_290"
        record_property("ambient_pgpeer_preservation", "PASS:empty_boundary_and_authority_retained")
        await asyncio.to_thread(command.downgrade, pgpeer, "core_290")
        assert await pool.fetchval("SELECT to_regclass('public.ambient_projections')") is None
        # pinned-revision: the genuine last pgpeer departure removes only an empty store.
        assert await pool.fetchval("SELECT version_num FROM pgpeer.alembic_version") == "core_290"
        assert await pool.fetchval("SELECT to_regclass('public.model_served_usage')::oid") == served
        await asyncio.to_thread(command.upgrade, pgpeer, "core_291")
        async with pool.acquire() as conn:
            pgpeer_restored = await _grant_boundary(conn)
        assert pgpeer_restored["rows"] == "[]"
        for key in ("authority", "columns", "constraints", "indexes", "policies", "privileges"):
            assert pgpeer_restored[key] == empty_boundary[key]
        pg_grant, pg_token = await store.mint(
            label="Supported peer sentinel",
            surface_kind="eink",
            audience_class="household",
            route_scope="glance",
            expected_fetch_interval=timedelta(minutes=5),
        )
        assert await resolve_grant(store, pg_grant.id, "glance", pg_token) == pg_grant
        record_property(
            "ambient_pgpeer_final_reupgrade", "PASS:last_peer_empty_owner_mint_restored"
        )
    finally:
        if control is not None:
            await control.close()
        await pool.close()

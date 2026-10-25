"""Genuine migrated catalog admission matrix. Locally authored/collected, not executed.

REQ-database-security-020; REQ-database-security-016. Administrative mutations
below are confined to fresh disposable test databases and unique test logins.
Current bootstrap still gives runtime schema CREATE: these tests explicitly
stage the intended least-privilege profile by REVOKE, not a production installer
or a substitute private-memory GRANT. Full manifest convergence remains future.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from urllib.parse import unquote, urlparse

import asyncpg
import pytest

from butlers.db import Database
from butlers.db_authority import roles
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def authority_database(postgres_container, tmp_path, monkeypatch):
    name = migration_db_name()
    url = create_migrated_test_db(
        postgres_container,
        name,
        ["core", "chronicler", "memory"],
        {"core": "chronicler", "chronicler": "chronicler", "memory": "chronicler_mem"},
    )
    # The isolated deployment's fixed source has the same declared identities;
    # only its disposable database name differs. No caller authority list exists.
    root = tmp_path / "roster"
    entry = root / "chronicler"
    entry.mkdir(parents=True)
    (entry / "butler.toml").write_text(
        f'[butler]\nname="chronicler"\n[butler.db]\nname="{name}"\n'
        'schema="chronicler"\n[modules.memory]\nmemory_schema="chronicler_mem"\n'
    )
    monkeypatch.setattr(roles, "_ROSTER_ROOT", root)
    return url, migration_bootstrap_db_url(postgres_container, name)


@asynccontextmanager
async def _case(authority_database):
    url, admin_url = authority_database
    parsed = urlparse(url)
    bootstrap = urlparse(admin_url)
    if bootstrap.scheme.split("+", 1)[0] not in ("postgres", "postgresql"):
        raise ValueError("Unsupported administrative database scheme")
    # Testcontainers returns an SQLAlchemy driver-qualified URL. Only its
    # scheme changes; escaped credentials, target and SSL options stay intact.
    admin_dsn = bootstrap._replace(scheme="postgresql").geturl()
    login = "authority_" + uuid.uuid4().hex[:16]
    admin = await asyncpg.connect(admin_dsn)
    owner = None
    database = None
    role_created = False
    role_creation_unknown = False
    primary_error = None

    async def cleanup():
        failures = []

        async def attempt(operation, terminate=None):
            try:
                return await asyncio.wait_for(operation(), timeout=5)
            except (Exception, asyncio.CancelledError) as exc:
                failures.append(exc)
                if terminate is not None:
                    try:
                        terminate()
                    except Exception as cleanup_error:
                        failures.append(cleanup_error)
                return None

        if database is not None:
            pool = database.pool
            await attempt(
                database.close,
                pool.terminate if isinstance(pool, asyncpg.Pool) else None,
            )
        cleanup_admin = None
        if role_created or role_creation_unknown:
            # A cancelled CREATE can hide either success or a concurrent
            # duplicate. Existence is never evidence that this case owns it.
            cleanup_admin = await attempt(lambda: asyncpg.connect(admin_dsn, timeout=5))
            connection = cleanup_admin if cleanup_admin is not None else admin
            if role_creation_unknown:
                failures.append(RuntimeError("Disposable role creation ownership is unknown"))
            if role_created and owner is not None:
                target = unquote(parsed.path[1:]).replace('"', '""')
                old_owner = owner.replace('"', '""')
                await attempt(
                    lambda: connection.execute(
                        f'ALTER DATABASE "{target}" OWNER TO "{old_owner}"', timeout=5
                    )
                )
            exists = False
            if role_created:
                exists = await attempt(
                    lambda: connection.fetchval(
                        "SELECT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname=$1)",
                        login,
                        timeout=5,
                    )
                )
            if role_created and exists is not False:
                # NULL/unreadable is not proof of absence. Attempt both owned
                # releases independently and report any incomplete cleanup.
                await attempt(
                    lambda: connection.execute(f'DROP OWNED BY "{login}" CASCADE', timeout=5)
                )
                await attempt(
                    lambda: connection.execute(f'DROP ROLE IF EXISTS "{login}"', timeout=5)
                )
        if cleanup_admin is not None:
            await attempt(lambda: cleanup_admin.close(timeout=5), cleanup_admin.terminate)
        await attempt(lambda: admin.close(timeout=5), admin.terminate)
        return failures

    try:
        owner = await admin.fetchval(
            "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database "
            "WHERE datname=current_database()"
        )
        if not isinstance(owner, str) or not owner:
            raise RuntimeError("Disposable database owner is unavailable")
        exists = await admin.fetchval(
            "SELECT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname=$1)", login
        )
        if exists is not False:
            raise RuntimeError("Disposable login is not fresh")
        role_creation_unknown = True
        try:
            await admin.execute(
                f'CREATE ROLE "{login}" LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
                "NOREPLICATION NOBYPASSRLS PASSWORD 'synthetic-test-only'"
            )
        except asyncpg.DuplicateObjectError:
            # A concurrent/preexisting identity was not created by this case.
            role_creation_unknown = False
            raise
        role_created = True  # only an acknowledged CREATE proves this creator
        role_creation_unknown = False
        await admin.execute(f'GRANT butler_chronicler_rw TO "{login}"')
        # Stage only the future intended no-CREATE posture in this isolated DB.
        for row in await admin.fetch("SELECT nspname FROM pg_catalog.pg_namespace"):
            schema = row["nspname"].replace('"', '""')
            await admin.execute(
                f'REVOKE CREATE ON SCHEMA "{schema}" FROM PUBLIC, butler_chronicler_rw'
            )
        await admin.execute(
            f'REVOKE CREATE ON DATABASE "{unquote(parsed.path[1:])}" '
            "FROM PUBLIC, butler_chronicler_rw"
        )
        database = Database(
            unquote(parsed.path[1:]),
            schema="chronicler",
            role="butler_chronicler_rw",
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=login,
            password="synthetic-test-only",
            min_pool_size=0,
            max_pool_size=1,
        )
        yield database, admin, login
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        # Retain the finite cleanup task across repeated caller cancellation;
        # every native close/query has its own bound and remaining releases run.
        task = asyncio.create_task(cleanup())
        cancelled = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if cancelled is None:
                    cancelled = exc
        failures = task.result()
        if failures:
            if primary_error is not None:
                primary_error.add_note("Disposable catalog cleanup incomplete")
            elif cancelled is None:
                raise RuntimeError("Disposable catalog cleanup incomplete") from failures[0]
        if cancelled is not None and primary_error is None:
            raise cancelled


async def test_real_migrated_private_consumer_and_independent_commit(authority_database):
    async with _case(authority_database) as (primary, admin, _):
        pool = await primary.connect()
        assert type(pool) is asyncpg.Pool
        assert await pool.fetchval("SELECT current_user") == "butler_chronicler_rw"
        child = primary.for_owned_schema("chronicler_mem")
        try:
            private = await child.connect()  # actual mem_015 ACL; no fixture private grant
            key = uuid.uuid4()
            async with private.acquire() as connection, connection.transaction():
                await connection.execute(
                    "INSERT INTO chronicler_mem.episodes(id,butler,content,content_authority) "
                    "VALUES($1,'chronicler','synthetic authority probe','system')",
                    key,
                )
            assert (
                await private.fetchval(
                    "SELECT count(*) FROM chronicler_mem.episodes WHERE id=$1", key
                )
                == 1
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM chronicler_mem.episodes WHERE id=$1", key
                )
                == 1
            )
        finally:
            await child.close()


@pytest.mark.parametrize(
    "fault",
    [
        "unexpected",
        "indirect_noinherit",
        "superuser",
        "createdb",
        "createrole",
        "replication",
        "bypassrls",
        "table_owner",
        "sequence_owner",
        "view_owner",
        "function_owner",
        "schema_owner",
        "database_owner",
        "schema_create",
        "database_create",
        "public_create",
        "admin_option",
    ],
)
async def test_actual_catalog_fault_refuses_before_pool_publication(authority_database, fault):
    async with _case(authority_database) as (database, admin, login):
        extra = "authority_extra_" + uuid.uuid4().hex[:12]
        created_extra = False
        try:
            if fault in ("unexpected", "indirect_noinherit"):
                await admin.execute(f'CREATE ROLE "{extra}" NOLOGIN NOINHERIT')
                created_extra = True
                await admin.execute(f'GRANT "{extra}" TO "{login}"')
                if fault == "indirect_noinherit":
                    await admin.execute("CREATE TABLE public.authority_owned(id integer)")
                    await admin.execute(f'ALTER TABLE public.authority_owned OWNER TO "{extra}"')
            elif fault in ("superuser", "createdb", "createrole", "replication", "bypassrls"):
                await admin.execute(f'ALTER ROLE "{login}" {fault.upper()}')
            elif fault == "table_owner":
                await admin.execute("CREATE TABLE public.authority_owned(id integer)")
                await admin.execute(f'ALTER TABLE public.authority_owned OWNER TO "{login}"')
            elif fault == "sequence_owner":
                await admin.execute("CREATE SEQUENCE public.authority_owned")
                await admin.execute(f'ALTER SEQUENCE public.authority_owned OWNER TO "{login}"')
            elif fault == "view_owner":
                await admin.execute("CREATE VIEW public.authority_owned AS SELECT 1 AS value")
                await admin.execute(f'ALTER VIEW public.authority_owned OWNER TO "{login}"')
            elif fault == "function_owner":
                await admin.execute(
                    "CREATE FUNCTION public.authority_owned() RETURNS integer "
                    "LANGUAGE sql AS $$ SELECT 1 $$"
                )
                await admin.execute(f'ALTER FUNCTION public.authority_owned() OWNER TO "{login}"')
            elif fault == "schema_owner":
                await admin.execute(f'CREATE SCHEMA authority_owned AUTHORIZATION "{login}"')
            elif fault == "database_owner":
                target = database.db_name
                await admin.execute(f'ALTER DATABASE "{target}" OWNER TO "{login}"')
            elif fault == "schema_create":
                await admin.execute(f'GRANT CREATE ON SCHEMA public TO "{login}"')
            elif fault == "database_create":
                await admin.execute(f'GRANT CREATE ON DATABASE "{database.db_name}" TO "{login}"')
            elif fault == "public_create":
                await admin.execute("GRANT CREATE ON SCHEMA public TO PUBLIC")
            elif fault == "admin_option":
                await admin.execute(f'GRANT butler_chronicler_rw TO "{login}" WITH ADMIN OPTION')
            with pytest.raises(RuntimeError):
                await database.connect()
            assert database.pool is None
            # The genuine migrated objects still exist: missing tables cannot
            # make this denial vacuously pass.
            assert await admin.fetchval("SELECT to_regclass('chronicler_mem.episodes') IS NOT NULL")
        finally:
            if created_extra:
                await admin.execute(f'DROP OWNED BY "{extra}" CASCADE')
                await admin.execute(f'DROP ROLE "{extra}"')


@pytest.mark.parametrize("fault", ["membership", "owner", "create"])
async def test_native_reacquire_rechecks_committed_authority_change(authority_database, fault):
    async with _case(authority_database) as (database, admin, login):
        pool = await database.connect()
        assert await pool.fetchval("SELECT current_user") == "butler_chronicler_rw"
        if fault == "membership":
            await admin.execute(f'REVOKE butler_chronicler_rw FROM "{login}"')
        elif fault == "owner":
            await admin.execute("CREATE TABLE public.authority_owned(id integer)")
            await admin.execute(f'ALTER TABLE public.authority_owned OWNER TO "{login}"')
        else:
            await admin.execute(f'GRANT CREATE ON SCHEMA public TO "{login}"')
        with pytest.raises(RuntimeError):
            await pool.fetchval("SELECT 1")
        assert await admin.fetchval("SELECT to_regclass('chronicler_mem.episodes') IS NOT NULL")


async def test_private_usage_revocation_is_not_sibling_authority(authority_database):
    async with _case(authority_database) as (primary, admin, _):
        await primary.connect()
        child = primary.for_owned_schema("chronicler_mem")
        try:
            private = await child.connect()
            assert await private.fetchval("SELECT current_user") == "butler_chronicler_rw"
            await admin.execute("REVOKE USAGE ON SCHEMA chronicler_mem FROM butler_chronicler_rw")
            with pytest.raises(RuntimeError):
                await private.fetchval("SELECT 1")
            with pytest.raises(RuntimeError):
                primary.for_owned_schema("relationship")
        finally:
            await child.close()

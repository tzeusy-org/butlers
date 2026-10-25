"""Genuine migrated catalog admission matrix. Locally authored/collected, not executed.

REQ-database-security-020; REQ-database-security-016. Administrative mutations
below are confined to fresh disposable test databases and unique test logins.
The real bootstrap and held-session migration producer install the selected
source manifest. Test logins acquire only its real capability membership;
fixtures never substitute table/private-schema GRANTs or a hand-staged REVOKE
profile. Locally these genuine PostgreSQL bodies remain NOTRUN.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import tarfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from butlers.db import Database
from butlers.db_authority import roles
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

_PREDECESSOR = "30b376895e1108977bf1b80bf54fcbdaaa3d64ad"
_REPOSITORY = Path(__file__).resolve().parents[2]

# This process imports the released package, not the current editable package.
# It runs the actual released initializer and actual registered chain runners;
# no fixture DDL, table GRANT or manually assembled legacy ACL profile is used.
# The disposable DSN crosses stdin/stdout in RAM, never argv or a saved log.
_PREDECESSOR_SETUP = r"""
import asyncio
import json
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

import butlers
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from butlers.migrations import get_module_migration_chains, has_butler_chain, run_migrations
from butlers.modules.registry import default_registry
from butlers.testing.migration import create_migration_db, _bootstrap_migration_prerequisites

request = json.loads(sys.stdin.read())
root = Path.cwd().resolve()
assert Path(butlers.__file__).resolve().is_relative_to(root / "src")
registry = default_registry()
plan = []
for path in sorted((root / "roster").glob("*/butler.toml")):
    config = tomllib.loads(path.read_text())
    butler = config["butler"]
    schema = butler.get("db", {}).get("schema") or butler["name"]
    modules = registry.load_from_config(config.get("modules", {}))
    chains = [("core", schema)]
    if has_butler_chain(butler["name"]):
        chains.append((butler["name"], schema))
    for module, chain in get_module_migration_chains(modules, butler["name"]).items():
        target = config["modules"][module].get("memory_schema") or schema
        chains.append((chain, target))
    plan.extend(chains)
assert len({schema for chain, schema in plan if chain == "core"}) == 13
if request.get("plan_only"):
    print(json.dumps({"profiles": 13, "plans": sorted(set(plan))}))
else:
    class Container:
        def get_connection_url(self):
            return request["admin_url"]
        def get_container_host_ip(self):
            return urlparse(request["admin_url"]).hostname
        def get_exposed_port(self, _):
            return urlparse(request["admin_url"]).port
    url = create_migration_db(Container(), request["database"])
    async def migrate():
        for chain, schema in dict.fromkeys(plan):
            await run_migrations(url, chain=chain, schema=schema)
    asyncio.run(migrate())
    admin = urlparse(request["admin_url"])
    admin_url = admin._replace(path="/" + request["database"]).geturl()
    normal_owner = urlparse(url).username
    # The original bootstrap contract explicitly leaves the DB bootstrap-owned.
    # create_migration_db's generic test default instead owns it by the normal
    # login, so select the actual supported old deployment owner before capture.
    engine = create_engine(admin_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as conn:
            actor = conn.exec_driver_sql("SELECT session_user").scalar_one()
            quote = lambda value: '"' + value.replace('"', '""') + '"'
            conn.exec_driver_sql("ALTER DATABASE " + quote(request["database"])
                                 + " OWNER TO " + quote(actor))
    finally:
        engine.dispose()
    _bootstrap_migration_prerequisites(admin_url, normal_owner)
    print(json.dumps({"normal_url": url, "admin_url": admin_url, "plans": plan}))
"""

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def authority_database(postgres_container, tmp_path, monkeypatch):
    name = migration_db_name()
    # Select a real one-butler deployment BEFORE administrative provisioning.
    # The fixed source deployment is named butlers; the disposable producer
    # separately selects its unique physical database through the same compiler.
    root = tmp_path / "roster"
    entry = root / "chronicler"
    entry.mkdir(parents=True)
    source = (
        '[butler]\nname="chronicler"\n[butler.db]\nname="butlers"\n'
        'schema="chronicler"\n[modules.chronicler]\n[modules.memory]\nmemory_schema="chronicler_mem"\n'
    )
    (entry / "butler.toml").write_text(source)
    monkeypatch.setattr(roles, "_ROSTER_ROOT", root)
    url = create_migrated_test_db(
        postgres_container,
        name,
        ["core", "chronicler", "memory"],
        {"core": "chronicler", "chronicler": "chronicler", "memory": "chronicler_mem"},
    )
    # Serving admission observes that same deployment's actual database identity,
    # never an administrative source_database alias supplied by a runtime caller.
    (entry / "butler.toml").write_text(source.replace('name="butlers"', f'name="{name}"'))
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
        # No fixture authority repair: the actual held migrator finalized the
        # catalog. A missing/broad production manifest fails the positive case.
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


@pytest.mark.parametrize(
    "fault",
    [
        "policy_widened",
        "policy_missing",
        "policy_extra",
        "trigger_disabled",
        "trigger_missing",
        "trigger_function",
        "trigger_event",
        "trigger_condition",
    ],
)
async def test_native_handoff_rejects_changed_policy_or_trigger(authority_database, fault):
    """Real native expression/trigger metadata, not a recorded SQL predicate."""
    async with _case(authority_database) as (database, admin, _):
        pool = await database.connect()
        assert await pool.fetchval("SELECT current_user") == "butler_chronicler_rw"
        if fault == "policy_widened":
            await admin.execute(
                "ALTER POLICY dnd_user_context_insert ON public.user_context WITH CHECK(true)"
            )
        elif fault == "policy_missing":
            await admin.execute("DROP POLICY dnd_user_context_insert ON public.user_context")
        elif fault == "policy_extra":
            await admin.execute(
                "CREATE POLICY authority_unlisted ON public.user_context USING(true) WITH CHECK(true)"
            )
        elif fault == "trigger_disabled":
            await admin.execute(
                "ALTER TABLE public.audit_log DISABLE TRIGGER audit_log_writer_identity"
            )
        elif fault == "trigger_missing":
            await admin.execute("DROP TRIGGER audit_log_writer_identity ON public.audit_log")
        else:
            await admin.execute("DROP TRIGGER audit_log_writer_identity ON public.audit_log")
            event = "UPDATE" if fault == "trigger_event" else "INSERT"
            condition = "WHEN(NEW.actor IS NOT NULL) " if fault == "trigger_condition" else ""
            function = (
                "audit_log_refuse_mutation"
                if fault == "trigger_function"
                else "audit_log_bind_writer"
            )
            await admin.execute(
                f"CREATE TRIGGER audit_log_writer_identity BEFORE {event} ON public.audit_log "
                f"FOR EACH ROW {condition}EXECUTE FUNCTION public.{function}()"
            )
            await admin.execute(
                "ALTER TABLE public.audit_log ENABLE ALWAYS TRIGGER audit_log_writer_identity"
            )
        with pytest.raises(RuntimeError):
            await pool.fetchval("SELECT 1")
        assert await admin.fetchval("SELECT to_regclass('chronicler_mem.episodes') IS NOT NULL")


def _extract_pinned_predecessor(destination):
    """Extract only regular source files from an already-present immutable object."""
    archive = subprocess.run(
        ["git", "archive", "--format=tar", _PREDECESSOR, "src", "alembic", "roster", "scripts"],
        cwd=_REPOSITORY,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    if archive.returncode:
        raise RuntimeError("Pinned predecessor Git object is unavailable")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as source:
        for member in source:
            path = destination / member.name
            if not path.resolve().is_relative_to(destination.resolve()):
                raise RuntimeError("Pinned predecessor archive path is invalid")
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                stream = source.extractfile(member)
                if stream is None:
                    raise RuntimeError("Pinned predecessor source is incomplete")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(stream.read())
            elif member.issym() and any(part.startswith(".") for part in path.parts):
                # Released roster agent-skill links are not package/migration
                # inputs. Do not follow them out of the extracted tree.
                continue
            else:
                # A source link cannot redirect package loading outside this
                # immutable extraction. All required package inputs are files.
                raise RuntimeError("Pinned predecessor source link is unsupported")


def _run_pinned_predecessor(root, payload):
    try:
        process = subprocess.run(
            [sys.executable, "-c", _PREDECESSOR_SETUP],
            cwd=root,
            env={**os.environ, "PYTHONPATH": str(root / "src")},
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Pinned predecessor setup exceeded its finite bound") from None
    if process.returncode:
        # Never copy raw migrations, driver errors or credentials into a failure.
        raise RuntimeError("Pinned predecessor setup did not complete")
    try:
        return json.loads(process.stdout)
    except (ValueError, TypeError):
        raise RuntimeError("Pinned predecessor setup receipt is invalid") from None


@pytest.fixture
def rollback_database(tmp_path, monkeypatch):
    """A complete real predecessor, isolated from other tests' GLOBAL role grants.

    Unlike ordinary per-database catalog faults, R0/R1 intentionally change
    cluster role attributes/memberships. Sharing the session server would make
    another test's login a foreign predecessor member and contaminate its proof.
    Only hosted integration executes this fixture; local validation collects it.
    """
    from testcontainers.postgres import PostgresContainer

    import conftest

    source = tmp_path / "released"
    source.mkdir()
    _extract_pinned_predecessor(source)
    container = conftest._configure_postgres_container(
        PostgresContainer("pgvector/pgvector:pg17"), os.environ
    )
    with container as postgres:
        result = _run_pinned_predecessor(
            source,
            {"admin_url": postgres.get_connection_url(), "database": migration_db_name()},
        )
        database = unquote(urlparse(result["normal_url"]).path[1:])
        selected = tmp_path / "selected-roster"
        for path in sorted((source / "roster").glob("*/butler.toml")):
            entry = selected / path.parent.name
            entry.mkdir(parents=True)
            content = path.read_text()
            old = 'name = "butlers"'
            if content.count(old) != 1:
                raise RuntimeError("Pinned deployment database source is ambiguous")
            # This changes only the physical disposable DB name, preserving all
            # actual module/private-schema declarations and their source owners.
            (entry / path.name).write_text(content.replace(old, f'name = "{database}"', 1))
        monkeypatch.setattr(roles, "_ROSTER_ROOT", selected)
        yield result | {"roster": selected}


def _rollback_observer(url):
    """Independent connections read durable data/stamps, never a producer cache."""
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            schemas = connection.exec_driver_sql(
                "SELECT nspname FROM pg_catalog.pg_namespace "
                "WHERE nspname NOT IN ('pg_catalog','information_schema') "
                "AND left(nspname,3)<>'pg_' ORDER BY nspname"
            ).scalars()
            versions = {}
            for schema in list(schemas):
                if connection.execute(
                    text("SELECT pg_catalog.to_regclass(:name) IS NOT NULL"),
                    {"name": f'"{schema}".alembic_version'},
                ).scalar_one():
                    quoted = schema.replace('"', '""')
                    versions[schema] = tuple(
                        connection.exec_driver_sql(
                            f'SELECT version_num FROM "{quoted}".alembic_version '
                            "ORDER BY version_num"
                        ).scalars()
                    )
            return {
                "versions": versions,
                "rows": connection.exec_driver_sql(
                    "SELECT key,value,version FROM general.state ORDER BY key"
                ).all(),
                "dnd": connection.exec_driver_sql(
                    "SELECT mutation_id,generation,writer,operation,correlation "
                    "FROM public.dnd_generation_mutations ORDER BY mutation_id"
                ).all(),
                "generation": connection.exec_driver_sql(
                    "SELECT * FROM public.dnd_generation_guard"
                ).all(),
                "outbox": connection.exec_driver_sql(
                    "SELECT id,md5(row_to_json(episode)::text) "
                    "FROM public.runtime_attention_outbox episode ORDER BY id"
                ).all(),
                "leases": connection.exec_driver_sql(
                    "SELECT lease_name,md5(row_to_json(lease)::text) "
                    "FROM public.runtime_attention_delivery_lease lease ORDER BY lease_name"
                ).all(),
                "trigger": connection.exec_driver_sql(
                    "SELECT t.tgname,t.tgtype,t.tgenabled,p.proname "
                    "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
                    "WHERE t.tgrelid='public.model_dispatch_attempts'::regclass "
                    "AND NOT t.tgisinternal ORDER BY t.tgname"
                ).all(),
            }
    finally:
        engine.dispose()


async def _apply_real_rebuild(result, monkeypatch):
    """Explicit operator owner handoff, then the unchanged production CLI path."""
    from butlers.bootstrap_prerequisite import prepare_bootstrap_connection
    from butlers.cli import _migrate_all
    from butlers.db_authority.apply import quote_identifier
    from butlers.db_authority.grants import compile_relation_manifest

    database = unquote(urlparse(result["normal_url"]).path[1:])
    migration_role = unquote(urlparse(result["normal_url"]).username)
    engine = create_engine(
        result["admin_url"], poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    try:
        with engine.begin() as connection:
            actor = connection.exec_driver_sql("SELECT session_user").scalar_one()
            manifest = compile_relation_manifest(database, migration_role, actor)
            # These are the explicit operator ownership prerequisites, not
            # fixture replacement ACLs. Ordinary tables already belong to the
            # actual released migration login. Fixed purpose owners stay fixed.
            connection.exec_driver_sql(
                f"ALTER DATABASE {quote_identifier(database)} "
                f"OWNER TO {quote_identifier(migration_role)}"
            )
            protected = {
                "dnd_generation_private",
                "restore_drill_executor",
                "dashboard_attention",
                "dashboard_stepup",
            }
            for schema in manifest.schemas:
                if schema in protected or schema == "public":
                    continue
                owner = connection.execute(
                    text(
                        "SELECT pg_catalog.pg_get_userbyid(nspowner) "
                        "FROM pg_catalog.pg_namespace WHERE nspname=:schema"
                    ),
                    {"schema": schema},
                ).scalar_one_or_none()
                if owner is not None and owner not in {actor, migration_role}:
                    raise RuntimeError("Operator schema handoff owner is unavailable")
                if owner is not None:
                    connection.exec_driver_sql(
                        f"ALTER SCHEMA {quote_identifier(schema)} "
                        f"OWNER TO {quote_identifier(migration_role)}"
                    )
        raw = engine.raw_connection()
        try:
            raw.autocommit = True
            prepare_bootstrap_connection(raw, database, migration_role, source_database=database)
        finally:
            raw.close()
    finally:
        engine.dispose()
    monkeypatch.setenv("BUTLERS_MIGRATOR_DATABASE_URL", result["admin_url"])
    await _migrate_all({entry.name: entry for entry in result["roster"].iterdir()}, dry_run=False)


async def _record_real_rollback_attention_evidence(result, engine):
    """Real recorder -> durable episode -> owning lease/claim; never a send.

    The pinned predecessor and final installer must preserve these exact rows.
    Native login admission is a separate matrix: this explicitly administrative
    test connection selects the real runtime capability for the producer call.
    No protected table INSERT or fabricated lease/receipt bypass is used.
    """
    from butlers.core.dispatch_outcomes import record_dispatch_attempt
    from butlers.db import register_jsonb_codec
    from butlers.tools.switchboard.runtime_attention.outbox import RuntimeAttentionOutbox

    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO public.model_catalog(id,alias,runtime_type,model_id)
                VALUES (:id,:alias,'codex','synthetic-rollback-model')"""),
            {"id": identifier, "alias": "rollback-" + identifier.hex},
        )

    async def active_producer(connection):
        await connection.execute('SET ROLE "butler_general_rw"')

    parsed = urlparse(result["admin_url"])
    pool = asyncpg.create_pool(
        parsed._replace(scheme="postgresql").geturl(),
        min_size=0,
        max_size=2,
        timeout=5,
        command_timeout=5,
        init=register_jsonb_codec,
        setup=active_producer,
    )
    try:
        await pool
        attempts = []
        for index in range(5):
            attempt = await record_dispatch_attempt(
                pool,
                catalog_entry_id=identifier,
                butler="general",
                outcome="runtime_failure",
                attempt_index=index,
            )
            assert type(attempt) is int
            attempts.append(attempt)
        # A separate privileged observation cannot be a cached producer result.
        with engine.connect() as observer:
            episode_id = observer.execute(
                text(
                    "SELECT id FROM public.runtime_attention_outbox WHERE triggering_attempt_id=:id"
                ),
                {"id": attempts[-1]},
            ).scalar_one()
        repository = RuntimeAttentionOutbox(pool, instance_id="rollback-proof")
        lease = await repository.acquire_service_lease()
        assert lease is not None
        episode = await repository.claim_next_pending(lease)
        assert episode is not None and episode.id == episode_id
        assert episode.claim_epoch == 1 and episode.delivery_lease_epoch == lease.epoch
        assert await repository.claim_is_current(episode) is True
        assert await repository.release_service_lease(lease) is True
        # Leave the durable sending/unknown outcome untouched. No provider is
        # called, no synthetic ACK is invented, and a reverse must preserve it.
        with engine.connect() as observer:
            row = observer.execute(
                text(
                    "SELECT lifecycle_state,claim_token,claim_epoch,delivery_lease_epoch "
                    "FROM public.runtime_attention_outbox WHERE id=:id"
                ),
                {"id": episode_id},
            ).one()
            assert tuple(row) == ("sending", episode.claim_token, 1, lease.epoch)
    finally:
        task = asyncio.create_task(pool.close())
        cancelled = None
        deadline = asyncio.get_running_loop().time() + 5
        while not task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                pool.terminate()
                break
            try:
                await asyncio.wait_for(asyncio.shield(task), remaining)
            except TimeoutError:
                pool.terminate()
                break
            except asyncio.CancelledError as exc:
                if cancelled is None:
                    cancelled = exc
        # Native terminate releases every holder, including a shielded reset.
        # Preserve ownership until the already-created close task has settled.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if cancelled is None:
                    cancelled = exc
        task.result()
        if cancelled is not None:
            raise cancelled


def _observe_rebuilt_relation_matrix(result, engine):
    """Independent SQL observation plus real role behavior after production CLI.

    No fixture grants or cloned classifier response: PostgreSQL computes each
    effective table privilege, owner and ENABLE/FORCE flag, and each role then
    executes both a legitimate write and an actually forbidden foreign write.
    These administrative SET ROLE probes prove capability behavior, separately
    from the native login/recursive-reach matrix above. Every probe rolls back.
    """
    from butlers.db_authority.grants import (
        ObjectKind,
        compile_relation_manifest,
        relation_security_flags,
    )

    database = unquote(urlparse(result["normal_url"]).path[1:])
    migration_role = unquote(urlparse(result["normal_url"]).username)
    with engine.connect() as observer:
        actor = observer.exec_driver_sql("SELECT session_user").scalar_one()
        manifest = compile_relation_manifest(database, migration_role, actor)
        profiles = roles.runtime_profiles(database)
        assert len(profiles) == 13
        declared = {
            (row.schema, row.name): row
            for row in manifest.relations
            if row.kind in {ObjectKind.TABLE, ObjectKind.VIEW}
        }
        capabilities = [profile.role for profile in profiles] + [
            "dashboard_api",
            "connector_writer",
            "credential_store_writer",
        ]
        observed = observer.execute(
            text("""
                SELECT n.nspname, c.relname, pg_catalog.pg_get_userbyid(c.relowner),
                       c.relrowsecurity, c.relforcerowsecurity, candidate.role, verb.name,
                       pg_catalog.has_table_privilege(candidate.role,c.oid,verb.name)
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                CROSS JOIN unnest(CAST(:roles AS text[])) AS candidate(role)
                CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE',
                                       'TRUNCATE','REFERENCES','TRIGGER']) AS verb(name)
                WHERE n.nspname=ANY(:schemas) AND c.relkind IN ('r','p','v','m')
                  AND NOT EXISTS(SELECT FROM pg_catalog.pg_depend dependency
                      WHERE dependency.classid='pg_catalog.pg_class'::regclass
                        AND dependency.objid=c.oid AND dependency.deptype='e')
                ORDER BY n.nspname,c.relname,candidate.role,verb.name
            """),
            {"roles": capabilities, "schemas": list(manifest.schemas)},
        ).all()
        checked = set()
        for schema, name, owner, enabled, forced, capability, verb, granted in observed:
            # Partition children have an empty direct ACL and source-bound
            # parent authority. Their parent/sequence linkage is independently
            # checked by the production finalizer and native catalog matrix.
            declaration = declared.get((schema, name))
            if declaration is None:
                partition = observer.execute(
                    text(
                        "SELECT relispartition FROM pg_catalog.pg_class WHERE oid=CAST(:name AS regclass)"
                    ),
                    {"name": f'"{schema}"."{name}"'},
                ).scalar_one()
                assert partition is True and granted is False
                continue
            checked.add((schema, name))
            assert owner == declaration.owner
            assert (enabled, forced) == relation_security_flags(declaration)
            expected = any(
                permission.role == capability
                and not permission.columns
                and verb in permission.verbs
                for permission in declaration.permissions
            )
            assert granted is expected, (schema, name, capability, verb)
        assert all(not row.required or identity in checked for identity, row in declared.items())
        assert (
            observer.execute(
                text("""
                SELECT count(*) FROM pg_catalog.pg_default_acl defaults
                JOIN pg_catalog.pg_namespace n ON n.oid=defaults.defaclnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) acl
                WHERE n.nspname=ANY(:schemas) AND acl.grantee<>defaults.defaclrole
            """),
                {"schemas": list(manifest.schemas)},
            ).scalar_one()
            == 0
        )

    def probe(role, sql, params=None):
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql('SET LOCAL ROLE "' + role.replace('"', '""') + '"')
                return connection.execute(text(sql), params or {}).scalar_one()
            finally:
                transaction.rollback()

    for index, profile in enumerate(profiles):
        quoted = '"' + profile.primary_schema.replace('"', '""') + '"'
        foreign = profiles[(index + 1) % len(profiles)].primary_schema.replace('"', '""')
        assert (
            probe(
                profile.role,
                f"INSERT INTO {quoted}.state(key,value) VALUES (:key,'{{}}'::jsonb) RETURNING 1",
                {"key": "authority-matrix-" + uuid.uuid4().hex},
            )
            == 1
        )
        assert probe(profile.role, "SELECT count(*)>=0 FROM public.model_catalog") is True
        assert probe(profile.role, f"SELECT count(*)>=0 FROM {quoted}.alembic_version") is True
        for forbidden in (
            f"""INSERT INTO "{foreign}".state(key,value)
                VALUES ('foreign','{{}}'::jsonb) RETURNING 1""",
            f"CREATE TABLE {quoted}.authority_forbidden(value integer)",
            f"UPDATE {quoted}.alembic_version SET version_num=version_num RETURNING 1",
            "TRUNCATE public.dnd_generation_mutations",
        ):
            with pytest.raises(DBAPIError) as refused:
                probe(profile.role, forbidden)
            assert refused.value.orig.pgcode == "42501"
        for child in profile.private_schemas:
            target = '"' + child.replace('"', '""') + '"'
            assert probe(profile.role, f"SELECT count(*)>=0 FROM {target}.episodes") is True
        if profile.primary_schema == "concierge":
            for view in ("v_fleet_sessions", "v_fleet_spend"):
                assert probe(profile.role, f"SELECT count(*)>=0 FROM concierge.{view}") is True


def _prove_real_second_migrator_waits_for_owner(result, engine, monkeypatch):
    """Two real physical sessions share the production first-to-final lock.

    The observer witnesses the second session actually polling the source lock,
    not just a scheduled thread or an event double. Only after the first owner
    releases may the actual CLI complete all chains and final verification.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import parse_qsl, urlencode

    from butlers.cli import _migrate_all
    from butlers.migrations import _MIGRATION_LOCK, operator_rollback_session

    application = "authority-concurrency-" + uuid.uuid4().hex
    parsed = urlparse(result["admin_url"])
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "application_name"]
    selected_url = parsed._replace(query=urlencode([*query, ("application_name", application)]))
    monkeypatch.setenv("BUTLERS_MIGRATOR_DATABASE_URL", selected_url.geturl())
    configs = {entry.name: entry for entry in result["roster"].iterdir()}

    def second_migrator():
        return asyncio.run(_migrate_all(configs, dry_run=False))

    with ThreadPoolExecutor(max_workers=1) as executor:
        with operator_rollback_session(result["admin_url"]) as first:
            first_pid = first.exec_driver_sql("SELECT pg_catalog.pg_backend_pid()").scalar_one()
            future = executor.submit(second_migrator)
            deadline = time.monotonic() + 10
            observed = False
            while time.monotonic() < deadline and not future.done():
                with engine.connect() as observer:
                    observed = observer.execute(
                        text("""SELECT EXISTS(SELECT FROM pg_catalog.pg_stat_activity a
                            WHERE a.datname=current_database() AND a.application_name=:application
                              AND a.query LIKE '%pg_try_advisory_lock%'
                              AND a.pid<>:first
                              AND NOT EXISTS(SELECT FROM pg_catalog.pg_locks l
                                  WHERE l.pid=a.pid AND l.locktype='advisory' AND l.granted
                                    AND l.classid=:family AND l.objid=:operation AND l.objsubid=2))
                            AND EXISTS(SELECT FROM pg_catalog.pg_locks l
                                WHERE l.pid=:first AND l.locktype='advisory' AND l.granted
                                  AND l.classid=:family AND l.objid=:operation AND l.objsubid=2)"""),
                        {
                            "application": application,
                            "first": first_pid,
                            "family": _MIGRATION_LOCK[0],
                            "operation": _MIGRATION_LOCK[1],
                        },
                    ).scalar_one()
                if observed:
                    break
                time.sleep(0.02)
            assert observed is True
            assert not future.done()
        # The actual producer's existing lock watchdog and this outer await are
        # both finite. Exiting the executor always joins its owned worker.
        assert future.result(timeout=90) is None
    with engine.connect() as observer:
        assert (
            observer.execute(
                text(
                    "SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE application_name=:name"
                ),
                {"name": application},
            ).scalar_one()
            == 0
        )


@pytest.mark.timeout(600)  # Two real 13-schema releases; hosted duration remains uncalibrated.
async def test_real_predecessor_r0_r1_preserve_data_receipts_and_actual_stamps(
    rollback_database,
    monkeypatch,
):
    """RB01/02 and wrong-combination/destructive refusal use real PostgreSQL.

    This is authored proof, locally NOTRUN. It cannot grant RB06 live deployment
    readiness, native process/secret rotation, or a hosted success by collection.
    """
    from butlers.db_authority import apply
    from butlers.db_authority.roles import AuthorityError
    from butlers.migrations import operator_rollback_session, run_preserving_rollback

    result = rollback_database
    url = result["admin_url"]
    normal_role = unquote(urlparse(result["normal_url"]).username)
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as connection:
            # Use the real owner and DND writer gateway; never seed protected
            # receipts/generation through privileged INSERT or reset fixtures.
            connection.exec_driver_sql('SET LOCAL ROLE "' + normal_role + '"')
            connection.exec_driver_sql(
                "INSERT INTO general.state(key,value) VALUES "
                "('authority-rollback-proof','{\"synthetic\":true}'::jsonb)"
            )
            connection.exec_driver_sql("SET LOCAL ROLE butler_general_rw")
            connection.execute(
                text(
                    "SELECT * FROM public.context_dnd_mutate("
                    "CAST(:id AS uuid),'general','set',now()+interval '1 hour',"
                    "'synthetic rollback coverage',1.0::real,'{}'::jsonb)"
                ),
                {"id": str(uuid.uuid4())},
            ).all()
        await _record_real_rollback_attention_evidence(result, engine)
        before = _rollback_observer(url)
        assert before["rows"] and before["dnd"] and before["trigger"]
        assert before["outbox"] and before["leases"]
        with operator_rollback_session(url) as held:
            capsule = apply.capture_rollback_capsule(
                held,
                normal_role,
                {"chart": "released-30b", "image": "released-30b", "old_secret": "old-ref"},
            )
            held.commit()
        assert len(capsule["catalog"]["versions"]) >= 13

        # Actual old init must restore a genuinely absent GRANT. Omitting it
        # must fail and leave the denial intact; equal preexisting ACLs cannot
        # make the initializer omission falsely pass.
        with engine.begin() as connection:
            connection.exec_driver_sql("REVOKE SELECT ON general.state FROM butler_general_rw")
            assert connection.exec_driver_sql(
                "SELECT NOT has_table_privilege('butler_general_rw','general.state','SELECT')"
            ).scalar_one()
        with monkeypatch.context() as omitted:
            omitted.setattr(apply, "_replay_predecessor_initializer", lambda *args: None)
            with pytest.raises(AuthorityError):
                run_preserving_rollback(url, capsule, transition="R0")
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT NOT has_table_privilege('butler_general_rw','general.state','SELECT')"
            ).scalar_one()
        receipt = run_preserving_rollback(url, capsule, transition="R0")
        assert receipt["state"] == "predecessor_restored" and receipt["transition"] == "R0"
        assert _rollback_observer(url) == before
        with engine.connect() as connection:
            connection.exec_driver_sql("SET ROLE butler_general_rw")
            assert (
                connection.exec_driver_sql(
                    "SELECT value->>'synthetic' FROM general.state WHERE key='authority-rollback-proof'"
                ).scalar_one()
                == "true"
            )

        # A received wrong reference/stamp digest refuses before initializer
        # effects, and an owning protected receipt forbids destructive teardown.
        import copy
        import hashlib

        for fault in ("reference", "unknown_revision", "mixed_revision"):
            invalid = copy.deepcopy(capsule)
            if fault == "reference":
                invalid["release_references"]["old_secret"] = ""
            else:
                vector = invalid["catalog"]["versions"][0]["revisions"]
                vector[:] = (
                    ["unknown_001"]
                    if fault == "unknown_revision"
                    else sorted(set(vector) | {"core_279"})
                )
                invalid["catalog_sha256"] = hashlib.sha256(
                    json.dumps(invalid["catalog"], sort_keys=True, default=str).encode()
                ).hexdigest()
            with pytest.raises(AuthorityError):
                run_preserving_rollback(url, invalid, transition="R0")
            assert _rollback_observer(url) == before
        with engine.connect() as connection:
            for entrypoint in (
                "dnd_generation_admin.rollback_interface()",
                "runtime_attention_admin.rollback_interface()",
            ):
                with pytest.raises(DBAPIError) as refused:
                    connection.exec_driver_sql("SELECT " + entrypoint)
                assert refused.value.orig.pgcode == "P0001"
                connection.rollback()
        assert _rollback_observer(url) == before

        await _apply_real_rebuild(result, monkeypatch)
        rebuilt = _rollback_observer(url)
        # All thirteen real roster capabilities share the actual migrated
        # deployment; no narrowed single-Chronicler fixture can mask an omitted
        # Concierge, standalone QA or private Memory declaration.
        _observe_rebuilt_relation_matrix(result, engine)
        assert _rollback_observer(url) == rebuilt
        _prove_real_second_migrator_waits_for_owner(result, engine, monkeypatch)
        assert _rollback_observer(url) == rebuilt
        assert rebuilt["rows"] == before["rows"]
        assert rebuilt["dnd"] == before["dnd"]
        assert rebuilt["generation"] == before["generation"]
        assert all(
            "core_281" in stamps
            for stamps in rebuilt["versions"].values()
            if any(revision.startswith("core_") for revision in stamps)
        )
        assert not any(
            row[0] == "runtime_attention_plant_legacy_debounce_marker_trigger"
            for row in rebuilt["trigger"]
        )
        # RB03: execute the real Alembic reverse, with the correct physical
        # maintenance identity and lock, but without the preserving procedure.
        # Even this privileged entry must refuse before DDL/DCL or stamp writes;
        # testing only the removed administrative functions would miss it.
        from alembic import command
        from butlers.migrations import _build_alembic_config

        with pytest.raises(AuthorityError, match="Explicit preserving operator reverse"):
            with operator_rollback_session(url) as held:
                config = _build_alembic_config(url, ["core"], target_schema="general")
                config.attributes["connection"] = held
                command.downgrade(config, "core_280")
        assert _rollback_observer(url) == rebuilt
        receipt = run_preserving_rollback(url, capsule, transition="R1")
        assert receipt["state"] == "predecessor_restored" and receipt["transition"] == "R1"
        # A fresh connection observes full data/stamp equality and the actual
        # retired trigger restored, rather than an exception in a shape oracle.
        assert _rollback_observer(url) == before
        with engine.connect() as connection:
            connection.exec_driver_sql("SET ROLE butler_general_rw")
            assert (
                connection.exec_driver_sql(
                    "SELECT value->>'synthetic' FROM general.state WHERE key='authority-rollback-proof'"
                ).scalar_one()
                == "true"
            )
            with pytest.raises(DBAPIError) as refused:
                connection.exec_driver_sql("TRUNCATE public.dnd_generation_mutations")
            assert refused.value.orig.pgcode == "42501"
            connection.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("chain", "schema", "revision"),
    [
        ("core", None, "core_170"),
        ("core", None, "core_196"),
        ("core", None, "core_197"),
        ("memory", "isolated_memory", "mem_001"),
    ],
)
async def test_real_bounded_administrative_chain_closes_acl_without_runtime_profile(
    postgres_container, chain, schema, revision
):
    """Real old graph targets retain their data shape without serving authority."""

    def run():
        name = migration_db_name()
        url = create_migrated_test_db(
            postgres_container,
            name,
            [chain],
            {chain: schema},
            revisions={chain: revision},
        )
        target = "public" if schema is None else schema
        admin_url = migration_bootstrap_db_url(postgres_container, name)
        engine = create_engine(admin_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
        try:
            with engine.connect() as connection:
                assert connection.exec_driver_sql(
                    f'SELECT version_num FROM "{target}".alembic_version'
                ).scalars().all() == [revision]
                # The actual finalizer, not a fixture REVOKE, closes every
                # selected application's relation/function ACL. Extension
                # objects retain their independent package-defined contract.
                for kind, catalog, alias, namespace, owner, oidtype in (
                    ("r", "pg_class", "c", "relnamespace", "relowner", "pg_class"),
                    ("f", "pg_proc", "c", "pronamespace", "proowner", "pg_proc"),
                ):
                    acl = "relacl" if kind == "r" else "proacl"
                    population = (
                        " AND c.relkind IN ('r','p','v','m','S','f')" if kind == "r" else ""
                    )
                    default_kind = (
                        "CASE WHEN c.relkind='S' THEN 'S' ELSE 'r' END" if kind == "r" else "'f'"
                    )
                    assert (
                        connection.execute(
                            text(
                                f"SELECT count(*) FROM pg_catalog.{catalog} {alias} "
                                f"JOIN pg_catalog.pg_namespace n ON n.oid=c.{namespace} "
                                "CROSS JOIN LATERAL pg_catalog.aclexplode("
                                f"COALESCE(c.{acl},pg_catalog.acldefault("
                                f'({default_kind})::"char",c.{owner}))) a '
                                f"WHERE n.nspname=:schema AND a.grantee<>c.{owner} "
                                "AND NOT EXISTS(SELECT FROM pg_catalog.pg_depend d "
                                f"WHERE d.classid='pg_catalog.{oidtype}'::regclass "
                                "AND d.objid=c.oid AND d.deptype='e')" + population
                            ),
                            {"schema": target},
                        ).scalar_one()
                        == 0
                    )
                if chain == "core":
                    has_restore = connection.execute(
                        text("SELECT EXISTS(SELECT FROM pg_namespace WHERE nspname=:name)"),
                        {"name": "restore_drill_executor"},
                    ).scalar_one()
                    assert has_restore is (revision in {"core_196", "core_197"})
                else:
                    assert not any(
                        profile.primary_schema == target
                        for profile in roles.compile_deployment_profiles(
                            name, source_database="butlers"
                        )
                    )
        finally:
            engine.dispose()
        return url

    await asyncio.to_thread(run)

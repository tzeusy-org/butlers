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
from contextlib import asynccontextmanager, contextmanager
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
                "CREATE POLICY authority_unlisted ON public.user_context "
                "USING(true) WITH CHECK(true)"
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
        [
            "git",
            "archive",
            "--format=tar",
            _PREDECESSOR,
            "src",
            "alembic",
            "roster",
            "scripts",
            "deploy",
            "docker-compose.yml",
        ],
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
        yield result | {"roster": selected, "released_source": source}


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
                "marker_trigger": connection.exec_driver_sql(
                    "SELECT t.tgname,t.tgtype,t.tgenabled,p.proname "
                    "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
                    "WHERE t.tgrelid='public.runtime_attention_outbox'::regclass "
                    "AND NOT t.tgisinternal ORDER BY t.tgname"
                ).all(),
                "condition_markers": connection.exec_driver_sql(
                    "SELECT condition_id,md5(row_to_json(marker)::text) "
                    "FROM public.runtime_attention_condition_episodes marker ORDER BY condition_id"
                ).all(),
                "condition_facts": connection.exec_driver_sql(
                    "SELECT id,md5(row_to_json(fact)::text) "
                    "FROM public.infra_conditions fact ORDER BY id"
                ).all(),
                "attempts": connection.exec_driver_sql(
                    "SELECT id,md5(row_to_json(attempt)::text) "
                    "FROM public.model_dispatch_attempts attempt ORDER BY id"
                ).all(),
                "controls": {
                    name: connection.exec_driver_sql(
                        'SELECT md5(row_to_json(control)::text) FROM public."'
                        + name
                        + '" control ORDER BY singleton'
                    ).all()
                    for name in (
                        "runtime_attention_producer_control",
                        "runtime_attention_operator_control",
                        "runtime_attention_condition_control",
                    )
                },
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
            manifest = compile_relation_manifest(
                database, migration_role, actor, source_database=database
            )
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
    The administrative seed is separate from the two native LOGIN subjects;
    each native subject holds only its explicitly selected source capability.
    No protected table INSERT or fabricated lease/receipt bypass is used.
    """
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO public.model_catalog(id,alias,runtime_type,model_id)
                VALUES (:id,:alias,'codex','synthetic-rollback-model')"""),
            {"id": identifier, "alias": "rollback-" + identifier.hex},
        )

    async with _rollback_runtime_pool(result, "butler_general_rw") as pool:
        async with _rollback_runtime_pool(result, "butler_switchboard_rw") as consumer:
            return await _record_rollback_runtime_rows(result, engine, identifier, pool, consumer)


async def _record_rollback_runtime_rows(result, engine, identifier, pool, consumer):
    from butlers.core.dispatch_outcomes import record_dispatch_attempt
    from butlers.core.fleet_conditions import FLEET_FINGERPRINT, FLEET_SOURCE
    from butlers.core.model_routing import CEILING_DENIAL_REASON_PREFIX
    from butlers.tools.switchboard.runtime_attention.outbox import RuntimeAttentionOutbox

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
            text("SELECT id FROM public.runtime_attention_outbox WHERE triggering_attempt_id=:id"),
            {"id": attempts[-1]},
        ).scalar_one()
    fleet_attempt = await record_dispatch_attempt(
        pool,
        catalog_entry_id=identifier,
        butler="general",
        outcome="quota_skip",
        attempt_index=5,
        failure_reason=CEILING_DENIAL_REASON_PREFIX + " synthetic rollback ceiling",
        produce_fleet_halt=True,
    )
    assert type(fleet_attempt) is int
    # infra_conditions is the ordinary observed-fact ledger, not an
    # attention control/receipt table. The timestamp is a deterministic
    # old observation input; only the actual fixed producer may append a
    # protected episode/marker after its database-owned five-minute grace.
    with engine.begin() as observation:
        condition_id = observation.execute(
            text("""INSERT INTO public.infra_conditions
                (source,fingerprint,episode,state,first_detected_at,last_confirmed_at,
                 summary,metadata)
                VALUES (:source,:fingerprint,1,'open',now()-interval '10 minutes',
                        now(),'synthetic receiver observation','{}'::jsonb)
                RETURNING id"""),
            {"source": FLEET_SOURCE, "fingerprint": FLEET_FINGERPRINT},
        ).scalar_one()
    condition_episode = await consumer.fetchval(
        "SELECT public.append_runtime_attention_condition($1)", condition_id
    )
    assert isinstance(condition_episode, uuid.UUID)
    assert (
        await consumer.fetchval(
            "SELECT public.append_runtime_attention_condition($1)", condition_id
        )
        == condition_episode
    )
    with engine.connect() as observer:
        assert (
            observer.execute(
                text(
                    "SELECT episode_id FROM public.runtime_attention_condition_episodes "
                    "WHERE condition_id=:id"
                ),
                {"id": condition_id},
            ).scalar_one()
            == condition_episode
        )
        assert (
            observer.execute(
                text(
                    "SELECT count(*) FROM public.runtime_attention_outbox WHERE source='fleet_halt'"
                )
            ).scalar_one()
            == 1
        )
    repository = RuntimeAttentionOutbox(consumer, instance_id="rollback-proof")
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
    return {"condition": condition_id, "condition_episode": condition_episode}


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
        compile_function_manifest,
        compile_relation_manifest,
        deployment_function_plans,
        relation_security_flags,
    )

    database = unquote(urlparse(result["normal_url"]).path[1:])
    migration_role = unquote(urlparse(result["normal_url"]).username)
    with engine.connect() as observer:
        actor = observer.exec_driver_sql("SELECT session_user").scalar_one()
        manifest = compile_relation_manifest(
            database, migration_role, actor, source_database=database
        )
        profiles = roles.runtime_profiles(database)
        assert len(profiles) == 13
        # REQ021 is independent of compiler/catalog agreeing on the same
        # ordinary LOGIN owner. Observe every installed managed definer's
        # physical owner profile and complete membership isolation directly.
        functions = compile_function_manifest(
            profiles, migration_role, (("core", None, None), *deployment_function_plans(profiles))
        )
        actual_definers = observer.execute(
            text("""
            SELECT n.nspname,p.proname,
              replace(pg_catalog.oidvectortypes(p.proargtypes),', ',','),
              owner.rolname,
              NOT owner.rolcanlogin AND NOT owner.rolinherit AND NOT owner.rolsuper
                AND NOT owner.rolcreatedb AND NOT owner.rolcreaterole
                AND NOT owner.rolreplication AND NOT owner.rolbypassrls,
              NOT EXISTS(SELECT FROM pg_catalog.pg_auth_members membership
                         WHERE membership.roleid=owner.oid OR membership.member=owner.oid)
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner
            WHERE p.prosecdef AND n.nspname=ANY(:schemas)
              AND n.nspname NOT IN ('dashboard_attention','dashboard_stepup')
              AND NOT EXISTS(SELECT FROM pg_catalog.pg_depend dependency
                  WHERE dependency.classid='pg_catalog.pg_proc'::regclass
                    AND dependency.objid=p.oid AND dependency.deptype='e')
        """),
            {"schemas": list(manifest.schemas)},
        ).all()
        assert actual_definers
        for schema, name, signature, owner, profile_ok, isolated in actual_definers:
            assert owner in roles.PURPOSE_OWNERS and owner != migration_role
            assert profile_ok is True and isolated is True
            declared_function = next(
                (row for row in functions if row.identity == (schema, name, signature)), None
            )
            if declared_function is not None:
                assert owner == declared_function.owner
        assert {row.identity for row in functions if row.security_definer} <= {
            (schema, name, signature) for schema, name, signature, *_ in actual_definers
        }
        # Physical privileges below do not take expected owner/rights from the
        # compiler. A compiler and catalog agreeing on a wrong owner must fail.
        from copy import deepcopy

        from butlers.db_authority.catalog import _CATALOG_OBJECT_SQL
        from butlers.db_authority.grants import serving_catalog_contract

        contract = serving_catalog_contract(database)
        statement = _CATALOG_OBJECT_SQL.replace("%", "%%").replace("$1", "%(catalog)s")
        assert (
            observer.exec_driver_sql(statement, {"catalog": json.dumps(contract)}).scalar_one()
            is True
        )
        target = next(
            row
            for row in functions
            if row.name == "resolve_relationship_prepared_action_status" and row.security_definer
        )
        signature = f'"{target.schema}"."{target.name}"({target.signature})'
        wrong = deepcopy(contract)
        for row in wrong["functions"]:
            if (row["schema"], row["name"], row["signature"]) == target.identity:
                row["owner"] = migration_role
        with observer.begin_nested() as reverse_owner:
            quoted_owner = '"' + migration_role.replace('"', '""') + '"'
            observer.exec_driver_sql(f"ALTER FUNCTION {signature} OWNER TO {quoted_owner}")
            assert (
                observer.exec_driver_sql(statement, {"catalog": json.dumps(wrong)}).scalar_one()
                is False
            )
            reverse_owner.rollback()
        for attribute in ("LOGIN", "INHERIT"):
            with observer.begin_nested() as reverse_attribute:
                observer.exec_driver_sql(f'ALTER ROLE "{target.owner}" {attribute}')
                assert (
                    observer.exec_driver_sql(
                        statement, {"catalog": json.dumps(contract)}
                    ).scalar_one()
                    is False
                )
                reverse_attribute.rollback()
        with observer.begin_nested() as reverse_membership:
            observer.exec_driver_sql(f'GRANT "{target.owner}" TO "butler_general_rw"')
            assert (
                observer.exec_driver_sql(statement, {"catalog": json.dumps(contract)}).scalar_one()
                is False
            )
            reverse_membership.rollback()
        assert (
            observer.exec_driver_sql(statement, {"catalog": json.dumps(contract)}).scalar_one()
            is True
        )
        for role, relation, verb, expected in (
            ("connector_partition_owner", "connectors.filtered_events", "INSERT", True),
            ("registry_control_owner", "switchboard.butler_boot_registrations", "SELECT", False),
            ("model_probe_owner", "public.model_catalog", "UPDATE", False),
            ("cost_claim_restore_owner", "public.cost_claims", "INSERT", True),
            ("premise_amendment_owner", "public.insight_amendments", "INSERT", False),
        ):
            assert (
                observer.execute(
                    text("SELECT pg_catalog.has_table_privilege(:role,:relation,:verb)"),
                    {"role": role, "relation": relation, "verb": verb},
                ).scalar_one()
                is expected
            )
        for role, relation, column, verb in (
            (
                "registry_control_owner",
                "switchboard.butler_boot_registrations",
                "boot_epoch",
                "SELECT",
            ),
            ("model_probe_owner", "public.model_catalog", "last_verified_ok", "UPDATE"),
            ("premise_amendment_owner", "public.insight_amendments", "candidate_id", "INSERT"),
        ):
            assert (
                observer.execute(
                    text("SELECT pg_catalog.has_column_privilege(:role,:relation,:column,:verb)"),
                    {"role": role, "relation": relation, "column": column, "verb": verb},
                ).scalar_one()
                is True
            )
        assert (
            observer.exec_driver_sql(
                "SELECT has_sequence_privilege('cost_claim_restore_owner',"
                "'public.cost_claim_events_id_seq','UPDATE') "
                "AND NOT has_sequence_privilege('cost_claim_restore_owner',"
                "'public.cost_claim_events_id_seq','USAGE')"
            ).scalar_one()
            is True
        )
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
                        "SELECT relispartition FROM pg_catalog.pg_class "
                        "WHERE oid=CAST(:name AS regclass)"
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

    # Fixed registry purpose: actual row-trigger initialization, boot CAS,
    # control-plane mutation, QA projection and Finance pure READ all execute
    # under their original subjects. No fixture writes to control/receipt rows.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql('SET LOCAL ROLE "' + migration_role.replace('"', '""') + '"')
            for name in ("finance", "qa"):
                connection.execute(
                    text(
                        "INSERT INTO switchboard.butler_registry(name,endpoint_url) "
                        "VALUES (:name,'http://synthetic.invalid') ON CONFLICT (name) DO NOTHING"
                    ),
                    {"name": name},
                )
            boot = str(uuid.uuid4())
            connection.exec_driver_sql("SET LOCAL ROLE butler_finance_rw")
            epoch = connection.execute(
                text("SELECT public.register_butler_boot('finance',CAST(:boot AS uuid))"),
                {"boot": boot},
            ).scalar_one()
            assert type(epoch) is int and epoch > 0
            assert (
                connection.execute(
                    text("SELECT public.register_butler_boot('finance',CAST(:boot AS uuid))"),
                    {"boot": boot},
                ).scalar_one()
                == epoch
            )
            assert (
                connection.execute(
                    text("SELECT public.finance_warning_boot_current(CAST(:boot AS uuid),:epoch)"),
                    {"boot": boot, "epoch": epoch},
                ).scalar_one()
                is True
            )
            with connection.begin_nested() as denied_read:
                with pytest.raises(DBAPIError) as denied:
                    connection.exec_driver_sql(
                        "SELECT * FROM switchboard.butler_registry_control_plane"
                    )
                assert denied.value.orig.pgcode == "42501"
                denied_read.rollback()
            connection.exec_driver_sql("SET LOCAL ROLE butler_switchboard_rw")
            reserved = connection.execute(
                text("SELECT * FROM public.reserve_butler_probe('finance')")
            ).one()
            assert reserved.boot_epoch == epoch and reserved.probe_sequence > 0
            assert (
                connection.execute(
                    text(
                        "SELECT public.record_butler_probe('finance',:epoch,:sequence,true,true,true,NULL)"
                    ),
                    {"epoch": epoch, "sequence": reserved.probe_sequence},
                ).scalar_one()
                is True
            )
            connection.exec_driver_sql("SET LOCAL ROLE dashboard_api")
            for name in ("finance", "qa"):
                assert (
                    connection.execute(
                        text("SELECT public.set_butler_registry_policy(:name,'active')"),
                        {"name": name},
                    ).scalar_one()
                    == "active"
                )
            connection.exec_driver_sql("SET LOCAL ROLE butler_qa_rw")
            assert tuple(
                connection.exec_driver_sql("SELECT * FROM public.qa_local_schedule_policy()").one()
            ) == ("active", "operator")
            connection.exec_driver_sql("RESET ROLE")
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM switchboard.butler_boot_registrations h "
                        "JOIN switchboard.butler_registry_control_plane c ON c.name=h.name "
                        "AND c.boot_epoch=h.boot_epoch AND c.boot_instance_id=h.boot_instance_id "
                        "WHERE h.name='finance' AND h.boot_instance_id=CAST(:boot AS uuid) "
                        "AND h.boot_epoch=:epoch AND c.recorded_probe_sequence=:sequence"
                    ),
                    {"boot": boot, "epoch": epoch, "sequence": reserved.probe_sequence},
                ).scalar_one()
                == 1
            )
        finally:
            transaction.rollback()

    # Execute real body dependencies under their existing legitimate subjects.
    # Empty read results are successful fixed READ calls, not missing-function
    # exceptions. Partition DDL and the premise INSERT below require their new
    # ownership/RLS rights to work; all effects are rolled back independently.
    missing = str(uuid.uuid4())
    for sql in (
        "SELECT count(*) FROM public.resolve_relationship_prepared_action_status(CAST(:id AS uuid))",
        "SELECT count(*) FROM public.resolve_finance_bill_status(CAST(:id AS uuid))",
        "SELECT count(*) FROM public.resolve_owner_triple('owner-channel', ARRAY[:id])",
    ):
        assert probe("butler_switchboard_rw", sql, {"id": missing}) == 0
    assert (
        probe(
            "butler_switchboard_rw",
            "SELECT public.record_runtime_probe_verification(CAST(:id AS uuid),true,1,NULL)",
            {"id": missing},
        )
        is False
    )
    assert (
        probe(
            "butler_switchboard_rw",
            "SELECT public.resolve_health_medication_refill(:subject,1,1)",
            {"subject": "missing-authority-" + missing},
        )
        is None
    )
    assert (
        probe(
            "dashboard_api",
            "SELECT outcome FROM public.dashboard_turn_dispatch_status(CAST(:id AS uuid))",
            {"id": missing},
        )
        == "missing"
    )
    probe("dashboard_auth_api", "SELECT dashboard_auth.cleanup()")
    assert isinstance(
        probe(migration_role, "SELECT dashboard_auth.host('status','{}'::jsonb)"), dict
    )
    with pytest.raises(DBAPIError) as host_denied:
        probe("dashboard_auth_api", "SELECT dashboard_auth.host('status','{}'::jsonb)")
    assert host_denied.value.orig.pgcode == "42501"
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql('SET LOCAL ROLE "' + migration_role.replace('"', '""') + '"')
            claim_id = str(uuid.uuid4())
            payload = {
                "id": claim_id,
                "claim_key": "authority-restore-" + claim_id,
                "asserted_by": "general",
                "asserted_by_role": "butler_general_rw",
                "kind": "receivable",
                "direction": "inbound",
                "amount": 1,
                "currency": "SGD",
                "counterparty_label": "synthetic",
                "description": "Synthetic purpose-owner restore witness",
                "asserted_at": "2080-01-01T00:00:00+00:00",
            }
            connection.execute(
                text("SELECT public.cost_claim_restore_row('cost_claims',CAST(:payload AS jsonb))"),
                {"payload": json.dumps(payload)},
            ).one()
            connection.exec_driver_sql("RESET ROLE")
            assert (
                connection.execute(
                    text("SELECT description FROM public.cost_claims WHERE id=CAST(:id AS uuid)"),
                    {"id": claim_id},
                ).scalar_one()
                == payload["description"]
            )
            # The actual invoker audit trigger must honor the isolated restore
            # purpose without emitting a second historical assertion event.
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM public.cost_claim_events WHERE claim_id=CAST(:id AS uuid)"
                    ),
                    {"id": claim_id},
                ).scalar_one()
                == 0
            )
        finally:
            transaction.rollback()
    for role, function in (
        ("connector_writer", "connectors.connectors_filtered_events_ensure_partition"),
        (
            "butler_switchboard_rw",
            "switchboard.switchboard_connector_heartbeat_log_ensure_partition",
        ),
        ("butler_switchboard_rw", "switchboard.switchboard_message_inbox_ensure_partition"),
    ):
        assert isinstance(probe(role, f"SELECT {function}('2198-02-01'::timestamptz)"), str)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql('SET LOCAL ROLE "' + migration_role.replace('"', '""') + '"')
            identifier = str(uuid.uuid4())
            connection.execute(
                text(
                    "INSERT INTO public.insight_candidates "
                    "(id,origin_butler,priority,category,dedup_key,expires_at,message,status,"
                    "delivered_at,premise,delivery_ref) VALUES "
                    "(CAST(:id AS uuid),'general',80,'synthetic',:key,now()+interval '1 day',"
                    "'Synthetic purpose witness','delivered',now()-interval '1 minute',"
                    "jsonb_build_object('kind','owner_condition','source',:key,'fingerprint',:key),"
                    "jsonb_build_object('synthetic',true))"
                ),
                {"id": identifier, "key": "authority-purpose-" + identifier},
            )
            connection.exec_driver_sql("SET LOCAL ROLE butler_general_rw")
            assert (
                connection.execute(
                    text("SELECT public.enqueue_premise_amendments(:key,:key,now(),'synthetic')"),
                    {"key": "authority-purpose-" + identifier},
                ).scalar_one()
                == 1
            )
            connection.exec_driver_sql("RESET ROLE")
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM public.insight_amendments WHERE candidate_id=CAST(:id AS uuid)"
                    ),
                    {"id": identifier},
                ).scalar_one()
                == 1
            )
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
                                  AND l.classid=:family AND l.objid=:operation AND l.objsubid=2)
                            """),
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
        evidence = await _record_real_rollback_attention_evidence(result, engine)
        before = _rollback_observer(url)
        assert before["rows"] and before["dnd"] and before["trigger"]
        assert before["outbox"] and before["leases"]
        assert before["condition_markers"] and before["condition_facts"] and before["attempts"]
        assert all(before["controls"].values())
        release_references = _render_pinned_release_profiles(result)
        with operator_rollback_session(url) as held:
            capsule = apply.capture_rollback_capsule(
                held,
                normal_role,
                release_references,
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
        _prove_actual_released_native_profile(result, before)
        with engine.connect() as connection:
            connection.exec_driver_sql("SET ROLE butler_general_rw")
            assert (
                connection.exec_driver_sql(
                    "SELECT value->>'synthetic' FROM general.state "
                    "WHERE key='authority-rollback-proof'"
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
        _prove_current_condition_marker_transition(result, engine, rebuilt)
        # All thirteen real roster capabilities share the actual migrated
        # deployment; no narrowed single-Chronicler fixture can mask an omitted
        # Concierge, standalone QA or private Memory declaration.
        _observe_rebuilt_relation_matrix(result, engine)
        await _prove_rollback_fixture_cleanup_faults(result, monkeypatch)
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
        await _prove_reverse_commit_ack_and_lock_boundaries(
            result, engine, monkeypatch, capsule, before, rebuilt
        )
        receipt = run_preserving_rollback(url, capsule, transition="R1")
        assert receipt["state"] == "predecessor_restored" and receipt["transition"] == "R1"
        # A fresh connection observes full data/stamp equality and the actual
        # retired trigger restored, rather than an exception in a shape oracle.
        assert _rollback_observer(url) == before
        with engine.connect() as connection:
            connection.exec_driver_sql("SET ROLE butler_general_rw")
            assert (
                connection.exec_driver_sql(
                    "SELECT value->>'synthetic' FROM general.state "
                    "WHERE key='authority-rollback-proof'"
                ).scalar_one()
                == "true"
            )
            with pytest.raises(DBAPIError) as refused:
                connection.exec_driver_sql("TRUNCATE public.dnd_generation_mutations")
            assert refused.value.orig.pgcode == "42501"
            connection.rollback()
        await _prove_restored_attention_no_refire(result, evidence, before)
        _prove_actual_released_native_profile(result, before)
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


async def _close_rollback_pool(pool):
    """Retain exactly this native pool close through reset and cancellation."""
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
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if cancelled is None:
                cancelled = exc
    task.result()
    if cancelled is not None:
        raise cancelled


@asynccontextmanager
async def _rollback_runtime_pool(result, capability):
    """Native LOGIN has only its explicitly selected source capability.

    No fixture table/function/schema privilege or admission override is used.
    General cannot implicitly choose the Switchboard outbox consumer role.
    Only an acknowledged CREATE permits this helper to revoke/drop its login.
    """
    from urllib.parse import quote

    from butlers.db import register_jsonb_codec

    if capability not in {"butler_general_rw", "butler_switchboard_rw"}:
        raise ValueError("Rollback fixture capability is not declared")
    parsed = urlparse(result["admin_url"])
    login = "rollback_runtime_" + uuid.uuid4().hex[:16]
    admin = await asyncpg.connect(parsed._replace(scheme="postgresql").geturl(), timeout=5)
    pool = None
    created = False
    creation_unknown = False
    primary = None

    async def active_role(connection):
        await connection.execute('SET ROLE "' + capability + '"')

    async def release():
        errors = []
        try:
            if pool is not None:
                try:
                    await _close_rollback_pool(pool)
                except BaseException as exc:
                    errors.append(exc)
            if creation_unknown:
                # Absence before CREATE and existence after an ambiguous ACK
                # cannot identify the creator. Preserve that identity and
                # report incomplete cleanup instead of destructive guessing.
                errors.append(RuntimeError("Rollback role creation ownership is unknown"))
            if created:
                exists = None
                try:
                    exists = await admin.fetchval(
                        "SELECT EXISTS(SELECT FROM pg_catalog.pg_roles WHERE rolname=$1)",
                        login,
                        timeout=5,
                    )
                except BaseException as exc:
                    errors.append(exc)
                if exists is True:
                    for sql in (
                        'REVOKE "' + capability + '" FROM "' + login + '"',
                        'DROP ROLE "' + login + '"',
                    ):
                        try:
                            await admin.execute(sql, timeout=5)
                        except BaseException as exc:
                            errors.append(exc)
                elif exists is not False:
                    errors.append(RuntimeError("Owned rollback role cleanup is unverified"))
        except BaseException as exc:
            errors.append(exc)
        finally:
            # Independent closure must still run when any role lookup or
            # earlier resource cleanup fails. Never replace the primary fault.
            try:
                await admin.close(timeout=5)
            except BaseException as exc:
                errors.append(exc)
                try:
                    admin.terminate()
                except BaseException as terminate_error:
                    errors.append(terminate_error)
        return errors

    try:
        assert (
            await admin.fetchval(
                "SELECT NOT EXISTS(SELECT FROM pg_catalog.pg_roles WHERE rolname=$1)", login
            )
            is True
        )
        creation_unknown = True
        try:
            await admin.execute(
                'CREATE ROLE "' + login + '" LOGIN NOINHERIT NOSUPERUSER NOCREATEDB '
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD 'synthetic-test-only'",
                timeout=5,
            )
        except asyncpg.DuplicateObjectError:
            # Actual duplicate is known not to be this CREATE's identity.
            creation_unknown = False
            raise
        created = True  # Only this successful CREATE acknowledgement is proof.
        creation_unknown = False
        await admin.execute('GRANT "' + capability + '" TO "' + login + '"', timeout=5)
        host = parsed.hostname
        if ":" in host:
            host = "[" + host + "]"
        netloc = quote(login, safe="") + ":synthetic-test-only@" + host
        if parsed.port:
            netloc += ":" + str(parsed.port)
        pool = asyncpg.create_pool(
            parsed._replace(scheme="postgresql", netloc=netloc).geturl(),
            min_size=0,
            max_size=2,
            timeout=5,
            command_timeout=5,
            init=register_jsonb_codec,
            setup=active_role,
        )
        await pool
        assert type(pool) is asyncpg.Pool
        assert await pool.fetchval("SELECT session_user") == login
        assert await pool.fetchval("SELECT current_user") == capability
        yield pool
    except BaseException as exc:
        primary = exc
        raise
    finally:
        task = asyncio.create_task(release())
        cancelled = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if cancelled is None:
                    cancelled = exc
        errors = task.result()
        if errors:
            if primary is not None:
                primary.add_note("Rollback fixture native identity cleanup incomplete")
            elif cancelled is None:
                raise RuntimeError(
                    "Rollback fixture native identity cleanup incomplete"
                ) from errors[0]
        if cancelled is not None and primary is None:
            raise cancelled


async def _prove_rollback_fixture_cleanup_faults(result, monkeypatch):
    """Actual PostgreSQL/native pools, with only precise driver-return faults.

    These controls execute inside the existing real R0/R1 owner. They do not
    replace serving admission, fake a SQL verdict or claim a local execution.
    A separately acknowledged control CREATE owns any preserved foreign login;
    the helper under test must never infer that ownership for itself.
    """
    connect = asyncpg.connect
    create_pool = asyncpg.create_pool
    dsn = urlparse(result["admin_url"])._replace(scheme="postgresql").geturl()

    class SetupReturnFailure(RuntimeError):
        pass

    class CleanupLookupFailure(RuntimeError):
        pass

    async def exercise_scenario(scenario):
        connections = []
        pools = []
        identity = {"name": None, "acknowledged_control_create": False}
        destructive = []
        role_before = None
        close_entered = asyncio.Event()
        release_close = asyncio.Event()
        task = None

        async def role_state(connection):
            row = await connection.fetchrow(
                "SELECT oid,rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
                "rolcreaterole,rolreplication,rolbypassrls "
                "FROM pg_catalog.pg_roles WHERE rolname=$1",
                identity["name"],
                timeout=5,
            )
            return None if row is None else tuple(row)

        class ObservedAdmin:
            def __init__(self, connection):
                self.connection = connection
                self.close_attempted = False

            def __getattr__(self, name):
                return getattr(self.connection, name)

            async def fetchval(self, statement, *args, **kwargs):
                nonlocal role_before
                if statement.startswith("SELECT NOT EXISTS("):
                    identity["name"] = args[0]
                    if scenario == "preexisting":
                        await self.connection.execute(
                            'CREATE ROLE "' + identity["name"] + '" NOLOGIN NOINHERIT '
                            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS",
                            timeout=5,
                        )
                        identity["acknowledged_control_create"] = True
                        role_before = await role_state(self.connection)
                if scenario == "cleanup_lookup_failure" and statement.startswith("SELECT EXISTS("):
                    raise CleanupLookupFailure
                return await self.connection.fetchval(statement, *args, **kwargs)

            async def execute(self, statement, *args, **kwargs):
                nonlocal role_before
                if statement.startswith('CREATE ROLE "'):
                    if scenario == "duplicate":
                        # An independently acknowledged CREATE races the exact
                        # helper CREATE. Its second actual SQL must raise the
                        # native DuplicateObjectError, not a fabricated result.
                        await self.connection.execute(statement, *args, **kwargs)
                        identity["acknowledged_control_create"] = True
                        role_before = await role_state(self.connection)
                    result_value = await self.connection.execute(statement, *args, **kwargs)
                    identity["acknowledged_control_create"] = True
                    if scenario == "unknown_create_ack":
                        role_before = await role_state(self.connection)
                        # SQL completed, but its acknowledgement is withheld
                        # from the fixture. The control still owns its CREATE.
                        raise asyncio.CancelledError
                    return result_value
                if statement.startswith(('REVOKE "', 'DROP ROLE "')):
                    destructive.append(statement.split(" ", 1)[0])
                return await self.connection.execute(statement, *args, **kwargs)

            async def close(self, **kwargs):
                self.close_attempted = True
                if scenario == "repeated_cancel":
                    close_entered.set()
                    await asyncio.wait_for(release_close.wait(), timeout=5)
                await self.connection.close(**kwargs)

        async def observed_connect(*args, **kwargs):
            observed = ObservedAdmin(await connect(*args, **kwargs))
            connections.append(observed)
            return observed

        def observed_pool(*args, **kwargs):
            original_init = kwargs["init"]

            async def init(connection):
                await original_init(connection)
                if scenario in {"partial_setup", "cleanup_lookup_failure"}:
                    raise SetupReturnFailure

            kwargs["init"] = init
            pool = create_pool(*args, **kwargs)
            pools.append(pool)
            return pool

        async def exercise():
            async with _rollback_runtime_pool(result, "butler_general_rw") as pool:
                assert await pool.fetchval("SELECT 1") == 1

        try:
            with monkeypatch.context() as fault:
                fault.setattr(asyncpg, "connect", observed_connect)
                fault.setattr(asyncpg, "create_pool", observed_pool)
                if scenario == "repeated_cancel":
                    task = asyncio.create_task(exercise())
                    await asyncio.wait_for(close_entered.wait(), timeout=15)
                    task.cancel()
                    await asyncio.sleep(0)
                    task.cancel()
                    await asyncio.sleep(0)
                    assert not task.done()
                    assert all(pool.get_size() == 0 for pool in pools)
                    release_close.set()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, timeout=15)
                elif scenario in {"partial_setup", "cleanup_lookup_failure"}:
                    with pytest.raises(SetupReturnFailure) as primary:
                        await asyncio.wait_for(exercise(), timeout=20)
                    if scenario == "cleanup_lookup_failure":
                        assert any(
                            note == "Rollback fixture native identity cleanup incomplete"
                            for note in primary.value.__notes__
                        )
                elif scenario == "preexisting":
                    with pytest.raises(AssertionError):
                        await asyncio.wait_for(exercise(), timeout=20)
                elif scenario == "duplicate":
                    with pytest.raises(asyncpg.DuplicateObjectError):
                        await asyncio.wait_for(exercise(), timeout=20)
                elif scenario == "unknown_create_ack":
                    with pytest.raises(asyncio.CancelledError) as primary:
                        await asyncio.wait_for(exercise(), timeout=20)
                    assert any(
                        note == "Rollback fixture native identity cleanup incomplete"
                        for note in primary.value.__notes__
                    )
                else:
                    await asyncio.wait_for(exercise(), timeout=20)
            assert all(connection.close_attempted for connection in connections)
            assert all(connection.connection.is_closed() for connection in connections)
            assert all(pool.get_size() == 0 for pool in pools)
            observer = await connect(dsn, timeout=5)
            try:
                after = await role_state(observer)
                if scenario in {"preexisting", "duplicate", "unknown_create_ack"}:
                    assert destructive == []
                    assert after == role_before and after is not None
                elif scenario == "cleanup_lookup_failure":
                    assert destructive == []
                    assert after is not None
                else:
                    assert after is None
                    assert destructive == ["REVOKE", "DROP"]
            finally:
                await observer.close(timeout=5)
        finally:
            primary_error = sys.exception()

            async def cleanup_control():
                errors = []
                release_close.set()
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=20)
                    except asyncio.CancelledError:
                        pass
                    except BaseException as error:
                        errors.append(error)
                for native_pool in pools:
                    if native_pool.get_size():
                        try:
                            await _close_rollback_pool(native_pool)
                        except BaseException as error:
                            errors.append(error)
                for observed in connections:
                    if not observed.connection.is_closed():
                        try:
                            await observed.connection.close(timeout=5)
                        except BaseException as error:
                            errors.append(error)
                            observed.connection.terminate()
                # Only this control's acknowledged native CREATE authorizes
                # independent cleanup, including unknown-to-helper outcomes.
                # Random name or existence alone never authorizes a DROP.
                if identity["acknowledged_control_create"]:
                    cleanup = None
                    try:
                        cleanup = await connect(dsn, timeout=5)
                        await cleanup.execute(
                            'DROP ROLE IF EXISTS "' + identity["name"] + '"', timeout=5
                        )
                    except BaseException as error:
                        errors.append(error)
                    finally:
                        if cleanup is not None:
                            try:
                                await cleanup.close(timeout=5)
                            except BaseException as error:
                                errors.append(error)
                                cleanup.terminate()
                return errors

            cleanup_task = asyncio.create_task(cleanup_control())
            cancelled = None
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError as error:
                    if cancelled is None:
                        cancelled = error
            cleanup_errors = cleanup_task.result()
            if cleanup_errors:
                if primary_error is not None:
                    primary_error.add_note("Native cleanup-control release is incomplete")
                elif cancelled is None:
                    raise RuntimeError(
                        "Native cleanup-control release is incomplete"
                    ) from cleanup_errors[0]
            if cancelled is not None and primary_error is None:
                raise cancelled

    for scenario in (
        "acknowledged",
        "partial_setup",
        "preexisting",
        "duplicate",
        "unknown_create_ack",
        "cleanup_lookup_failure",
        "repeated_cancel",
    ):
        await exercise_scenario(scenario)


async def _prove_restored_attention_no_refire(result, evidence, before):
    """Restored real producer returns the durable marker instead of re-paging."""
    async with _rollback_runtime_pool(result, "butler_switchboard_rw") as pool:
        assert (
            await pool.fetchval(
                "SELECT public.append_runtime_attention_condition($1)", evidence["condition"]
            )
            == evidence["condition_episode"]
        )
    assert _rollback_observer(result["admin_url"]) == before


async def _prove_reverse_commit_ack_and_lock_boundaries(
    result, engine, monkeypatch, capsule, before, rebuilt
):
    """Native reverse, commit-return faults and real maintenance-lock exclusion.

    Only final commit return is faulted; validation/SQL/Alembic are unchanged.
    PostgreSQL observation establishes durable truth and maintenance-lock order.
    This is not a network-loss or live deployment/RB06 experiment.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from urllib.parse import parse_qsl, urlencode

    from butlers import migrations

    class BeforeReverseCommit(RuntimeError):
        pass

    class AfterReverseCommitAcknowledgement(RuntimeError):
        pass

    original_session = migrations.operator_rollback_session

    @contextmanager
    def fail_before_commit(url):
        with original_session(url) as held:
            commit = held.commit
            first = True

            def injected_commit():
                nonlocal first
                if first:
                    first = False
                    raise BeforeReverseCommit
                return commit()

            held.commit = injected_commit
            try:
                yield held
            finally:
                held.commit = commit

    with monkeypatch.context() as fault:
        fault.setattr(migrations, "operator_rollback_session", fail_before_commit)
        with pytest.raises(BeforeReverseCommit):
            migrations.run_preserving_rollback(result["admin_url"], capsule, transition="R1")
    assert _rollback_observer(result["admin_url"]) == rebuilt

    committed = Event()
    release_ack = Event()
    first_identity = {}

    @contextmanager
    def lose_commit_ack(url):
        with original_session(url) as held:
            first_identity["pid"] = held.exec_driver_sql(
                "SELECT pg_catalog.pg_backend_pid()"
            ).scalar_one()
            commit = held.commit
            first = True

            def injected_commit():
                nonlocal first
                if not first:
                    return commit()
                first = False
                commit()
                committed.set()
                if not release_ack.wait(90):
                    raise TimeoutError("Reverse acknowledgement barrier was not released")
                raise AfterReverseCommitAcknowledgement

            held.commit = injected_commit
            try:
                yield held
            finally:
                held.commit = commit

    application = "authority-reverse-waiter-" + uuid.uuid4().hex
    parsed = urlparse(result["admin_url"])
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k != "application_name"]
    selected = parsed._replace(query=urlencode([*query, ("application_name", application)]))

    def second_maintenance_observer():
        # This is the actual production PRE-cutover maintenance entry, not an
        # ordinary initializer. R1 has restored a bootstrap-owned database, so
        # ordinary migration admission is intentionally unavailable here.
        # The original unpatched entry verifies the actual superuser actor and
        # polls the identical physical migration lock before yielding.
        with original_session(selected.geturl()) as held:
            from butlers.db_authority.apply import require_rollback_lock

            require_rollback_lock(held)
            assert held.exec_driver_sql("SELECT current_user=session_user").scalar_one()
            assert _rollback_observer(result["admin_url"]) == before
        return None

    with monkeypatch.context() as fault, ThreadPoolExecutor(max_workers=2) as executor:
        fault.setattr(migrations, "operator_rollback_session", lose_commit_ack)
        first = executor.submit(
            migrations.run_preserving_rollback, result["admin_url"], capsule, transition="R1"
        )
        second = None
        try:
            assert committed.wait(90) is True
            assert _rollback_observer(result["admin_url"]) == before
            second = executor.submit(second_maintenance_observer)
            deadline = time.monotonic() + 10
            observed = False
            while time.monotonic() < deadline and not second.done():
                with engine.connect() as observer:
                    observed = observer.execute(
                        text("""SELECT
                          EXISTS(SELECT FROM pg_catalog.pg_locks
                            WHERE pid=:first AND locktype='advisory' AND granted
                              AND classid=:family AND objid=:operation AND objsubid=2)
                          AND EXISTS(SELECT FROM pg_catalog.pg_stat_activity a
                            WHERE a.application_name=:application
                              AND a.datname=current_database()
                              AND a.query LIKE '%pg_try_advisory_lock%'
                              AND NOT EXISTS(SELECT FROM pg_catalog.pg_locks l
                                WHERE l.pid=a.pid AND l.locktype='advisory' AND l.granted
                                  AND l.classid=:family AND l.objid=:operation AND l.objsubid=2))
                        """),
                        {
                            "first": first_identity["pid"],
                            "application": application,
                            "family": migrations._MIGRATION_LOCK[0],
                            "operation": migrations._MIGRATION_LOCK[1],
                        },
                    ).scalar_one()
                if observed:
                    break
                await asyncio.sleep(0.02)
            assert observed is True
            assert not second.done()
        finally:
            release_ack.set()
            with pytest.raises(AfterReverseCommitAcknowledgement):
                first.result(timeout=90)
            if second is not None:
                assert second.result(timeout=90) is None
    # Both physical maintenance sessions are now closed. Only at this point
    # may the supported operator owner/bootstrap preparation run; the existing
    # helper then invokes the unchanged actual CLI rebuild and full finalizer.
    assert _rollback_observer(result["admin_url"]) == before
    await _apply_real_rebuild(result, monkeypatch)
    assert _rollback_observer(result["admin_url"]) == rebuilt
    with engine.connect() as observer:
        assert (
            observer.execute(
                text(
                    "SELECT count(*) FROM pg_catalog.pg_stat_activity "
                    "WHERE pid=:first OR application_name=:application"
                ),
                {"first": first_identity["pid"], "application": application},
            ).scalar_one()
            == 0
        )


class RollbackReleaseProfileMismatch(AssertionError):
    """Only a rendered release invariant, never a renderer/setup error."""


def _render_pinned_release_profiles(result):
    """Run actual pinned Helm and Compose producers; never activate a release.

    No inherited .env or secret store is consulted. Required external renderer
    binaries are explicit prerequisites: absence/failure is setup UNKNOWN, not
    an omitted-reference mutant kill. Rendered credentials remain RAM-only.
    """
    import hashlib
    import re
    import shutil

    import yaml

    source = result["released_source"]
    chart = source / "deploy/helm/butlers"
    compose = source / "docker-compose.yml"
    if shutil.which("helm") is None or shutil.which("docker") is None:
        raise RuntimeError("Pinned release renderer prerequisite is unavailable")
    values = yaml.safe_load((chart / "values.yaml").read_text())
    expected_keys = values["externalSecrets"]["data"]
    cache = source.parent / "render-owned"
    cache.mkdir(exist_ok=True)
    empty_env = cache / "empty.env"
    empty_env.write_text("")
    native = urlparse(result["normal_url"])
    # Values belong only to this disposable database; they never leave RAM.
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(cache),
        "HELM_CACHE_HOME": str(cache / "cache"),
        "HELM_CONFIG_HOME": str(cache / "config"),
        "HELM_DATA_HOME": str(cache / "data"),
        "POSTGRES_HOST": native.hostname,
        "POSTGRES_PORT": str(native.port or 5432),
        "POSTGRES_USER": unquote(native.username),
        "POSTGRES_PASSWORD": unquote(native.password),
        "POSTGRES_SSLMODE": "disable",
        "BUTLERS_APP_TAG": _PREDECESSOR,
    }
    for key in re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\:\?", compose.read_text()):
        environment.setdefault(key, "synthetic-render-only")

    def render(command, selected_environment):
        try:
            process = subprocess.run(
                command,
                cwd=source,
                env=selected_environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("Pinned release rendering did not complete") from None
        if process.returncode:
            raise RuntimeError("Pinned release rendering did not complete")
        return process.stdout

    helm_command = [
        "helm",
        "template",
        "authority-old",
        str(chart),
        "--set-string",
        "image.tag=" + _PREDECESSOR,
        "--set-string",
        "frontendImage.tag=" + _PREDECESSOR,
        "--set-string",
        "database.host=127.0.0.1",
        "--set-string",
        "database.name=" + unquote(native.path[1:]),
    ]
    compose_command = [
        "docker",
        "compose",
        "--env-file",
        str(empty_env),
        "-f",
        str(compose),
        "config",
        "--format",
        "json",
    ]

    def verify_helm(rendered):
        documents = list(yaml.safe_load_all(rendered))
        deployment = next(
            x
            for x in documents
            if x and x.get("kind") == "Deployment" and x["metadata"]["name"] == "butlers-up"
        )
        external = next(
            x
            for x in documents
            if x
            and x.get("kind") == "ExternalSecret"
            and x["metadata"]["name"] == "authority-old-bws"
        )
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        env = {x["name"]: x for x in container["env"]}
        keys = {x["secretKey"]: x["remoteRef"]["key"] for x in external["spec"]["data"]}
        expected_image = values["image"]["repository"] + ":" + _PREDECESSOR
        if (
            container["image"] != expected_image
            or external["spec"]["target"]["name"] != "authority-old-bws"
            or any(
                keys.get(key) != expected_keys[key]
                for key in ("POSTGRES_USER", "POSTGRES_PASSWORD")
            )
            or any(
                env[key].get("valueFrom", {}).get("secretKeyRef")
                != {"name": "authority-old-bws", "key": key}
                for key in ("POSTGRES_USER", "POSTGRES_PASSWORD")
            )
            or container["readinessProbe"]["httpGet"] != {"path": "/health", "port": "daemon"}
        ):
            raise RollbackReleaseProfileMismatch("Rendered predecessor profile differs")
        return expected_image

    def verify_compose(rendered):
        service = json.loads(rendered)["services"]["butlers-up"]
        env = service["environment"]
        if (
            service["image"] != "butlers-app:" + _PREDECESSOR
            or env["POSTGRES_USER"] != unquote(native.username)
            or env["POSTGRES_PASSWORD"] != unquote(native.password)
            or service["healthcheck"]["test"]
            != ["CMD-SHELL", "curl -sf http://localhost:41100/health || exit 1"]
            or service.get("profiles")
        ):
            raise RollbackReleaseProfileMismatch("Rendered predecessor profile differs")

    image = verify_helm(render(helm_command, environment))
    verify_compose(render(compose_command, environment))
    # Real renderer mutation: the old credential key no longer resolves from
    # its adopted named source. Only the dedicated invariant rejection counts.
    with pytest.raises(RollbackReleaseProfileMismatch):
        verify_helm(
            render(
                [*helm_command, "--set-string", "externalSecrets.data.POSTGRES_USER="], environment
            )
        )
    with pytest.raises(RollbackReleaseProfileMismatch):
        verify_compose(render(compose_command, environment | {"POSTGRES_USER": "wrong-reference"}))
    assert verify_helm(render(helm_command, environment)) == image
    verify_compose(render(compose_command, environment))
    return {
        "chart": _PREDECESSOR
        + ":"
        + hashlib.sha256((chart / "Chart.yaml").read_bytes()).hexdigest(),
        "image": image,
        "old_secret": "authority-old-bws:POSTGRES_USER:POSTGRES_PASSWORD:"
        + expected_keys["POSTGRES_USER"]
        + ":"
        + expected_keys["POSTGRES_PASSWORD"],
    }


_PREDECESSOR_PROFILE = r"""
import asyncio
import json
import os
import sys
from pathlib import Path
import butlers
from butlers.config import load_config
from butlers.db import Database, db_params_from_env, database_name_from_env

request = json.loads(sys.stdin.read())
root = Path.cwd().resolve()
assert Path(butlers.__file__).resolve().is_relative_to(root / 'src')
os.environ['DATABASE_URL'] = request['normal_url']
config = load_config(root / 'roster/general')

async def probe():
    database = Database(database_name_from_env(config.db_name), schema=config.db_schema,
                        role='butler_' + config.name + '_rw',
                        min_pool_size=0, max_pool_size=1, **db_params_from_env())
    try:
        pool = await database.connect()
        async with pool.acquire() as connection:
            assert await connection.fetchval('SELECT current_user') == 'butler_general_rw'
            assert await connection.fetchval(
                "SELECT value->>'synthetic' FROM general.state "
                    "WHERE key='authority-rollback-proof'"
            ) == 'true'
            transaction = connection.transaction()
            await transaction.start()
            try:
                assert await connection.fetchval(
                    "INSERT INTO general.state(key,value) VALUES "
                    "('rollback-native-write','{}'::jsonb) RETURNING 1"
                ) == 1
            finally:
                await transaction.rollback()
    finally:
        closing = asyncio.create_task(database.close())
        cancellation = None
        deadline = asyncio.get_running_loop().time() + 5
        while not closing.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                if database.pool is not None:
                    database.pool.terminate()
                break
            try:
                await asyncio.wait_for(asyncio.shield(closing), remaining)
            except TimeoutError:
                if database.pool is not None:
                    database.pool.terminate()
                break
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        closing.result()
        if cancellation is not None:
            raise cancellation
    wrong = Database(database_name_from_env(config.db_name), schema=config.db_schema,
                     role='butler_switchboard_rw', min_pool_size=0, max_pool_size=1,
                     **db_params_from_env())
    try:
        await wrong.connect()
    except RuntimeError as exc:
        if str(exc) != 'Runtime database role does not match its schema':
            raise
        assert wrong.pool is None
    else:
        raise AssertionError('wrong profile was admitted')
    print(json.dumps({'native_read': True, 'native_rolled_back_write': True,
                      'wrong_profile_refused': True, 'pool_closed': database.pool is None}))

asyncio.run(probe())
"""


def _prove_actual_released_native_profile(result, expected):
    """Actual pinned config/Database, not a reconstructed old role classifier."""
    try:
        process = subprocess.run(
            [sys.executable, "-c", _PREDECESSOR_PROFILE],
            cwd=result["released_source"],
            env={**os.environ, "PYTHONPATH": str(result["released_source"] / "src")},
            input=json.dumps({"normal_url": result["normal_url"]}),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Pinned native restored profile did not complete") from None
    if process.returncode:
        raise RuntimeError("Pinned native restored profile did not complete")
    assert json.loads(process.stdout) == {
        "native_read": True,
        "native_rolled_back_write": True,
        "wrong_profile_refused": True,
        "pool_closed": True,
    }
    assert _rollback_observer(result["admin_url"]) == expected


def _prove_current_condition_marker_transition(result, engine, stable):
    """Real CLI/held maintenance disable/reinstall retains nonempty evidence."""
    from click.testing import CliRunner

    from butlers.cli import cli

    runner = CliRunner()
    for component in ("marker", "conditions", "reissue"):
        for state in ("disabled", "enabled"):
            invocation = runner.invoke(
                cli,
                ["db", "attention-transition", "--component", component, "--state", state],
                env={"BUTLERS_BOOTSTRAP_DATABASE_URL": result["admin_url"]},
            )
            assert invocation.exit_code == 0
            observed = _rollback_observer(result["admin_url"])
            assert {
                k: v for k, v in observed.items() if k not in {"controls", "marker_trigger"}
            } == {k: v for k, v in stable.items() if k not in {"controls", "marker_trigger"}}
            with engine.connect() as observer:
                if component == "marker":
                    assert observer.exec_driver_sql(
                        "SELECT to_regprocedure("
                        "'public.runtime_attention_sync_condition_marker()') "
                        "IS NOT NULL"
                    ).scalar_one() is (state == "enabled")
                else:
                    table, field = {
                        "conditions": ("runtime_attention_condition_control", "producer_enabled"),
                        "reissue": ("runtime_attention_operator_control", "reissue_enabled"),
                    }[component]
                    assert observer.exec_driver_sql(
                        'SELECT "' + field + '" FROM public."' + table + '" WHERE singleton'
                    ).scalar_one() is (state == "enabled")
        assert _rollback_observer(result["admin_url"]) == stable


async def test_real_normal_multischema_replay_preserves_protected_user_context(postgres_container):
    """Actual normal-role core replay sees the ACL-hidden physical destination.

    The held administrative session supplies maintenance transport only. Both
    chains select the real normal migrator; a separate authenticated normal
    connection proves catalog visibility differs from information_schema.
    Locally this genuine PostgreSQL body is authored/collected only.
    """
    from butlers.migrations import finalize_authority, migration_session, run_migrations
    from butlers.testing.migration import create_migration_db

    def run():
        name = migration_db_name()
        normal_url = create_migration_db(postgres_container, name)
        admin_url = migration_bootstrap_db_url(postgres_container, name)
        normal = create_engine(normal_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
        observer = create_engine(admin_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
        catalog = text(
            "SELECT c.oid,r.rolname,c.relacl::text,c.relrowsecurity,c.relforcerowsecurity,"
            "(SELECT COALESCE(jsonb_agg(to_jsonb(u) ORDER BY u.id),'[]'::jsonb) "
            " FROM public.user_context u),"
            "(SELECT COALESCE(jsonb_agg(jsonb_build_array(p.polname,p.polcmd,p.polpermissive,"
            "p.polroles::text,pg_get_expr(p.polqual,p.polrelid),"
            "pg_get_expr(p.polwithcheck,p.polrelid)) ORDER BY p.polname),'[]'::jsonb) "
            " FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid) "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_catalog.pg_roles r ON r.oid=c.relowner "
            "WHERE n.nspname='public' AND c.relname='user_context' AND c.relkind IN ('r','p')"
        )
        try:
            with migration_session(admin_url, source_database="butlers") as held:
                asyncio.run(
                    run_migrations(normal_url, chain="core", schema="relationship", connection=held)
                )
                finalize_authority(
                    held, source_database="butlers", plans=(("core", "relationship", None),)
                )
                # Real source writer, not a fixture table GRANT or direct row
                # insertion, retains a nonempty protected data witness.
                held.exec_driver_sql("SET ROLE butler_general_rw")
                held.execute(
                    text(
                        "SELECT * FROM public.context_dnd_mutate(CAST(:id AS uuid),"
                        "'general','set',now()+interval '1 hour','multischema-proof',"
                        "CAST(1 AS real),CAST('{}' AS jsonb))"
                    ),
                    {"id": str(uuid.uuid4())},
                ).all()
                held.commit()
                held.exec_driver_sql("RESET ROLE")
                held.commit()
                with observer.connect() as independent:
                    before = independent.execute(catalog).one()
                    assert before[1] == "dnd_generation_owner" and before[3:5] == (True, True)
                    assert before[5]
                with normal.connect() as authenticated:
                    login, actor = authenticated.exec_driver_sql(
                        "SELECT session_user,current_user"
                    ).one()
                    assert login == actor == unquote(urlparse(normal_url).username)
                    assert (
                        authenticated.exec_driver_sql(
                            "SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema='public' AND table_name='user_context'"
                        ).scalar_one()
                        == 0
                    )
                    assert (
                        authenticated.exec_driver_sql(
                            "SELECT count(*) FROM pg_catalog.pg_class c "
                            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname='public' AND c.relname='user_context' AND c.relkind IN ('r','p')"
                        ).scalar_one()
                        == 1
                    )
                # This genuinely reruns core042/core046 in another schema after
                # the first schema has transferred the public table's owner.
                asyncio.run(
                    run_migrations(normal_url, chain="core", schema="switchboard", connection=held)
                )
                finalize_authority(
                    held,
                    source_database="butlers",
                    plans=(
                        ("core", "relationship", None),
                        ("core", "switchboard", None),
                    ),
                )
            with observer.connect() as independent:
                assert independent.execute(catalog).one() == before
                assert (
                    independent.exec_driver_sql(
                        "SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
                        "ON n.oid=c.relnamespace WHERE n.nspname='shared' AND c.relname='user_context'"
                    ).scalar_one()
                    == 0
                )
                from butlers.testing.migration import assert_at_chain_head

                assert_at_chain_head(independent, "relationship")
                assert_at_chain_head(independent, "switchboard")
        finally:
            normal.dispose()
            observer.dispose()

    await asyncio.to_thread(run)

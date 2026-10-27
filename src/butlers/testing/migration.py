"""Shared helpers for migration integration tests.

These utilities are used by both ``tests/config/`` and ``roster/*/tests/``
to avoid duplicating the same boilerplate across migration test files.

They are intentionally free of pytest fixtures so they can be imported from
any test context, including roster-local test trees.

Migrated DB Pattern
-------------------
Tests that need schema-accurate fixtures (without hand-rolled CREATE TABLE)
should use :func:`create_migrated_test_db` to provision a real Alembic-migrated
database::

    # In a module-scoped pytest fixture:
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    @pytest.fixture(scope="module")
    def migrated_db(postgres_container):
        db_url = create_migrated_test_db(
            postgres_container,
            migration_db_name(),
            chains=["core", "memory", "relationship"],
            schemas={"relationship": "relationship"},
        )
        return db_url  # yield if you need teardown

Adding a migration column or table requires zero changes in tests — the next
:func:`create_migrated_test_db` call picks it up automatically.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import sys
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from urllib.parse import quote, unquote, urlparse

import asyncpg
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.pool import NullPool

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INIT_DB = _REPO_ROOT / "scripts" / "init-db.sql"
_MODULE_REGISTRATION_LOCK = threading.RLock()
_PSQL_ON_ERROR_STOP_DIRECTIVE = r"\set ON_ERROR_STOP on" + "\n"


def migration_db_name() -> str:
    """Return a unique database name suitable for one migration test run."""
    return f"test_{uuid.uuid4().hex[:12]}"


def migration_bootstrap_db_url(postgres_container: object, db_name: str) -> str:
    """Return the disposable database URL for managed bootstrap operations.

    This is intentionally limited to testcontainers' privileged control login.
    Ordinary migration tests should use :func:`create_migration_db`'s returned
    normal NOCREATEDB login instead.
    """
    parsed = urlparse(postgres_container.get_connection_url())  # type: ignore[attr-defined]
    return parsed._replace(path=f"/{quote(db_name, safe='')}").geturl()


def create_migration_db(
    postgres_container: object,
    db_name: str,
    *,
    _owned_creation: list[tuple[str, str, int, int | None]] | None = None,
) -> str:
    """Provision a fresh bootstrapped database on *postgres_container*.

    Parameters
    ----------
    postgres_container:
        A ``testcontainers.postgres.PostgresContainer`` instance (typed loosely to
        avoid a hard import-time dependency on testcontainers).
    db_name:
        Name of the database to create.  Must be a valid PostgreSQL identifier.
    """
    admin_url = postgres_container.get_connection_url()  # type: ignore[attr-defined]
    migration_user = f"migration_{db_name}"
    migration_password = uuid.uuid4().hex
    _create_test_migration_role(admin_url, migration_user, migration_password)

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            role_oid = None
            if _owned_creation is not None:
                role_oid = conn.execute(
                    text("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:role"),
                    {"role": migration_user},
                ).scalar_one()
                # Only the acknowledged CREATE above can populate this receipt.
                _owned_creation.append(("role", migration_user, role_oid, None))
            safe_db_name = db_name.replace('"', '""')
            safe_migration_user = migration_user.replace('"', '""')
            conn.execute(text(f'CREATE DATABASE "{safe_db_name}" OWNER "{safe_migration_user}"'))
            if _owned_creation is not None:
                database_oid, owner_oid = conn.execute(
                    text("SELECT oid,datdba FROM pg_catalog.pg_database WHERE datname=:database"),
                    {"database": db_name},
                ).one()
                if owner_oid != role_oid:
                    raise RuntimeError("Acknowledged disposable database identity changed")
                _owned_creation.append(("database", db_name, database_oid, owner_oid))
    finally:
        engine.dispose()

    bootstrap_db_url = migration_bootstrap_db_url(postgres_container, db_name)
    host = postgres_container.get_container_host_ip()  # type: ignore[attr-defined]
    port = postgres_container.get_exposed_port(5432)  # type: ignore[attr-defined]
    db_url = (
        f"postgresql://{quote(migration_user, safe='')}:{quote(migration_password, safe='')}"
        f"@{host}:{port}/{db_name}"
    )

    # Activate required extensions before any migration chain runs.
    bootstrap_extensions(bootstrap_db_url)
    _bootstrap_migration_prerequisites(bootstrap_db_url, migration_user)

    return db_url


def _create_test_migration_role(admin_url: str, role: str, password: str) -> None:
    """Create a unique normal login for one disposable migration database."""
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    f'CREATE ROLE "{role}" LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE '
                    "NOCREATEDB NOREPLICATION PASSWORD :password"
                ),
                {"password": password},
            )
    finally:
        engine.dispose()


def init_db_sql_for_dbapi() -> str:
    """Return init-db SQL for disposable fixtures that execute through DBAPI.

    The checked-in script owns fail-fast behavior for its documented ``psql -f``
    invocation. DBAPI cursors cannot parse psql client commands, so fixtures
    remove only that exact client-only directive and fail if it drifts.
    """
    source = _INIT_DB.read_text(encoding="utf-8")
    if source.count(_PSQL_ON_ERROR_STOP_DIRECTIVE) != 1:
        raise RuntimeError("init-db.sql must contain exactly one psql fail-fast directive")
    return source.replace(_PSQL_ON_ERROR_STOP_DIRECTIVE, "", 1)


def _bootstrap_migration_prerequisites(bootstrap_db_url: str, migration_user: str) -> None:
    """Stage the production bootstrap contract in a disposable migration database.

    The same production preparation compiles the selected deployment's source
    identities. No fixture-specific grant or stored installer is substituted.
    """
    from butlers.bootstrap_prerequisite import prepare_bootstrap_connection

    database = unquote(urlparse(bootstrap_db_url).path.lstrip("/"))
    engine = create_engine(
        bootstrap_db_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5}
    )
    raw_connection = None
    try:
        raw_connection = engine.raw_connection()
        raw_connection.autocommit = True
        prepare_bootstrap_connection(raw_connection, database, migration_user)
    finally:
        try:
            if raw_connection is not None:
                raw_connection.close()
        finally:
            engine.dispose()


def bootstrap_extensions(db_url: str) -> None:
    """Install required PostgreSQL extensions on the target database.

    Must be called **after** the database is created and **before** any
    Alembic migrations run.  The pgvector/pgvector Docker image ships the
    extension shared-object files but they still need ``CREATE EXTENSION``
    to be activated.
    """
    parsed = urlparse(db_url)

    async def _install() -> None:
        conn = await asyncpg.connect(
            host=parsed.hostname,
            port=parsed.port,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip("/"),
        )
        try:
            await conn.execute('CREATE EXTENSION IF NOT EXISTS "vector"')
            await conn.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
            await conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
            await conn.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
        finally:
            await conn.close()

    asyncio.run(_install())


# ---------------------------------------------------------------------------
# Structural inspection helpers
# ---------------------------------------------------------------------------


def table_exists(db_url: str, table_name: str, schema: str = "public") -> bool:
    """Return True when *table_name* exists in *schema* (default ``public``).

    Pass ``schema`` to inspect a schema-scoped chain (e.g. ``schema="switchboard"``
    for tables the switchboard chain provisioned into its own schema, bu-9auxy).
    """
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_schema = :s AND table_name = :t"
                ")"
            ),
            {"s": schema, "t": table_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def constraint_exists(db_url: str, table_name: str, constraint_name: str) -> bool:
    """Return True when *constraint_name* exists on *table_name*."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.table_constraints"
                "  WHERE table_name = :t AND constraint_name = :c"
                ")"
            ),
            {"t": table_name, "c": constraint_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def index_exists(db_url: str, index_name: str, schema: str = "public") -> bool:
    """Return True when *index_name* exists in *schema* (default ``public``).

    Pass ``schema`` for indexes a schema-scoped chain provisioned into its own
    schema (e.g. ``schema="switchboard"``, bu-9auxy).
    """
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM pg_indexes"
                "  WHERE schemaname = :s AND indexname = :i"
                ")"
            ),
            {"s": schema, "i": index_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def get_column_info(
    db_url: str, table_name: str, column_name: str, schema: str = "public"
) -> dict | None:
    """Return column metadata from ``information_schema``, or None if absent.

    The returned dict has keys: ``data_type``, ``column_default``, ``is_nullable``.
    Pass ``schema`` for a table a schema-scoped chain provisioned into its own
    schema (e.g. ``schema="switchboard"``, bu-9auxy).
    """
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT data_type, column_default, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
            ),
            {"s": schema, "t": table_name, "c": column_name},
        )
        row = result.fetchone()
    engine.dispose()
    if row:
        return {
            "data_type": row[0],
            "column_default": row[1],
            "is_nullable": row[2],
        }
    return None


def _quote_ident(identifier: str) -> str:
    """Quote a schema/table identifier for interpolation into raw SQL."""
    return '"' + identifier.replace('"', '""') + '"'


def assert_at_chain_head(
    connection: Connection, schema: str = "public", *, chain: str = "core"
) -> None:
    """Assert *schema*'s ``alembic_version`` is stamped at *chain*'s codebase head.

    Tests that upgrade to ``<chain>@head`` must not spell the expected revision
    as a literal.  A literal encodes whatever the head happened to be the day
    the test was written, so the next migration breaks the assertion even when
    that migration is entirely correct — and a bare ``core_NNN`` is ambiguous by
    inspection, since it sometimes names *that* revision and sometimes names
    *the head*.  :func:`butlers.migrations.get_chain_head` resolves the head
    from the Alembic script directory (a filesystem scan, no database), which
    keeps these assertions self-maintaining across every renumber.

    Use this only where the test really means "the head".  An assertion that
    pins a specific revision on purpose — a partial upgrade, or a refused
    downgrade that must leave the schema behind — should keep its literal and
    say so with a ``# pinned-revision: <why>`` comment on or above the
    assertion, which is what ``tests/config/test_migration_chain_head.py``
    looks for.  The reason is mandatory: a bare marker does not excuse the
    literal, because articulating *which* of the two readings it has is the
    whole point of the annotation.
    """
    # Local import avoids a circular import at module load time.
    from butlers.migrations import get_chain_head

    expected = get_chain_head(chain)
    stamped = [
        row[0]
        for row in connection.execute(
            text(f"SELECT version_num FROM {_quote_ident(schema)}.alembic_version")
        )
    ]
    assert stamped == [expected], (
        f"{schema}.alembic_version holds {stamped!r}; expected exactly the "
        f"{chain} chain head [{expected!r}]"
    )


# ---------------------------------------------------------------------------
# Alembic-based test DB provisioning (preferred over hand-rolled CREATE TABLE)
# ---------------------------------------------------------------------------


def create_migrated_test_db(
    postgres_container: object,
    db_name: str,
    chains: list[str],
    schemas: dict[str, str] | None = None,
    revisions: dict[str, str] | None = None,
) -> str:
    """Create a fresh DB and run real Alembic migrations against it.

    This is the preferred pattern for feature/integration tests that need a
    schema-accurate database.  It replaces hand-rolled ``CREATE TABLE`` fixtures
    that drift whenever a migration adds or renames a column.

    Parameters
    ----------
    postgres_container:
        A ``testcontainers.postgres.PostgresContainer`` instance.
    db_name:
        Unique database name.  Use :func:`migration_db_name` to generate one.
    chains:
        Migration chains to run in order (e.g. ``["core", "memory", "relationship"]``).
        Each chain name must be recognized by :func:`butlers.migrations.run_migrations`.
    schemas:
        Optional mapping of chain name → target schema.  When a chain is not
        listed here, migrations run without a ``SET search_path`` override, so
        unqualified object names land in ``public`` (default PostgreSQL behaviour).

        Example::

            schemas={"relationship": "relationship"}
    revisions:
        Optional mapping of chain name → target revision.  Chains not listed
        migrate to head, which is what feature/integration fixtures want.

        A test that rolls one migration back must bound the chain to the
        revision it owns, because rollback is not uniformly available across a
        chain: later revisions install privileged boundaries whose rollback is
        deliberately restricted (``core_198``'s bootstrap-only runtime-attention
        interface, ``core_196``'s restore-drill executor boundary).  Upgrading
        to head and then downgrading past such a boundary asks for a rollback
        the boundary is designed to refuse, which surfaces as an unrelated
        migration failing in a test about some much older revision.

        Example::

            revisions={"core": "core_170"}

    Returns
    -------
    str
        A SQLAlchemy-compatible ``postgresql://`` URL for the migrated database.

    Usage
    -----
    ::

        @pytest.fixture(scope="module")
        def migrated_db_url(postgres_container) -> str:
            return create_migrated_test_db(
                postgres_container,
                migration_db_name(),
                chains=["core", "memory", "relationship"],
                schemas={"relationship": "relationship"},
            )
    """
    schemas = {} if schemas is None else schemas
    revisions = {} if revisions is None else revisions
    db_url = create_migration_db(postgres_container, db_name)
    plans = tuple((chain, schemas.get(chain), revisions.get(chain)) for chain in chains)
    return migrate_test_database(postgres_container, db_url, plans=plans)


def migrate_test_database(
    postgres_container: object,
    db_url: str,
    *,
    plans: tuple[tuple[str, str | None, str | None], ...],
    final_plans: tuple[tuple[str, str | None, str | None], ...] | None = None,
) -> str:
    """Run an explicit disposable test deployment under one held maintenance session.

    ``db_url`` remains the normal migration/query identity. Administrative
    transport comes only from the caller's actual selected testcontainer, never
    from the normal URL or host environment. Ordered plans retain duplicates,
    selected schemas and bounded historical targets; finalization happens only
    after every requested transition succeeds, on that same physical session.
    """
    from butlers.migrations import finalize_authority, migration_session, run_migrations

    selected_database = unquote(urlparse(db_url).path.lstrip("/"))
    maintenance_url = migration_bootstrap_db_url(postgres_container, selected_database)
    with migration_session(maintenance_url, source_database="butlers") as connection:
        for chain, schema, revision in plans:
            if revision is None:
                asyncio.run(
                    run_migrations(db_url, chain=chain, schema=schema, connection=connection)
                )
            else:
                _upgrade_chain_to_revision(
                    db_url, chain=chain, schema=schema, revision=revision, connection=connection
                )
        finalize_authority(
            connection,
            source_database="butlers",
            plans=plans if final_plans is None else final_plans,
        )
    return db_url


def stage_test_migration_history(postgres_container, db_url, *, plans):
    """Stage an explicit pre-cutover migration window without current finalization.

    This disposable historical fixture never crosses core_281 or claims serving
    admission. Every core target must be an actual source revision before the
    cutover. Later current deployment proof uses the separate complete finalizer.
    """
    from butlers.migrations import get_chain_revision_path, migration_session, run_migrations

    for chain, _schema, revision in plans:
        if chain == "core" and (
            revision is None or "core_281" in get_chain_revision_path("core", revision)
        ):
            raise ValueError("Historical core setup requires an explicit pre-core_281 target")
    selected_database = unquote(urlparse(db_url).path.lstrip("/"))
    maintenance_url = migration_bootstrap_db_url(postgres_container, selected_database)
    with migration_session(maintenance_url, source_database="butlers") as connection:
        for chain, schema, revision in plans:
            if revision is None:
                asyncio.run(
                    run_migrations(db_url, chain=chain, schema=schema, connection=connection)
                )
            else:
                _upgrade_chain_to_revision(
                    db_url, chain=chain, schema=schema, revision=revision, connection=connection
                )
    return db_url


def bind_historical_test_environment(config, directory, source, *, expected_sha256):
    """Bind one pinned historical online producer to explicitly held test IO.

    This never replaces the current environment. The literal historical online
    body is preserved; only its engine and version-location inputs borrow the
    caller-owned physical maintenance connection. It supports ownership/metadata
    diagnostics, not current serving admission or a reverse across core_281.
    """
    if hashlib.sha256(source.encode()).hexdigest() != expected_sha256:
        raise AssertionError("Pinned historical environment changed")
    entry = "if context.is_offline_mode():\n"
    if source.count(entry) != 1:
        raise AssertionError("Pinned historical entrypoint changed")
    adapter = """
# Test-only physical transport; the historical online function above is exact.
from contextlib import nullcontext
from types import SimpleNamespace

def _historical_held_engine(url, **kwargs):
    if url != context.config.attributes["historical.selected_url"]:
        raise AssertionError("Historical configured subject changed")
    held = context.config.attributes["historical.held_connection"]
    return SimpleNamespace(connect=lambda: nullcontext(held))

def _historical_selected_locations():
    return context.config.get_main_option("version_locations").split(os.pathsep)

create_engine = _historical_held_engine
get_version_locations = _historical_selected_locations

"""
    final = source.replace(entry, adapter + entry, 1)

    def online(text_source):
        return next(
            node
            for node in ast.parse(text_source).body
            if isinstance(node, ast.FunctionDef) and node.name == "run_migrations_online"
        )

    if ast.dump(online(source)) != ast.dump(online(final)):
        raise AssertionError("Historical online producer changed")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "env.py").write_text(final, encoding="utf-8")
    config.set_main_option("script_location", str(directory))
    return config


def run_historical_test_alembic_command(
    postgres_container, operation, config, revision, *, normal_url, administrative=False
):
    """Execute a labeled historical subject on the same actual maintenance session.

    Role selection is committed before the old public/no-schema online entry;
    success commits and failure rolls back before restoring role/outer lock.
    No normal URL is promoted into administrative credentials, and the borrowed
    physical connection is never closed by the historical environment.
    """
    from contextlib import nullcontext

    from butlers.migrations import migration_session, ordinary_migration_identity

    database = unquote(urlparse(normal_url).path.lstrip("/"))
    control = migration_bootstrap_db_url(postgres_container, database)
    selected = config.get_main_option("sqlalchemy.url").replace("%%", "%")
    if selected != (control if administrative else normal_url):
        raise AssertionError("Historical identity is not the explicit selected subject")
    names = ("historical.selected_url", "historical.held_connection")
    if any(name in config.attributes for name in names):
        raise AssertionError("Historical config already owns transport")
    with migration_session(control, source_database="butlers") as connection:
        config.attributes.update({names[0]: selected, names[1]: connection})
        try:
            subject = (
                nullcontext(connection)
                if administrative
                else ordinary_migration_identity(connection)
            )
            with subject:
                if administrative:
                    connection.exec_driver_sql("RESET ROLE")
                connection.commit()
                try:
                    result = operation(config, revision)
                    connection.commit()
                    return result
                except BaseException as primary:
                    try:
                        connection.rollback()
                    except BaseException:
                        primary.add_note("Historical operation rollback failed")
                    raise
        finally:
            for name in names:
                config.attributes.pop(name, None)


def run_test_alembic_command(postgres_container, operation, config, revision):
    """Execute the caller's exact Alembic operation on held test maintenance.

    Bounded transitions deliberately do not synthesize a future deployment or
    stamp. The original operation/target and its normal DDL role remain real;
    the selected testcontainer supplies only explicit administrative transport.
    """
    from butlers.migrations import migration_session, ordinary_migration_identity

    db_url = config.get_main_option("sqlalchemy.url").replace("%%", "%")
    selected_database = unquote(urlparse(db_url).path.lstrip("/"))
    maintenance_url = migration_bootstrap_db_url(postgres_container, selected_database)
    missing = object()
    previous = config.attributes.get("connection", missing)
    if previous is not missing:
        raise RuntimeError("Historical test config already owns a connection")
    with migration_session(maintenance_url, source_database="butlers") as connection:
        config.attributes["connection"] = connection
        try:
            if getattr(operation, "__name__", None) == "upgrade":
                from alembic.script import ScriptDirectory

                from butlers.db_authority.interfaces import stage_dashboard_interfaces

                # Resolve the actual selected source graph, including test-owned
                # producer variants. Historical targets below D13 gain no future
                # interfaces merely because current code has them.
                script = ScriptDirectory.from_config(config)
                targets = script.get_revisions(revision)
                ancestors = {
                    item.revision
                    for target in targets
                    for item in script.iterate_revisions(target.revision, "base")
                }
                if {"core_277", "core_278"} & ancestors:
                    stage_dashboard_interfaces(connection)
            with ordinary_migration_identity(connection):
                try:
                    result = operation(config, revision)
                    # Current env may borrow the transaction opened by SET ROLE
                    # for a public-schema command. This wrapper owns completion
                    # before maintenance cleanup rolls back role restoration.
                    connection.commit()
                    return result
                except BaseException as primary:
                    try:
                        connection.rollback()
                    except BaseException:
                        primary.add_note("Selected disposable command rollback was incomplete")
                    raise
        finally:
            config.attributes.pop("connection", None)


async def _settle_test_migration(operation, *args, **kwargs):
    """Retain an off-loop physical test migration until its owned session exits."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
        except BaseException:
            break
    if cancelled is not None:
        try:
            task.result()
        except BaseException:
            cancelled.add_note("Selected disposable migration also failed during cancellation")
        raise cancelled
    return task.result()


async def async_migrate_test_database(postgres_container, db_url, *, plans, final_plans=None):
    """Run the explicit complete selected test plan without blocking the active loop."""
    return await _settle_test_migration(
        migrate_test_database, postgres_container, db_url, plans=plans, final_plans=final_plans
    )


async def async_run_test_alembic_command(postgres_container, operation, config, revision):
    """Retain the caller's exact historical command and held-session cleanup."""
    return await _settle_test_migration(
        run_test_alembic_command, postgres_container, operation, config, revision
    )


def _upgrade_chain_to_revision(
    db_url: str, *, chain: str, schema: str | None, revision: str, connection: Connection
) -> None:
    """Upgrade one chain to *revision* instead of its head.

    ``run_migrations`` deliberately only knows "migrate to head" — a bounded
    target is a test-harness concern, so it is resolved here rather than by
    widening the production entry point.
    """
    # Local import avoids a circular import at module load time.
    from alembic import command
    from butlers.migrations import (
        _build_alembic_config,
        _normalize_schema,
        ordinary_migration_identity,
    )

    normalized_schema = _normalize_schema(schema)
    if chain == "core":
        from butlers.db_authority.interfaces import stage_dashboard_interfaces
        from butlers.migrations import get_chain_revision_path

        # Use the same actual maintenance producer as the production chain.
        # Only source revisions depending on these independent D13 interfaces
        # stage them; a bounded old target gains no future runtime capability.
        ancestors = get_chain_revision_path(chain, revision)
        if {"core_277", "core_278"} & ancestors:
            stage_dashboard_interfaces(connection)
    config = _build_alembic_config(db_url, [chain], target_schema=normalized_schema)
    config.attributes["connection"] = connection
    with ordinary_migration_identity(connection):
        command.upgrade(config, f"{chain}@{revision}")


async def create_migrated_test_pool(
    postgres_container: object,
    *,
    chains: list[str],
    schemas: dict[str, str] | None = None,
    pool_schema: str | None = None,
    min_pool_size: int = 1,
    max_pool_size: int = 3,
) -> asyncpg.Pool:
    """Create an administrative schema-accurate test pool off the active loop.

    The synchronous migration factory deliberately owns disposable database
    creation, privileged ``init-db.sql`` staging, and ordinary NOCREATEDB
    migrations. This utility then opens the selected disposable database with
    its testcontainer control login for administrative seeds/readback and
    explicit SET ROLE probes. It retains JSONB/search-path behavior, not a
    production serving identity. Native admission/RLS proofs must separately
    connect as a real dedicated serving login; an administrative acquisition
    cannot discharge them. create_migrated_test_db still returns only the
    normal migration-owner URL.
    """
    from butlers.db import register_jsonb_codec, schema_search_path

    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        chains,
        schemas,
    )
    pool_kwargs: dict[str, object] = {
        "min_size": min_pool_size,
        "max_size": max_pool_size,
        "init": register_jsonb_codec,
    }
    if pool_schema is not None:
        search_path = schema_search_path(pool_schema)
        assert search_path is not None
        pool_kwargs["server_settings"] = {"search_path": search_path}

    selected_database = unquote(urlparse(db_url).path.lstrip("/"))
    control_url = migration_bootstrap_db_url(postgres_container, selected_database)
    # testcontainers exposes a SQLAlchemy driver-qualified URL; asyncpg needs
    # the native scheme. Preserve every other selected connection component.
    control_url = urlparse(control_url)._replace(scheme="postgresql").geturl()
    return await asyncpg.create_pool(control_url, timeout=5, **pool_kwargs)


def load_migration(
    path: str | Path, name: str | None = None, *, register: bool = False
) -> ModuleType:
    """Import an explicit migration file freshly, without running upgrade/downgrade.

    ``name`` preserves the caller's module identity; the default is
    ``_migration_<file stem>``. Missing files and import errors propagate.
    ``register=True`` supports files whose import needs ``sys.modules`` (for
    example dataclasses); the prior entry is restored even if execution fails.
    Default loads never insert into ``sys.modules``. This does not provision a DB.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    name = name if name is not None else f"_migration_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"Cannot load migration: {path}"
    module = importlib.util.module_from_spec(spec)
    if not register:
        spec.loader.exec_module(module)
        return module
    # Registration is opt-in process-global state; serialize our temporary slots.
    with _MODULE_REGISTRATION_LOCK:
        missing = object()
        previous = sys.modules.get(name, missing)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
            return module
        finally:
            if previous is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


@dataclass(frozen=True)
class ServingTestLogin:
    """Synthetic operator-created identity, never a runtime capability alias."""

    user: str
    password: str = field(repr=False)
    role: str


@dataclass(frozen=True)
class ServingTestDeployment:
    """Actual source deployment on one disposable testcontainer server."""

    host: str
    port: int
    migration_url: str = field(repr=False)
    runtime_logins: dict[str, ServingTestLogin]
    purpose_logins: dict[str, ServingTestLogin]
    _resources: list[tuple[object, str]] = field(default_factory=list, repr=False, compare=False)

    def retain(self, owner: object, method: str = "close") -> None:
        """Own a real pool manager/Database/daemon before its first startup await."""
        if method not in {"close", "shutdown"}:
            raise ValueError("Unsupported disposable serving cleanup")
        self._resources.append((owner, method))

    async def close_resources(self) -> None:
        from butlers.db_authority.catalog import close_after_catalog_diagnostics

        failures = []
        for owner, method in reversed(self._resources):
            try:
                await getattr(owner, method)()
            except TimeoutError:
                # These are the actual source-owned retained shutdown tails,
                # not permission to close a dependent pool early.
                joined = False
                # Resolve each source-owned tail only when its predecessor has
                # settled. The Chronicler tail can create the credential tail
                # after shutdown's first bounded refusal; snapshotting both
                # beforehand misses that physical dependent cleanup.
                for name in ("_chronicler_cleanup_task", "_credential_dependents_close_task"):
                    tail = vars(owner).get(name)
                    if tail is None:
                        continue
                    joined = True
                    try:
                        await asyncio.shield(tail)
                    except BaseException as exc:
                        failures.append(exc)
                if not joined:
                    pools = list(vars(owner).get("_pools", {}).values())
                    pool = vars(owner).get("_pool")
                    if pool is not None:
                        pools.append(pool)
                    try:
                        if not pools:
                            raise RuntimeError("Disposable cleanup has no retained owner")
                        for pool in pools:
                            await close_after_catalog_diagnostics(pool, wait_timeout=None)
                        await getattr(owner, method)()
                    except BaseException as exc:
                        failures.append(exc)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise RuntimeError("Disposable serving resources did not close") from failures[0]
        self._resources.clear()

    def purpose_url(self, purpose: str) -> str:
        selected = self.purpose_logins[purpose]
        return (
            f"postgresql://{quote(selected.user, safe='')}:{quote(selected.password, safe='')}"
            f"@{self.host}:{self.port}/butlers?sslmode=disable"
        )

    def runtime_database(self, schema: str, *, min_size: int = 1, max_size: int = 3):
        from butlers.db import Database

        selected = self.runtime_logins[schema]
        database = Database(
            db_name="butlers",
            schema=schema,
            role=selected.role,
            host=self.host,
            port=self.port,
            user=selected.user,
            password=selected.password,
            ssl="disable",
            min_pool_size=min_size,
            max_pool_size=max_size,
        )
        self.retain(database)
        return database


def _close_created_test_database(postgres_container, created):
    """Drop only acknowledged disposable identities, with catalog identity fencing."""
    from butlers.db_authority.apply import quote_identifier

    if not created:
        return
    engine = create_engine(
        postgres_container.get_connection_url(),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )
    errors = []
    try:
        for kind, name, oid, owner_oid in reversed(created):
            try:
                with engine.connect() as connection:
                    if kind == "database":
                        observed = connection.execute(
                            text(
                                "SELECT oid,datdba FROM pg_catalog.pg_database WHERE datname=:name"
                            ),
                            {"name": name},
                        ).one_or_none()
                        expected = (oid, owner_oid)
                        statement = f"DROP DATABASE {quote_identifier(name)}"
                    else:
                        observed = connection.execute(
                            text("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:name"),
                            {"name": name},
                        ).one_or_none()
                        expected = (oid,)
                        statement = f"DROP ROLE {quote_identifier(name)}"
                    if observed is None:
                        continue
                    if tuple(observed) != expected:
                        raise RuntimeError("Disposable cleanup identity changed; retained unknown")
                    # No FORCE/termination: outstanding physical leases refuse cleanup.
                    connection.exec_driver_sql(statement)
            except BaseException as exc:
                errors.append(exc)
    finally:
        try:
            engine.dispose()
        except BaseException as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError("Disposable database cleanup was incomplete") from errors[0]


@contextmanager
def serving_test_deployment(
    postgres_container: object,
    *,
    include_connector: bool = False,
    finance_revision: str | None = None,
):
    """Explicit disposable operator preparation for genuine serving tests.

    The caller owns an isolated server and closes every serving pool before
    leaving this scope. All source profiles, real chains, ordinary ownership,
    held maintenance and the finalizer remain unpatched. The selected container
    control login creates only synthetic dedicated logins and their sole
    existing capability memberships; no object privilege is manufactured.
    ``create_migration_db`` still returns only the normal migration URL.
    ``include_connector`` opts into one additional synthetic connector-purpose
    login through this same constructor; defaults and object authority do not change.

    ``finance_revision="finance_018"`` is the single supported fresh predecessor
    window for Finance's runtime migration test. Finance019 never executes in
    that preparation; all other source chains remain current. The historical
    finalizer is administrative and does not certify serving readiness. Dedicated
    logins are minted only after that preparation succeeds, but an actual
    ``runtime_database("finance").connect()`` must still refuse until the caller
    upgrades Finance to head and completes the real full current-source finalizer.
    No profile, role membership, admission check or object privilege is bypassed.
    """
    from butlers.db_authority.apply import quote_identifier
    from butlers.db_authority.grants import deployment_function_plans
    from butlers.db_authority.roles import purpose_profile, runtime_profiles
    from butlers.migrations import (
        finalize_authority,
        get_all_chains,
        migration_session,
        run_migrations,
    )

    if type(include_connector) is not bool:
        raise ValueError("Disposable connector selection must be boolean")
    if finance_revision is not None and (
        type(finance_revision) is not str or finance_revision != "finance_018"
    ):
        raise ValueError("Unsupported disposable Finance predecessor")

    owned: list[tuple[str, str]] = []
    created: list[tuple[str, str, int, int | None]] = []
    primary = None
    control_url = migration_bootstrap_db_url(postgres_container, "butlers")
    try:
        migration_url = create_migration_db(postgres_container, "butlers", _owned_creation=created)
        profiles = runtime_profiles("butlers")
        available = frozenset(get_all_chains())
        plans = tuple(row for row in deployment_function_plans(profiles) if row[0] in available)
        if finance_revision is not None:
            if ("finance", "finance", None) not in plans:
                raise ValueError("Disposable Finance source plan is unavailable")
            plans = tuple(
                (chain, schema, finance_revision)
                if (chain, schema) == ("finance", "finance")
                else (chain, schema, revision)
                for chain, schema, revision in plans
            )
        core = tuple(row for row in plans if row[0] == "core")
        others = tuple(row for row in plans if row[0] != "core")
        with migration_session(control_url, source_database="butlers") as connection:
            for _, schema, _ in core:
                _upgrade_chain_to_revision(
                    migration_url,
                    chain="core",
                    schema=schema,
                    revision="core_114",
                    connection=connection,
                )
            for chain, schema, revision in (*core, *others):
                if revision is None:
                    asyncio.run(
                        run_migrations(
                            migration_url,
                            chain=chain,
                            schema=schema,
                            connection=connection,
                        )
                    )
                else:
                    _upgrade_chain_to_revision(
                        migration_url,
                        chain=chain,
                        schema=schema,
                        revision=revision,
                        connection=connection,
                    )
            finalize_authority(connection, source_database="butlers", plans=plans)

        engine = create_engine(
            control_url,
            isolation_level="AUTOCOMMIT",
            poolclass=NullPool,
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
        )
        setup_error = None
        try:

            def create_login(role: str) -> ServingTestLogin:
                login = f"serving_test_{uuid.uuid4().hex}"
                password = uuid.uuid4().hex
                with engine.connect() as connection:
                    safe = connection.execute(
                        text("""
                        SELECT NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper
                          AND NOT rolcreatedb AND NOT rolcreaterole
                          AND NOT rolreplication AND NOT rolbypassrls
                        FROM pg_catalog.pg_roles WHERE rolname=:role
                    """),
                        {"role": role},
                    ).scalar_one()
                    if safe is not True:
                        raise RuntimeError("Source test capability is unavailable")
                    connection.execute(
                        text(
                            f"CREATE ROLE {quote_identifier(login)} LOGIN NOINHERIT NOSUPERUSER "
                            "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :password"
                        ),
                        {"password": password},
                    )
                    # Only an acknowledged CREATE owns cleanup. Ambiguous
                    # creation never authorizes dropping another identity.
                    owned.append((login, role))
                    connection.exec_driver_sql(
                        f"GRANT {quote_identifier(role)} TO {quote_identifier(login)}"
                    )
                return ServingTestLogin(login, password, role)

            runtime = {p.primary_schema: create_login(p.role) for p in profiles}
            purposes = {
                purpose: create_login(purpose_profile("butlers", purpose).role)
                for purpose in (
                    "credential",
                    "dashboard",
                    *(("connector",) if include_connector else ()),
                )
            }
        except BaseException as exc:
            setup_error = exc
            raise
        finally:
            try:
                engine.dispose()
            except BaseException:
                if setup_error is None:
                    raise
                setup_error.add_note("Disposable setup connection disposal failed")
        yield ServingTestDeployment(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            migration_url=migration_url,
            runtime_logins=runtime,
            purpose_logins=purposes,
        )
    except BaseException as exc:
        primary = exc
        raise
    finally:
        failures = []
        if owned:
            cleanup = None
            try:
                cleanup = create_engine(
                    control_url,
                    isolation_level="AUTOCOMMIT",
                    poolclass=NullPool,
                    connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
                )
                for login, role in reversed(owned):
                    statements = (
                        f"REVOKE {quote_identifier(role)} FROM {quote_identifier(login)}",
                        f"DROP ROLE {quote_identifier(login)}",
                    )
                    for statement in statements:
                        try:
                            with cleanup.connect() as connection:
                                connection.exec_driver_sql(statement)
                        except BaseException as exc:
                            failures.append(exc)
            except BaseException as exc:
                failures.append(exc)
            finally:
                if cleanup is not None:
                    try:
                        cleanup.dispose()
                    except BaseException as exc:
                        failures.append(exc)
        try:
            _close_created_test_database(postgres_container, created)
        except BaseException as exc:
            failures.append(exc)
        if failures:
            if primary is None:
                raise RuntimeError(
                    "Disposable serving identity cleanup was incomplete"
                ) from failures[0]
            primary.add_note("Disposable serving identity cleanup was incomplete")


@asynccontextmanager
async def async_serving_test_deployment(
    postgres_container: object,
    *,
    include_connector: bool = False,
    finance_revision: str | None = None,
):
    """Own off-loop preparation and cleanup through repeated caller cancellation.

    The synchronous producer uses real Alembic/DBAPI and bootstrap's asyncio.run.
    A cancelled caller joins its exact worker before disposing acknowledged
    identities; it cannot abandon a still-creating thread or close its server.
    """
    if type(include_connector) is not bool:
        raise ValueError("Disposable connector selection must be boolean")
    if finance_revision is not None and (
        type(finance_revision) is not str or finance_revision != "finance_018"
    ):
        raise ValueError("Unsupported disposable Finance predecessor")
    options = {}
    if include_connector:
        options["include_connector"] = True
    if finance_revision is not None:
        options["finance_revision"] = finance_revision
    context = serving_test_deployment(postgres_container, **options)

    async def settle(task):
        cancelled = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if cancelled is None:
                    cancelled = exc
            except BaseException:
                break
        return cancelled

    entry = asyncio.create_task(asyncio.to_thread(context.__enter__))
    cancelled = await settle(entry)
    try:
        deployment = entry.result()
    except BaseException as exc:
        if cancelled is not None:
            cancelled.add_note("Disposable deployment preparation failed during cancellation")
            raise cancelled from exc
        raise
    primary = cancelled
    try:
        if primary is not None:
            raise primary
        yield deployment
    except BaseException as exc:
        primary = exc
        raise
    finally:
        resources = asyncio.create_task(deployment.close_resources())
        resources_cancelled = await settle(resources)
        resource_error = None
        try:
            resources.result()
        except BaseException as exc:
            resource_error = exc
        selected_primary = primary or resources_cancelled or resource_error
        exit_args = (
            (None, None, None)
            if selected_primary is None
            else (type(selected_primary), selected_primary, selected_primary.__traceback__)
        )
        cleanup = asyncio.create_task(asyncio.to_thread(context.__exit__, *exit_args))
        cleanup_cancelled = await settle(cleanup)
        cleanup_error = None
        try:
            cleanup.result()
        except BaseException as exc:
            cleanup_error = exc
        # Keep the body's exception; otherwise caller cancellation outranks
        # cleanup errors. With neither, retain the actual exception identity.
        cancelled = resources_cancelled or cleanup_cancelled
        if primary is not None:
            if resource_error is not None or cleanup_error is not None:
                primary.add_note("Disposable deployment cleanup failed")
        elif cancelled is not None:
            if resource_error is not None or cleanup_error is not None:
                cancelled.add_note("Disposable deployment cleanup failed")
            raise cancelled
        elif resource_error is not None:
            if cleanup_error is not None:
                resource_error.add_note("Disposable identity cleanup also failed")
            raise resource_error
        elif cleanup_error is not None:
            raise cleanup_error

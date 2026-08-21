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

import asyncio
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

import asyncpg
from sqlalchemy import create_engine, text

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INIT_DB = _REPO_ROOT / "scripts" / "init-db.sql"
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


def create_migration_db(postgres_container: object, db_name: str) -> str:
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
            safe_db_name = db_name.replace('"', '""')
            safe_migration_user = migration_user.replace('"', '""')
            conn.execute(text(f'CREATE DATABASE "{safe_db_name}" OWNER "{safe_migration_user}"'))
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

    ``core_196`` deliberately invokes only the installer provisioned by
    ``scripts/init-db.sql``.  Migration tests use a fresh testcontainer database,
    so they must stage that trusted bootstrap boundary before exercising the
    ordinary, non-privileged migration path.
    """
    source = init_db_sql_for_dbapi()
    engine = create_engine(bootstrap_db_url, isolation_level="AUTOCOMMIT")
    raw_connection = engine.raw_connection()
    try:
        raw_connection.autocommit = True
        with raw_connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('butlers.connecting_user', %s, false)",
                (migration_user,),
            )
            # Do not pass an empty parameter mapping: PostgreSQL's SQL-format
            # placeholders in init-db.sql are not DBAPI bind parameters.
            cursor.execute(source)
    finally:
        raw_connection.close()
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
    # Local import avoids a circular import at module load time.
    from butlers.migrations import run_migrations

    if schemas is None:
        schemas = {}
    if revisions is None:
        revisions = {}

    db_url = create_migration_db(postgres_container, db_name)

    for chain in chains:
        schema = schemas.get(chain)
        revision = revisions.get(chain)
        if revision is None:
            asyncio.run(run_migrations(db_url, chain=chain, schema=schema))
        else:
            _upgrade_chain_to_revision(db_url, chain=chain, schema=schema, revision=revision)

    return db_url


def _upgrade_chain_to_revision(
    db_url: str, *, chain: str, schema: str | None, revision: str
) -> None:
    """Upgrade one chain to *revision* instead of its head.

    ``run_migrations`` deliberately only knows "migrate to head" — a bounded
    target is a test-harness concern, so it is resolved here rather than by
    widening the production entry point.
    """
    # Local import avoids a circular import at module load time.
    from alembic import command
    from butlers.migrations import _build_alembic_config, _normalize_schema

    normalized_schema = _normalize_schema(schema)
    config = _build_alembic_config(db_url, [chain], target_schema=normalized_schema)
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
    """Create a schema-accurate asyncpg pool without blocking an active event loop.

    The synchronous migration factory deliberately owns disposable database
    creation, privileged ``init-db.sql`` staging, and ordinary NOCREATEDB
    migrations.  Async integration fixtures must use it in a worker thread,
    then connect their pool with the same JSONB and search-path behavior as the
    production :class:`butlers.db.Database` wrapper.
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

    return await asyncpg.create_pool(db_url, **pool_kwargs)

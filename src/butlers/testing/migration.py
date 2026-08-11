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


def _bootstrap_migration_prerequisites(bootstrap_db_url: str, migration_user: str) -> None:
    """Stage the production bootstrap contract in a disposable migration database.

    ``core_196`` deliberately invokes only the installer provisioned by
    ``scripts/init-db.sql``.  Migration tests use a fresh testcontainer database,
    so they must stage that trusted bootstrap boundary before exercising the
    ordinary, non-privileged migration path.
    """
    source = _INIT_DB.read_text(encoding="utf-8")
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

    db_url = create_migration_db(postgres_container, db_name)

    for chain in chains:
        schema = schemas.get(chain)
        asyncio.run(run_migrations(db_url, chain=chain, schema=schema))

    return db_url

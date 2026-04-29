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
from urllib.parse import urlparse

import asyncpg
from sqlalchemy import create_engine, text


def migration_db_name() -> str:
    """Return a unique database name suitable for one migration test run."""
    return f"test_{uuid.uuid4().hex[:12]}"


def create_migration_db(postgres_container: object, db_name: str) -> str:
    """Provision a fresh database on *postgres_container* and return its SQLAlchemy URL.

    Parameters
    ----------
    postgres_container:
        A ``testcontainers.postgres.PostgresContainer`` instance (typed loosely to
        avoid a hard import-time dependency on testcontainers).
    db_name:
        Name of the database to create.  Must be a valid PostgreSQL identifier.
    """
    admin_url = postgres_container.get_connection_url()  # type: ignore[attr-defined]
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        safe = db_name.replace('"', '""')
        conn.execute(text(f'CREATE DATABASE "{safe}"'))
    engine.dispose()

    host = postgres_container.get_container_host_ip()  # type: ignore[attr-defined]
    port = postgres_container.get_exposed_port(5432)  # type: ignore[attr-defined]
    user = postgres_container.username  # type: ignore[attr-defined]
    password = postgres_container.password  # type: ignore[attr-defined]
    db_url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"

    # Activate required extensions before any migration chain runs.
    bootstrap_extensions(db_url)

    return db_url


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


def table_exists(db_url: str, table_name: str) -> bool:
    """Return True when *table_name* exists in the ``public`` schema."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_schema = 'public' AND table_name = :t"
                ")"
            ),
            {"t": table_name},
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


def index_exists(db_url: str, index_name: str) -> bool:
    """Return True when *index_name* exists in the ``public`` schema."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM pg_indexes"
                "  WHERE schemaname = 'public' AND indexname = :i"
                ")"
            ),
            {"i": index_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def get_column_info(db_url: str, table_name: str, column_name: str) -> dict | None:
    """Return column metadata from ``information_schema``, or None if absent.

    The returned dict has keys: ``data_type``, ``column_default``, ``is_nullable``.
    """
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT data_type, column_default, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
            ),
            {"t": table_name, "c": column_name},
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

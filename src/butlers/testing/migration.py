"""Shared helpers for migration integration tests.

These utilities are used by both ``tests/config/`` and ``roster/*/tests/``
to avoid duplicating the same boilerplate across migration test files.

They are intentionally free of pytest fixtures so they can be imported from
any test context, including roster-local test trees.
"""

from __future__ import annotations

import uuid

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
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


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

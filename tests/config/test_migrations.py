"""Tests for Alembic migration infrastructure using testcontainers."""

from __future__ import annotations

import asyncio
import shutil
import uuid

import pytest
from sqlalchemy import create_engine, text

# Skip all tests if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

REQUIRED_SCHEMAS = ("shared", "general", "health", "messenger", "relationship", "switchboard")


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for migration tests."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as postgres:
        yield postgres


def _create_db(postgres_container, db_name: str) -> str:
    """Create a fresh database and return its SQLAlchemy URL."""
    admin_url = postgres_container.get_connection_url()
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        safe = db_name.replace('"', '""')
        conn.execute(text(f'CREATE DATABASE "{safe}"'))
    engine.dispose()

    # Build URL pointing at the new database
    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    user = postgres_container.username
    password = postgres_container.password
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


def _table_exists(db_url: str, table_name: str) -> bool:
    """Check whether a table exists in the database."""
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


def _schema_exists(db_url: str, schema_name: str) -> bool:
    """Check whether a schema exists in the database."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.schemata"
                "  WHERE schema_name = :s"
                ")"
            ),
            {"s": schema_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _schema_owner(db_url: str, schema_name: str) -> str | None:
    """Return schema owner role name, or None if schema does not exist."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT pg_catalog.pg_get_userbyid(n.nspowner) "
                "FROM pg_namespace n "
                "WHERE n.nspname = :s"
            ),
            {"s": schema_name},
        )
        owner = result.scalar()
    engine.dispose()
    return owner


def _current_user(db_url: str) -> str:
    """Return the current DB user for the connection."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT current_user"))
        user = result.scalar()
    engine.dispose()
    assert isinstance(user, str)
    return user


def test_core_migrations_create_tables(postgres_container):
    """Run core migrations and verify all core tables are created."""
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    assert _table_exists(db_url, "state"), "state table should exist"
    assert _table_exists(db_url, "scheduled_tasks"), "scheduled_tasks table should exist"
    assert _table_exists(db_url, "sessions"), "sessions table should exist"
    assert _table_exists(db_url, "route_inbox"), "route_inbox table should exist"
    assert _table_exists(db_url, "butler_secrets"), "butler_secrets table should exist"

    for schema in REQUIRED_SCHEMAS:
        assert _schema_exists(db_url, schema), f"schema {schema!r} should exist"


def test_core_schema_bootstrap_owner_baseline(postgres_container):
    """Schema bootstrap sets owner baseline to migration user on fresh installs."""
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    expected_owner = _current_user(db_url)
    for schema in REQUIRED_SCHEMAS:
        assert _schema_owner(db_url, schema) == expected_owner


def test_migrations_idempotent(postgres_container):
    """Running migrations twice should not raise errors."""
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    # Second run should succeed without errors
    asyncio.run(run_migrations(db_url, chain="core"))

    assert _table_exists(db_url, "state")
    assert _table_exists(db_url, "scheduled_tasks")
    assert _table_exists(db_url, "sessions")
    for schema in REQUIRED_SCHEMAS:
        assert _schema_exists(db_url, schema)


def test_upgrade_from_core_009_creates_required_schemas(postgres_container):
    """Upgrade path from previous core head creates one-db schemas cleanly."""
    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core_009")
    command.upgrade(config, "core@head")

    for schema in REQUIRED_SCHEMAS:
        assert _schema_exists(db_url, schema), f"schema {schema!r} should exist after upgrade path"


def test_alembic_version_tracking(postgres_container):
    """After migration, alembic_version table should have the correct entry."""
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    assert _table_exists(db_url, "alembic_version"), "alembic_version table should exist"

    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT version_num FROM alembic_version"))
        versions = [row[0] for row in result]
    engine.dispose()

    assert "core_010" in versions, f"Expected revision 'core_010' (current head) in {versions}"

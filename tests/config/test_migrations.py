"""Tests for Alembic migration infrastructure using testcontainers."""

from __future__ import annotations

import asyncio
import shutil
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

# Skip all tests if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

REQUIRED_SCHEMAS = ("shared", "general", "health", "messenger", "relationship", "switchboard")
CORE_HEAD_REVISION = "core_001"
RUNTIME_ROLES = {
    "general": "butler_general_rw",
    "health": "butler_health_rw",
    "messenger": "butler_messenger_rw",
    "relationship": "butler_relationship_rw",
    "switchboard": "butler_switchboard_rw",
}


def _quote_ident(identifier: str) -> str:
    """Quote an identifier for SQL text construction."""
    return '"' + identifier.replace('"', '""') + '"'


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


def _table_exists_in_schema(db_url: str, schema_name: str, table_name: str) -> bool:
    """Check whether a table exists in a specific schema."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_schema = :s AND table_name = :t"
                ")"
            ),
            {"s": schema_name, "t": table_name},
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


def _role_exists(db_url: str, role_name: str) -> bool:
    """Return True when role exists in pg_roles."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :r)"),
            {"r": role_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _execute_as_role(db_url: str, role_name: str, sql: str, *, scalar: bool = False):
    """Execute SQL after SET ROLE and optionally return scalar result."""
    quoted_role = _quote_ident(role_name)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET ROLE {quoted_role}"))
            try:
                result = conn.execute(text(sql))
                if scalar:
                    return result.scalar()
                return None
            finally:
                conn.execute(text("RESET ROLE"))
    finally:
        engine.dispose()


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
    assert not _table_exists(db_url, "google_oauth_credentials"), (
        "legacy google_oauth_credentials table should not exist in target-state baseline"
    )

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


def test_upgrade_to_core_head_creates_required_schemas(postgres_container):
    """Upgrade to core head creates one-db schemas cleanly."""
    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core@head")

    for schema in REQUIRED_SCHEMAS:
        assert _schema_exists(db_url, schema), f"schema {schema!r} should exist after upgrade path"


def test_core_acl_runtime_role_isolation(postgres_container):
    """Core ACL migration enforces own-schema + shared access with cross-schema denial."""
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))

    # Seed existing objects after ACL migration to validate object-level grants.
    setup_engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with setup_engine.connect() as conn:
            conn.execute(
                text("CREATE TABLE general.acl_general_existing (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(
                text("CREATE TABLE health.acl_health_existing (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(
                text("CREATE TABLE shared.acl_shared_existing (id SERIAL PRIMARY KEY, note TEXT)")
            )
            conn.execute(text("INSERT INTO shared.acl_shared_existing (note) VALUES ('seed')"))

            # Validate default privilege behavior for future objects created by owner.
            conn.execute(
                text("CREATE TABLE general.acl_general_future (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(
                text("CREATE TABLE health.acl_health_future (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(text("INSERT INTO health.acl_health_future (id, note) VALUES (1, 'h1')"))
            conn.execute(
                text("CREATE TABLE shared.acl_shared_future (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(text("INSERT INTO shared.acl_shared_future (id, note) VALUES (1, 's1')"))
    finally:
        setup_engine.dispose()

    for runtime_role in RUNTIME_ROLES.values():
        assert _role_exists(db_url, runtime_role), f"expected role {runtime_role!r} to exist"

    general_role = RUNTIME_ROLES["general"]

    _execute_as_role(
        db_url,
        general_role,
        "INSERT INTO general.acl_general_existing (id, note) VALUES (1, 'ok')",
    )
    own_note = _execute_as_role(
        db_url,
        general_role,
        "SELECT note FROM general.acl_general_existing WHERE id = 1",
        scalar=True,
    )
    assert own_note == "ok"

    # Own-schema default privileges should apply to future owner-created objects.
    _execute_as_role(
        db_url,
        general_role,
        "INSERT INTO general.acl_general_future (id, note) VALUES (2, 'future-ok')",
    )

    # Shared schema is intentionally read-only for runtime roles.
    shared_note = _execute_as_role(
        db_url,
        general_role,
        "SELECT note FROM shared.acl_shared_existing ORDER BY id LIMIT 1",
        scalar=True,
    )
    assert shared_note == "seed"

    shared_future_note = _execute_as_role(
        db_url,
        general_role,
        "SELECT note FROM shared.acl_shared_future WHERE id = 1",
        scalar=True,
    )
    assert shared_future_note == "s1"

    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            db_url,
            general_role,
            "INSERT INTO shared.acl_shared_existing (note) VALUES ('blocked')",
        )

    # Cross-butler schema access must be denied.
    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(db_url, general_role, "SELECT * FROM health.acl_health_existing")

    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(db_url, general_role, "SELECT * FROM health.acl_health_future")


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

    assert CORE_HEAD_REVISION in versions, (
        f"Expected revision {CORE_HEAD_REVISION!r} (current head) in {versions}"
    )


def test_schema_scoped_alembic_version_tracking_isolated(postgres_container):
    """Schema-scoped runs should track revisions in separate schema-local tables."""
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core", schema="general"))
    asyncio.run(run_migrations(db_url, chain="core", schema="health"))

    assert _table_exists_in_schema(db_url, "general", "alembic_version")
    assert _table_exists_in_schema(db_url, "health", "alembic_version")
    assert not _table_exists_in_schema(db_url, "public", "alembic_version")

    engine = create_engine(db_url)
    with engine.connect() as conn:
        general_versions = [
            row[0] for row in conn.execute(text("SELECT version_num FROM general.alembic_version"))
        ]
        health_versions = [
            row[0] for row in conn.execute(text("SELECT version_num FROM health.alembic_version"))
        ]
    engine.dispose()

    assert CORE_HEAD_REVISION in general_versions
    assert CORE_HEAD_REVISION in health_versions

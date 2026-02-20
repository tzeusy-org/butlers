"""Integration tests for one-DB schema ACL isolation and intentional fanout reads.

Issue: butlers-1003.6
"""

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

_BUTLER_SCHEMAS = ("general", "health", "messenger", "relationship", "switchboard")
_RUNTIME_ROLES = {
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

    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    user = postgres_container.username
    password = postgres_container.password
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


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


def _require_runtime_acl(db_url: str) -> None:
    """Skip tests when runtime ACL migration has not yet been applied."""
    missing = [role for role in _RUNTIME_ROLES.values() if not _role_exists(db_url, role)]
    if missing:
        pytest.skip(
            "Runtime ACL roles are not present; requires core runtime ACL migration "
            f"(missing: {', '.join(missing)})"
        )


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


def test_runtime_roles_are_limited_to_own_schema_and_shared(postgres_container):
    """Each runtime role can write own schema, read shared, and cannot read another schema."""
    from butlers.migrations import run_migrations

    db_url = _create_db(postgres_container, _unique_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    _require_runtime_acl(db_url)

    setup_engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with setup_engine.connect() as conn:
            for schema in _BUTLER_SCHEMAS:
                conn.execute(
                    text(f"CREATE TABLE {schema}.acl_probe (id INT PRIMARY KEY, note TEXT)")
                )
            conn.execute(
                text("CREATE TABLE shared.acl_probe_shared (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(
                text("INSERT INTO shared.acl_probe_shared (id, note) VALUES (1, 'shared-ok')")
            )
    finally:
        setup_engine.dispose()

    for owned_schema, runtime_role in _RUNTIME_ROLES.items():
        _execute_as_role(
            db_url,
            runtime_role,
            f"INSERT INTO {owned_schema}.acl_probe (id, note) VALUES (1, '{owned_schema}-ok')",
        )
        own_note = _execute_as_role(
            db_url,
            runtime_role,
            f"SELECT note FROM {owned_schema}.acl_probe WHERE id = 1",
            scalar=True,
        )
        assert own_note == f"{owned_schema}-ok"

        shared_note = _execute_as_role(
            db_url,
            runtime_role,
            "SELECT note FROM shared.acl_probe_shared WHERE id = 1",
            scalar=True,
        )
        assert shared_note == "shared-ok"

        with pytest.raises(ProgrammingError, match="permission denied"):
            _execute_as_role(
                db_url,
                runtime_role,
                "INSERT INTO shared.acl_probe_shared (id, note) VALUES (2, 'blocked')",
            )

        blocked_schema = next(schema for schema in _BUTLER_SCHEMAS if schema != owned_schema)
        with pytest.raises(ProgrammingError, match="permission denied"):
            _execute_as_role(
                db_url,
                runtime_role,
                f"SELECT id FROM {blocked_schema}.acl_probe LIMIT 1",
            )


def test_privileged_cross_schema_aggregate_reads_are_allowed(postgres_container):
    """Privileged connections can aggregate across butler schemas intentionally."""
    from butlers.migrations import run_migrations

    db_url = _create_db(postgres_container, _unique_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    _require_runtime_acl(db_url)

    setup_engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with setup_engine.connect() as conn:
            conn.execute(text("CREATE TABLE general.acl_fanout (id INT PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE health.acl_fanout (id INT PRIMARY KEY)"))
            conn.execute(text("INSERT INTO general.acl_fanout (id) VALUES (1), (2)"))
            conn.execute(text("INSERT INTO health.acl_fanout (id) VALUES (1)"))
    finally:
        setup_engine.dispose()

    admin_engine = create_engine(db_url)
    try:
        with admin_engine.connect() as conn:
            total = conn.execute(
                text(
                    "SELECT SUM(cnt) FROM ("
                    "  SELECT COUNT(*)::INT AS cnt FROM general.acl_fanout "
                    "  UNION ALL "
                    "  SELECT COUNT(*)::INT AS cnt FROM health.acl_fanout"
                    ") t"
                )
            ).scalar()
    finally:
        admin_engine.dispose()

    assert total == 3

    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            db_url,
            _RUNTIME_ROLES["general"],
            "SELECT SUM(cnt) FROM ("
            "  SELECT COUNT(*)::INT AS cnt FROM general.acl_fanout "
            "  UNION ALL "
            "  SELECT COUNT(*)::INT AS cnt FROM health.acl_fanout"
            ") t",
            scalar=True,
        )

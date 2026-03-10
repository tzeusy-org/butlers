"""Tests for butlers.db — asyncpg connection pool and DB provisioning."""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _unique_db_name() -> str:
    """Generate a unique database name for test isolation."""
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17") as postgres:
        yield postgres


@pytest.fixture
def db_factory(postgres_container):
    """Factory that creates Database instances wired to the test container."""
    from butlers.db import Database

    def _make(db_name: str | None = None) -> Database:
        return Database(
            db_name=db_name or _unique_db_name(),
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            min_pool_size=1,
            max_pool_size=3,
        )

    return _make


async def test_provision_creates_database(db_factory):
    """provision() creates a new database that didn't exist before."""
    db = db_factory()

    # Verify the database does not exist yet
    conn = await asyncpg.connect(
        host=db.host,
        port=db.port,
        user=db.user,
        password=db.password,
        database="postgres",
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            db.db_name,
        )
        assert exists is None
    finally:
        await conn.close()

    # Provision and verify creation
    await db.provision()

    conn = await asyncpg.connect(
        host=db.host,
        port=db.port,
        user=db.user,
        password=db.password,
        database="postgres",
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            db.db_name,
        )
        assert exists == 1
    finally:
        await conn.close()


async def test_provision_existing_database(db_factory):
    """provision() is idempotent — calling it twice doesn't raise."""
    db = db_factory()

    await db.provision()
    # Second call should not raise
    await db.provision()

    # Database should still exist
    conn = await asyncpg.connect(
        host=db.host,
        port=db.port,
        user=db.user,
        password=db.password,
        database="postgres",
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            db.db_name,
        )
        assert exists == 1
    finally:
        await conn.close()


async def test_connect_creates_pool(db_factory):
    """connect() returns a usable asyncpg.Pool."""
    db = db_factory()
    await db.provision()

    pool = await db.connect()
    try:
        assert pool is not None
        assert db.pool is pool
        assert isinstance(pool, asyncpg.Pool)
    finally:
        await db.close()


async def test_pool_executes_queries(db_factory):
    """The connection pool can execute simple queries."""
    db = db_factory()
    await db.provision()
    await db.connect()

    try:
        assert db.pool is not None
        result = await db.pool.fetchval("SELECT 1 + 1")
        assert result == 2

        # Test creating a table and inserting data
        async with db.pool.acquire() as conn:
            await conn.execute("CREATE TABLE test_table (id serial PRIMARY KEY, value text)")
            await conn.execute("INSERT INTO test_table (value) VALUES ($1)", "hello")
            row = await conn.fetchrow("SELECT value FROM test_table WHERE id = 1")
            assert row is not None
            assert row["value"] == "hello"
    finally:
        await db.close()


async def test_close_releases_pool(db_factory):
    """close() releases the pool and sets it to None."""
    db = db_factory()
    await db.provision()
    await db.connect()

    assert db.pool is not None
    await db.close()
    assert db.pool is None

    # Calling close again should be a no-op (no error)
    await db.close()
    assert db.pool is None


def test_from_env_database_url(monkeypatch):
    """from_env() reads connection parameters from DATABASE_URL."""
    from butlers.db import Database

    monkeypatch.setenv("DATABASE_URL", "postgres://myuser:mypass@myhost:6543/postgres")

    db = Database.from_env("test_db")

    assert db.db_name == "test_db"
    assert db.host == "myhost"
    assert db.port == 6543
    assert db.user == "myuser"
    assert db.password == "mypass"


def test_from_env_database_url_minimal(monkeypatch):
    """from_env() handles DATABASE_URL with minimal components."""
    from butlers.db import Database

    monkeypatch.setenv("DATABASE_URL", "postgres://localhost/postgres")

    db = Database.from_env("test_db")

    assert db.db_name == "test_db"
    assert db.host == "localhost"
    assert db.port == 5432  # Default port
    assert db.user == "butlers"  # Default user
    assert db.password == "butlers"  # Default password


def test_from_env_database_url_no_port(monkeypatch):
    """from_env() uses default port when DATABASE_URL omits it."""
    from butlers.db import Database

    monkeypatch.setenv("DATABASE_URL", "postgres://myuser:mypass@myhost/postgres")

    db = Database.from_env("test_db")

    assert db.db_name == "test_db"
    assert db.host == "myhost"
    assert db.port == 5432  # Default port
    assert db.user == "myuser"
    assert db.password == "mypass"


def test_from_env_fallback_to_individual_vars(monkeypatch):
    """from_env() falls back to POSTGRES_* vars when DATABASE_URL is not set."""
    from butlers.db import Database

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "myhost")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_USER", "myuser")
    monkeypatch.setenv("POSTGRES_PASSWORD", "mypass")

    db = Database.from_env("test_db")

    assert db.db_name == "test_db"
    assert db.host == "myhost"
    assert db.port == 6543
    assert db.user == "myuser"
    assert db.password == "mypass"


def test_from_env_defaults_spec_compliant(monkeypatch):
    """from_env() falls back to spec-compliant defaults when no env vars are set."""
    from butlers.db import Database

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    db = Database.from_env("test_db")

    assert db.db_name == "test_db"
    # Spec default: postgres://butlers:butlers@localhost/postgres
    assert db.host == "localhost"
    assert db.port == 5432
    assert db.user == "butlers"
    assert db.password == "butlers"


def test_from_env_database_url_takes_precedence(monkeypatch):
    """from_env() prefers DATABASE_URL over individual POSTGRES_* vars."""
    from butlers.db import Database

    monkeypatch.setenv("DATABASE_URL", "postgres://url_user:url_pass@url_host:7777/postgres")
    monkeypatch.setenv("POSTGRES_HOST", "var_host")
    monkeypatch.setenv("POSTGRES_PORT", "8888")
    monkeypatch.setenv("POSTGRES_USER", "var_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "var_pass")

    db = Database.from_env("test_db")

    # DATABASE_URL should take precedence
    assert db.db_name == "test_db"
    assert db.host == "url_host"
    assert db.port == 7777
    assert db.user == "url_user"
    assert db.password == "url_pass"

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
    """from_env() reads DATABASE_URL (full, minimal, no-port); prefers it over POSTGRES_* vars."""
    from butlers.db import Database

    # Full URL
    monkeypatch.setenv("DATABASE_URL", "postgres://myuser:mypass@myhost:6543/postgres")
    db = Database.from_env("test_db")
    assert db.host == "myhost" and db.port == 6543 and db.user == "myuser"

    # Minimal URL: uses defaults for missing fields
    monkeypatch.setenv("DATABASE_URL", "postgres://localhost/postgres")
    db2 = Database.from_env("test_db")
    assert db2.host == "localhost" and db2.port == 5432 and db2.user == "butlers"

    # No port: defaults to 5432
    monkeypatch.setenv("DATABASE_URL", "postgres://myuser:mypass@myhost/postgres")
    db3 = Database.from_env("test_db")
    assert db3.host == "myhost" and db3.port == 5432

    # DATABASE_URL takes precedence over POSTGRES_* vars
    monkeypatch.setenv("DATABASE_URL", "postgres://url_user:url_pass@url_host:7777/postgres")
    monkeypatch.setenv("POSTGRES_HOST", "var_host")
    monkeypatch.setenv("POSTGRES_PORT", "8888")
    db4 = Database.from_env("test_db")
    assert db4.host == "url_host" and db4.port == 7777 and db4.user == "url_user"


def test_from_env_fallback_and_defaults(monkeypatch):
    """from_env() falls back to POSTGRES_* vars; uses spec defaults when none set."""
    from butlers.db import Database

    # POSTGRES_* vars used when DATABASE_URL absent
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "myhost")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_USER", "myuser")
    monkeypatch.setenv("POSTGRES_PASSWORD", "mypass")
    db = Database.from_env("test_db")
    assert db.host == "myhost" and db.port == 6543 and db.user == "myuser"

    # Spec defaults when all env vars absent
    for var in ("DATABASE_URL", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
                "POSTGRES_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    db2 = Database.from_env("test_db")
    assert db2.host == "localhost" and db2.port == 5432 and db2.user == "butlers"

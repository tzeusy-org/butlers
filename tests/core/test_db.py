"""Tests for butlers.db — asyncpg connection pool and DB provisioning."""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17") as postgres:
        yield postgres


@pytest.fixture
def db_factory(postgres_container):
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


async def test_provision_connect_pool_close(db_factory):
    """provision() creates DB; idempotent; connect() returns usable pool; close releases it."""
    db = db_factory()

    # DB does not exist yet
    conn = await asyncpg.connect(
        host=db.host, port=db.port, user=db.user, password=db.password, database="postgres"
    )
    try:
        assert (
            await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db.db_name) is None
        )
    finally:
        await conn.close()

    # provision creates DB; idempotent second call
    await db.provision()
    await db.provision()  # must not raise

    conn2 = await asyncpg.connect(
        host=db.host, port=db.port, user=db.user, password=db.password, database="postgres"
    )
    try:
        assert await conn2.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db.db_name) == 1
    finally:
        await conn2.close()

    # connect() returns usable pool
    pool = await db.connect()
    assert pool is not None and db.pool is pool and isinstance(pool, asyncpg.Pool)

    # Pool executes queries
    assert await db.pool.fetchval("SELECT 1 + 1") == 2
    async with db.pool.acquire() as c:
        await c.execute("CREATE TABLE test_table (id serial PRIMARY KEY, value text)")
        await c.execute("INSERT INTO test_table (value) VALUES ($1)", "hello")
        row = await c.fetchrow("SELECT value FROM test_table WHERE id = 1")
        assert row["value"] == "hello"

    # close() releases pool; idempotent
    await db.close()
    assert db.pool is None
    await db.close()  # no error
    assert db.pool is None


def test_from_env_parsing(monkeypatch):
    """from_env() reads DATABASE_URL (full/minimal/no-port); prefers over POSTGRES_*;
    falls back to env vars; spec defaults."""
    from butlers.db import Database

    # Full URL
    monkeypatch.setenv("DATABASE_URL", "postgres://myuser:mypass@myhost:6543/postgres")
    db = Database.from_env("test_db")
    assert db.host == "myhost" and db.port == 6543 and db.user == "myuser"

    # Minimal URL: spec defaults for missing fields
    monkeypatch.setenv("DATABASE_URL", "postgres://localhost/postgres")
    assert Database.from_env("test_db").port == 5432

    # DATABASE_URL takes precedence over POSTGRES_* vars
    monkeypatch.setenv("DATABASE_URL", "postgres://url_user:url_pass@url_host:7777/postgres")
    monkeypatch.setenv("POSTGRES_HOST", "var_host")
    db2 = Database.from_env("test_db")
    assert db2.host == "url_host" and db2.port == 7777

    # POSTGRES_* vars used when DATABASE_URL absent
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "myhost")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_USER", "myuser")
    monkeypatch.setenv("POSTGRES_PASSWORD", "mypass")
    db3 = Database.from_env("test_db")
    assert db3.host == "myhost" and db3.port == 6543 and db3.user == "myuser"

    # Spec defaults when all env vars absent
    for var in (
        "DATABASE_URL",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    db4 = Database.from_env("test_db")
    assert db4.host == "localhost" and db4.port == 5432 and db4.user == "butlers"


def test_from_env_uses_pool_size_overrides(monkeypatch):
    """from_env() applies runtime pool-size overrides for constrained dev DBs."""
    from butlers.db import Database

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("BUTLERS_DB_POOL_MIN_SIZE", "0")
    monkeypatch.setenv("BUTLERS_DB_POOL_MAX_SIZE", "4")

    db = Database.from_env("test_db")

    assert db.min_pool_size == 0
    assert db.max_pool_size == 4

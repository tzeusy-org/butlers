"""Integration tests for per-butler schema isolation.

Proves that the one-db/multi-schema topology enforces write isolation between
butler schemas while keeping the shared schema visible from both paths.

The search_path logic under test lives in ``butlers.db.schema_search_path``.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_raw_pool(postgres_container, db_name: str) -> asyncpg.Pool:
    """Open an asyncpg pool to *db_name* without any search_path override."""
    return await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=3,
    )


async def _make_schema_pool(
    postgres_container,
    db_name: str,
    search_path: str,
) -> asyncpg.Pool:
    """Open an asyncpg pool to *db_name* with *search_path* injected as a server setting."""
    return await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        server_settings={"search_path": search_path},
        min_size=1,
        max_size=3,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def isolated_db(postgres_container):
    """Create a fresh database with butler_alpha, butler_beta, and shared schemas.

    Yields the database name; teardown closes the admin connection.
    """
    import uuid

    db_name = f"test_{uuid.uuid4().hex[:12]}"

    admin_conn = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database="postgres",
    )
    try:
        await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin_conn.close()

    # Bootstrap schemas and tables using a direct (no search_path) admin pool.
    admin_pool = await _make_raw_pool(postgres_container, db_name)
    try:
        await admin_pool.execute("CREATE SCHEMA IF NOT EXISTS butler_alpha")
        await admin_pool.execute("CREATE SCHEMA IF NOT EXISTS butler_beta")
        await admin_pool.execute("CREATE SCHEMA IF NOT EXISTS shared")

        # Per-butler private table (same name in both schemas — tests that writes
        # to one schema are not visible through the other schema's search_path).
        for schema in ("butler_alpha", "butler_beta"):
            await admin_pool.execute(f"""
                CREATE TABLE {schema}.private_events (
                    id SERIAL PRIMARY KEY,
                    payload TEXT NOT NULL
                )
            """)

        # Shared table — must be visible from both butler search paths.
        await admin_pool.execute("""
            CREATE TABLE shared.global_contacts (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL
            )
        """)
        await admin_pool.execute("INSERT INTO shared.global_contacts (name) VALUES ($1)", "Alice")
    finally:
        await admin_pool.close()

    yield db_name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def alpha_pool(postgres_container, isolated_db):
    """Pool scoped to butler_alpha's search path."""
    from butlers.db import schema_search_path

    path = schema_search_path("butler_alpha")
    pool = await _make_schema_pool(postgres_container, isolated_db, path)
    yield pool
    await pool.close()


@pytest.fixture
async def beta_pool(postgres_container, isolated_db):
    """Pool scoped to butler_beta's search path."""
    from butlers.db import schema_search_path

    path = schema_search_path("butler_beta")
    pool = await _make_schema_pool(postgres_container, isolated_db, path)
    yield pool
    await pool.close()


class TestSchemaIsolation:
    """Cross-schema write isolation between butler_alpha and butler_beta."""

    async def test_write_via_alpha_not_visible_via_beta(self, alpha_pool, beta_pool):
        """Data inserted through butler_alpha's path must not appear via butler_beta's path."""
        payload = "alpha-isolated-event-abc123"
        await alpha_pool.execute("INSERT INTO private_events (payload) VALUES ($1)", payload)

        # butler_beta's search_path resolves 'private_events' to butler_beta.private_events.
        # The row we just inserted lives in butler_alpha.private_events, not beta's.
        row = await beta_pool.fetchrow("SELECT 1 FROM private_events WHERE payload = $1", payload)
        assert row is None, (
            "butler_beta should not see rows inserted via butler_alpha's search path"
        )

    async def test_write_via_beta_not_visible_via_alpha(self, alpha_pool, beta_pool):
        """Data inserted through butler_beta's path must not appear via butler_alpha's path."""
        payload = "beta-isolated-event-xyz789"
        await beta_pool.execute("INSERT INTO private_events (payload) VALUES ($1)", payload)

        row = await alpha_pool.fetchrow("SELECT 1 FROM private_events WHERE payload = $1", payload)
        assert row is None, (
            "butler_alpha should not see rows inserted via butler_beta's search path"
        )

    async def test_shared_schema_visible_from_alpha(self, alpha_pool):
        """The shared schema's global_contacts table must be queryable from butler_alpha's path."""
        row = await alpha_pool.fetchrow(
            "SELECT name FROM global_contacts WHERE name = 'Alice'"
        )
        assert row is not None, (
            "butler_alpha should be able to read from shared.global_contacts"
        )

    async def test_shared_schema_visible_from_beta(self, beta_pool):
        """The shared schema's global_contacts table must be queryable from butler_beta's path."""
        row = await beta_pool.fetchrow(
            "SELECT name FROM global_contacts WHERE name = 'Alice'"
        )
        assert row is not None, (
            "butler_beta should be able to read from shared.global_contacts"
        )

    async def test_shared_write_visible_from_both_schemas(self, alpha_pool, beta_pool):
        """A row written to shared via alpha's path must be readable via beta's path."""
        await alpha_pool.execute("INSERT INTO global_contacts (name) VALUES ($1)", "Bob")

        beta_names = await beta_pool.fetch("SELECT name FROM global_contacts WHERE name = 'Bob'")
        assert len(beta_names) == 1, (
            "A row written to shared via butler_alpha must be visible via butler_beta"
        )

    async def test_schema_search_path_builder(self):
        """schema_search_path() returns the correct comma-separated path string."""
        from butlers.db import schema_search_path

        path = schema_search_path("my_butler")
        assert path == "my_butler,shared,public"

    async def test_schema_search_path_deduplicates_shared(self):
        """schema_search_path() deduplicates if schema name is 'shared'."""
        from butlers.db import schema_search_path

        path = schema_search_path("shared")
        # 'shared' should appear only once; no duplicate in the path
        parts = path.split(",")
        assert parts.count("shared") == 1

    async def test_schema_search_path_none_returns_none(self):
        """schema_search_path() returns None for None or blank schema."""
        from butlers.db import schema_search_path

        assert schema_search_path(None) is None
        assert schema_search_path("") is None
        assert schema_search_path("   ") is None

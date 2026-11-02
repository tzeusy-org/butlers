"""Integration tests for per-butler schema isolation.

Proves that the one-db/multi-schema topology enforces write isolation between
butler schemas while keeping the public schema visible from both paths.

The search_path logic under test lives in ``butlers.db.schema_search_path``.
The schemas are the real migrated ones: the core chain gives the ``general``
and ``finance`` butler schemas each their own ``sessions`` table, and
``public.entities`` is shared by every butler.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.testing.db import open_pool

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_ALPHA = "general"
_BETA = "finance"

# Per-butler private table (same name in both schemas — tests that writes to
# one schema are not visible through the other schema's search_path).
_INSERT_PRIVATE = (
    "INSERT INTO sessions (prompt, trigger_source, request_id) "
    "VALUES ($1, 'schedule', gen_random_uuid()::text)"
)
_SELECT_PRIVATE = "SELECT 1 FROM sessions WHERE prompt = $1"
_INSERT_PUBLIC = "INSERT INTO entities (canonical_name, entity_type) VALUES ($1, 'person')"
_SELECT_PUBLIC = "SELECT canonical_name FROM entities WHERE canonical_name = $1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def isolated_db(fresh_db) -> str:
    """A fresh core-migrated database with one shared public row, ``Alice``."""
    url = fresh_db("core")
    conn = await asyncpg.connect(url)
    try:
        # Public table — must be visible from both butler search paths.
        await conn.execute(
            "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, 'person')",
            "Alice",
        )
    finally:
        await conn.close()
    return url


@pytest.fixture
async def alpha_pool(isolated_db):
    """Pool scoped to the alpha butler's search path."""
    pool = await open_pool(isolated_db, schema=_ALPHA)
    yield pool
    await pool.close()


@pytest.fixture
async def beta_pool(isolated_db):
    """Pool scoped to the beta butler's search path."""
    pool = await open_pool(isolated_db, schema=_BETA)
    yield pool
    await pool.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSchemaIsolation:
    """Cross-schema write isolation between two butler schemas."""

    async def test_write_via_alpha_not_visible_via_beta(self, alpha_pool, beta_pool):
        """Data inserted through alpha's path must not appear via beta's path."""
        payload = "alpha-isolated-event-abc123"
        await alpha_pool.execute(_INSERT_PRIVATE, payload)

        # beta's search_path resolves 'sessions' to its own schema's sessions.
        # The row we just inserted lives in alpha's sessions, not beta's.
        row = await beta_pool.fetchrow(_SELECT_PRIVATE, payload)
        assert row is None, "beta should not see rows inserted via alpha's search path"

    async def test_write_via_beta_not_visible_via_alpha(self, alpha_pool, beta_pool):
        """Data inserted through beta's path must not appear via alpha's path."""
        payload = "beta-isolated-event-xyz789"
        await beta_pool.execute(_INSERT_PRIVATE, payload)

        row = await alpha_pool.fetchrow(_SELECT_PRIVATE, payload)
        assert row is None, "alpha should not see rows inserted via beta's search path"

    async def test_public_schema_visible_from_alpha(self, alpha_pool):
        """The public schema's entities table must be queryable from alpha's path."""
        row = await alpha_pool.fetchrow(_SELECT_PUBLIC, "Alice")
        assert row is not None, "alpha should be able to read from public.entities"

    async def test_public_schema_visible_from_beta(self, beta_pool):
        """The public schema's entities table must be queryable from beta's path."""
        row = await beta_pool.fetchrow(_SELECT_PUBLIC, "Alice")
        assert row is not None, "beta should be able to read from public.entities"

    async def test_public_write_visible_from_both_schemas(self, alpha_pool, beta_pool):
        """A row written to public via alpha's path must be readable via beta's path."""
        await alpha_pool.execute(_INSERT_PUBLIC, "Bob")

        beta_names = await beta_pool.fetch(_SELECT_PUBLIC, "Bob")
        assert len(beta_names) == 1, "A row written to public via alpha must be visible via beta"

    async def test_schema_search_path(self):
        """schema_search_path(): correct path string; deduplicates public; None/blank returns None."""
        from butlers.db import schema_search_path

        assert schema_search_path("my_butler") == "my_butler,public"
        assert schema_search_path("public").split(",").count("public") == 1
        assert schema_search_path(None) is None
        assert schema_search_path("") is None
        assert schema_search_path("   ") is None

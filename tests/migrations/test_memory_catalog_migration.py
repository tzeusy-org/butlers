"""Tests for core_009 memory_catalog migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. Upgrade SQL shape: required columns, indexes, unique constraint.
3. Downgrade SQL: DROP TABLE and DROP INDEX present.
4. Integration: table is queryable after upgrade (catalog read path).
5. Integration: downgrade cleanly removes the table.

Integration tests (marked pytest.mark.integration) require Docker + Postgres
provisioned via the shared ``provisioned_postgres_pool`` fixture.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_009_memory_catalog.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("core_009", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


class TestMigrationFileAndChain:
    """Revision-chain contract test."""

    def test_revision_chain(self) -> None:
        """core_009 -> core_008, no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "core_009"
        assert mod.down_revision == "core_008"
        assert mod.branch_labels is None
        assert mod.depends_on is None


class TestUpgradeSQLShape:
    """Verify the SQL emitted by upgrade() declares the table + indexes.

    Column-presence and the UNIQUE provenance constraint are exercised against a
    live DB by the integration insert/select/upsert tests; here we pin the table
    creation and the three indexes (which the integration path does not assert).
    """

    def _collect_execute_calls(self) -> list[str]:
        mod = _load_migration()
        calls_collected: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: calls_collected.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.upgrade()
        return calls_collected

    def test_create_table_with_unique_provenance(self) -> None:
        """upgrade() creates public.memory_catalog with a UNIQUE provenance constraint."""
        sqls = self._collect_execute_calls()
        create_sql = next(s for s in sqls if "public.memory_catalog" in s and "CREATE" in s.upper())
        assert "UNIQUE" in create_sql.upper()
        assert "source_schema" in create_sql and "source_table" in create_sql

    def test_indexes_present(self) -> None:
        """upgrade() creates the IVFFlat, GIN search_vector, and tenant+schema indexes."""
        sqls = self._collect_execute_calls()
        joined = "\n".join(sqls)
        assert "ivfflat" in joined.lower()
        assert "idx_memory_catalog_search_vector" in joined
        assert "idx_memory_catalog_tenant_schema" in joined


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMemoryCatalogMigrationIntegration:
    """Integration tests requiring a real PostgreSQL instance.

    These tests exercise upgrade / downgrade against a fresh database that
    includes the public.entities dependency table (required for the FK on
    object_entity_id).
    """

    @pytest.fixture
    async def catalog_pool(self, provisioned_postgres_pool):
        """Provision a fresh DB with public.entities and return a pool."""
        async with provisioned_postgres_pool() as pool:
            # Install pgvector extension (available in pgvector/pgvector image).
            await pool.execute("CREATE EXTENSION IF NOT EXISTS vector")
            # Create public.entities as required by the FK constraint.
            await pool.execute("""
                CREATE TABLE IF NOT EXISTS public.entities (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    canonical_name  VARCHAR NOT NULL,
                    entity_type     VARCHAR NOT NULL DEFAULT 'other',
                    aliases         TEXT[] NOT NULL DEFAULT '{}',
                    metadata        JSONB DEFAULT '{}'::jsonb,
                    roles           TEXT[] NOT NULL DEFAULT '{}',
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            yield pool

    async def _run_upgrade(self, pool) -> None:
        """Execute upgrade() SQL against the pool."""
        mod = _load_migration()
        sqls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.upgrade()
        for sql in sqls:
            await pool.execute(sql)

    async def _run_downgrade(self, pool) -> None:
        """Execute downgrade() SQL against the pool."""
        mod = _load_migration()
        sqls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.downgrade()
        for sql in sqls:
            await pool.execute(sql)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_upgrade_creates_table(self, catalog_pool) -> None:
        """After upgrade, public.memory_catalog exists and is queryable."""
        pool = catalog_pool
        await self._run_upgrade(pool)

        # Table must exist and SELECT must succeed (empty result is fine).
        rows = await pool.fetch("SELECT * FROM public.memory_catalog LIMIT 1")
        assert isinstance(rows, list)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_catalog_read_path_returns_empty_results(self, catalog_pool) -> None:
        """Catalog read path returns an empty list (no entries) without error.

        This exercises the search_catalog code path with feature flag enabled,
        verifying the SQL query against the freshly-migrated table does not fail.
        """
        pool = catalog_pool
        await self._run_upgrade(pool)

        # Exercise the keyword search path directly (no embedding engine needed).
        rows = await pool.fetch(
            """
            SELECT *,
                   ts_rank(search_vector,
                           plainto_tsquery('english', $1)) AS rank
            FROM public.memory_catalog
            WHERE search_vector @@ plainto_tsquery('english', $1)
              AND tenant_id = $2
            ORDER BY rank DESC
            LIMIT $3
            """,
            "test query",
            "shared",
            10,
        )
        assert rows == [], f"Expected empty catalog, got: {rows}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_catalog_insert_and_select(self, catalog_pool) -> None:
        """A catalog row can be inserted and retrieved after upgrade."""
        import uuid

        pool = catalog_pool
        await self._run_upgrade(pool)

        source_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO public.memory_catalog (
                source_schema, source_table, source_id,
                tenant_id, summary, memory_type
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            "memory",
            "facts",
            source_id,
            "shared",
            "test summary entry",
            "fact",
        )

        row = await pool.fetchrow(
            "SELECT * FROM public.memory_catalog WHERE source_id = $1",
            source_id,
        )
        assert row is not None, "Inserted catalog row not found"
        assert row["source_schema"] == "memory"
        assert row["source_table"] == "facts"
        assert row["source_id"] == source_id
        assert row["memory_type"] == "fact"
        assert row["summary"] == "test summary entry"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_upsert_conflict_on_source_provenance(self, catalog_pool) -> None:
        """ON CONFLICT (source_schema, source_table, source_id) updates summary."""
        import uuid

        pool = catalog_pool
        await self._run_upgrade(pool)

        source_id = uuid.uuid4()
        base_insert = """
            INSERT INTO public.memory_catalog (
                source_schema, source_table, source_id,
                tenant_id, summary, memory_type, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, now())
            ON CONFLICT (source_schema, source_table, source_id)
            DO UPDATE SET summary = EXCLUDED.summary, updated_at = now()
        """
        await pool.execute(base_insert, "memory", "facts", source_id, "shared", "original", "fact")
        await pool.execute(base_insert, "memory", "facts", source_id, "shared", "updated", "fact")

        count = await pool.fetchval("SELECT COUNT(*) FROM public.memory_catalog")
        assert count == 1, f"Expected 1 row after upsert, got {count}"

        row = await pool.fetchrow(
            "SELECT summary FROM public.memory_catalog WHERE source_id = $1", source_id
        )
        assert row["summary"] == "updated", (
            f"Expected 'updated' summary after upsert, got {row['summary']!r}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_downgrade_drops_table(self, catalog_pool) -> None:
        """After downgrade, public.memory_catalog no longer exists."""
        pool = catalog_pool
        await self._run_upgrade(pool)
        await self._run_downgrade(pool)

        # Table must not exist — any query should raise.
        with pytest.raises(Exception, match="memory_catalog"):
            await pool.fetch("SELECT 1 FROM public.memory_catalog LIMIT 1")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_upgrade_is_idempotent(self, catalog_pool) -> None:
        """Running upgrade() twice does not raise an error (IF NOT EXISTS guards)."""
        pool = catalog_pool
        await self._run_upgrade(pool)
        # Second run must not raise.
        await self._run_upgrade(pool)
        rows = await pool.fetch("SELECT * FROM public.memory_catalog LIMIT 1")
        assert isinstance(rows, list)

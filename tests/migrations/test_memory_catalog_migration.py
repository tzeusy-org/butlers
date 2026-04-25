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
    """File-level and revision-chain contract tests."""

    def test_migration_file_exists(self) -> None:
        """core_009_memory_catalog.py exists at expected path."""
        assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"

    def test_revision_id(self) -> None:
        """Revision is core_009."""
        mod = _load_migration()
        assert mod.revision == "core_009"

    def test_down_revision(self) -> None:
        """down_revision points to core_008."""
        mod = _load_migration()
        assert mod.down_revision == "core_008"

    def test_branch_labels_none(self) -> None:
        """Non-root migrations must not declare branch_labels."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        """No cross-chain dependency declared."""
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_callable(self) -> None:
        """upgrade() is a callable."""
        mod = _load_migration()
        assert callable(getattr(mod, "upgrade", None))

    def test_downgrade_callable(self) -> None:
        """downgrade() is a callable."""
        mod = _load_migration()
        assert callable(getattr(mod, "downgrade", None))


class TestUpgradeSQLShape:
    """Verify the SQL emitted by upgrade() matches the intended schema."""

    def _collect_execute_calls(self) -> list[str]:
        """Run upgrade() with op.execute mocked; return SQL strings."""
        mod = _load_migration()
        calls_collected: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: calls_collected.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.upgrade()
        return calls_collected

    def test_create_table_present(self) -> None:
        """upgrade() emits a CREATE TABLE for public.memory_catalog."""
        sqls = self._collect_execute_calls()
        create_stmts = [s for s in sqls if "public.memory_catalog" in s and "CREATE" in s.upper()]
        assert create_stmts, "No CREATE TABLE public.memory_catalog found in upgrade SQL"

    def test_provenance_columns_present(self) -> None:
        """The CREATE TABLE includes source_schema, source_table, source_id."""
        sqls = self._collect_execute_calls()
        create_sql = next(s for s in sqls if "public.memory_catalog" in s and "CREATE" in s.upper())
        for col in ("source_schema", "source_table", "source_id"):
            assert col in create_sql, f"Column {col!r} missing from memory_catalog DDL"

    def test_required_columns_present(self) -> None:
        """The CREATE TABLE includes all required columns."""
        sqls = self._collect_execute_calls()
        create_sql = next(s for s in sqls if "public.memory_catalog" in s and "CREATE" in s.upper())
        required = [
            "id",
            "source_butler",
            "tenant_id",
            "entity_id",
            "summary",
            "embedding",
            "search_vector",
            "memory_type",
            "created_at",
            "updated_at",
        ]
        for col in required:
            assert col in create_sql, f"Column {col!r} missing from memory_catalog DDL"

    def test_spec_enrichment_columns_present(self) -> None:
        """Spec-required enrichment columns from core_024 are present."""
        sqls = self._collect_execute_calls()
        create_sql = next(s for s in sqls if "public.memory_catalog" in s and "CREATE" in s.upper())
        enrichment_cols = [
            "title",
            "predicate",
            "scope",
            "valid_at",
            "invalid_at",
            "confidence",
            "importance",
            "retention_class",
            "sensitivity",
            "object_entity_id",
        ]
        for col in enrichment_cols:
            assert col in create_sql, f"Enrichment column {col!r} missing from memory_catalog DDL"

    def test_unique_constraint_on_source_provenance(self) -> None:
        """CREATE TABLE declares UNIQUE on (source_schema, source_table, source_id)."""
        sqls = self._collect_execute_calls()
        create_sql = next(s for s in sqls if "public.memory_catalog" in s and "CREATE" in s.upper())
        assert "UNIQUE" in create_sql.upper(), "No UNIQUE constraint in memory_catalog DDL"
        assert "source_schema" in create_sql and "source_table" in create_sql

    def test_embedding_ivfflat_index_present(self) -> None:
        """upgrade() creates an IVFFlat index on the embedding column."""
        sqls = self._collect_execute_calls()
        ivfflat_sqls = [s for s in sqls if "ivfflat" in s.lower() or "IVFFLAT" in s]
        assert ivfflat_sqls, "No IVFFlat index creation found in upgrade SQL"

    def test_gin_search_vector_index_present(self) -> None:
        """upgrade() creates a GIN index on search_vector."""
        sqls = self._collect_execute_calls()
        gin_sqls = [s for s in sqls if "idx_memory_catalog_search_vector" in s]
        assert gin_sqls, "No GIN search_vector index found in upgrade SQL"

    def test_tenant_schema_index_present(self) -> None:
        """upgrade() creates a B-tree index on (tenant_id, source_schema)."""
        sqls = self._collect_execute_calls()
        idx_sqls = [s for s in sqls if "idx_memory_catalog_tenant_schema" in s]
        assert idx_sqls, "No tenant+schema composite index found in upgrade SQL"


class TestDowngradeSQLShape:
    """Verify the SQL emitted by downgrade() correctly reverses the upgrade."""

    def _collect_execute_calls(self) -> list[str]:
        """Run downgrade() with op.execute mocked; return SQL strings."""
        mod = _load_migration()
        calls_collected: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: calls_collected.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.downgrade()
        return calls_collected

    def test_drop_table_present(self) -> None:
        """downgrade() emits a DROP TABLE for public.memory_catalog."""
        sqls = self._collect_execute_calls()
        drop_stmts = [s for s in sqls if "memory_catalog" in s and "DROP TABLE" in s.upper()]
        assert drop_stmts, "No DROP TABLE memory_catalog found in downgrade SQL"

    def test_drop_indexes_before_table(self) -> None:
        """downgrade() drops indexes before dropping the table."""
        sqls = self._collect_execute_calls()
        index_drops = [s for s in sqls if "DROP INDEX" in s.upper()]
        table_drops = [s for s in sqls if "DROP TABLE" in s.upper()]
        assert index_drops, "No DROP INDEX statements found in downgrade SQL"
        # Index drops should appear before the table drop in the list.
        last_index_drop_pos = max(sqls.index(s) for s in index_drops)
        first_table_drop_pos = min(sqls.index(s) for s in table_drops)
        assert last_index_drop_pos < first_table_drop_pos, (
            "Index drops must appear before DROP TABLE in downgrade()"
        )


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

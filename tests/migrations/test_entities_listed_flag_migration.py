"""Tests for core_103 entities.listed flag Alembic migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. upgrade() SQL shape: ADD COLUMN with correct type/default, partial index.
3. downgrade() SQL shape: DROP COLUMN, DROP INDEX.
4. Integration: column exists with correct default after upgrade; gone after downgrade.
5. Default value: new entities get listed=true without explicit INSERT.
6. Downgrade is reversible (re-upgrade succeeds after downgrade).

Decision: Option A — docs/archive/decisions/2026-05-19-contacts-listed-flag-migration.md (PR #1794)
Bead: bu-69zp9 (discovered-from bu-gpc2u)
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_103_entities_listed_flag.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("core_103", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_upgrade_sqls() -> list[str]:
    """Run upgrade() with op mocked; return SQL strings."""
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    return sqls


def _collect_downgrade_sqls() -> list[str]:
    """Run downgrade() with op mocked; return SQL strings."""
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.downgrade()
    return sqls


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


def test_revision_chain():
    """core_103 -> core_102 (channel_defaults), no branch/depends."""
    mod = _load_migration()
    assert mod.revision == "core_103"
    assert mod.down_revision == "core_102"
    assert mod.branch_labels is None
    assert mod.depends_on is None


class TestUpgradeSQLShape:
    """Verify upgrade() emits the expected SQL."""

    def test_adds_listed_column_to_public_entities(self):
        sqls = _collect_upgrade_sqls()
        alter_stmts = [s for s in sqls if "ALTER TABLE" in s.upper() and "entities" in s.lower()]
        assert alter_stmts, "upgrade() must emit an ALTER TABLE on public.entities"
        stmt = alter_stmts[0]
        assert "public.entities" in stmt
        assert "listed" in stmt.lower()

    def test_column_is_boolean_not_null_default_true(self):
        sqls = _collect_upgrade_sqls()
        alter_stmts = [s for s in sqls if "ALTER TABLE" in s.upper() and "entities" in s.lower()]
        assert alter_stmts
        stmt = alter_stmts[0]
        assert "BOOLEAN" in stmt.upper()
        assert "NOT NULL" in stmt.upper()
        assert "DEFAULT true" in stmt.lower() or "DEFAULT TRUE" in stmt.upper()

    def test_add_column_is_idempotent(self):
        sqls = _collect_upgrade_sqls()
        alter_stmts = [s for s in sqls if "ALTER TABLE" in s.upper() and "entities" in s.lower()]
        assert alter_stmts
        stmt = alter_stmts[0]
        assert "IF NOT EXISTS" in stmt.upper()

    def test_creates_partial_index_on_listed(self):
        sqls = _collect_upgrade_sqls()
        idx_stmts = [s for s in sqls if "CREATE INDEX" in s.upper()]
        assert idx_stmts, "upgrade() must emit at least one CREATE INDEX"
        assert any("listed" in s.lower() for s in idx_stmts), "Missing index on 'listed' column"

    def test_partial_index_name_is_ix_entities_listed_active(self):
        sqls = _collect_upgrade_sqls()
        idx_stmts = [s for s in sqls if "CREATE INDEX" in s.upper()]
        assert any("ix_entities_listed_active" in s for s in idx_stmts), (
            "Expected index named 'ix_entities_listed_active'"
        )

    def test_partial_index_targets_public_entities(self):
        sqls = _collect_upgrade_sqls()
        idx_stmts = [s for s in sqls if "CREATE INDEX" in s.upper() and "listed" in s.lower()]
        assert idx_stmts
        assert any("public.entities" in s for s in idx_stmts)

    def test_partial_index_where_clause_filters_true(self):
        sqls = _collect_upgrade_sqls()
        idx_stmts = [s for s in sqls if "CREATE INDEX" in s.upper() and "listed" in s.lower()]
        assert idx_stmts
        stmt = idx_stmts[0]
        # WHERE listed = true is the partial predicate
        assert "WHERE" in stmt.upper()
        assert "listed" in stmt.lower()
        assert "true" in stmt.lower()

    def test_partial_index_is_idempotent(self):
        sqls = _collect_upgrade_sqls()
        idx_stmts = [s for s in sqls if "CREATE INDEX" in s.upper() and "listed" in s.lower()]
        assert idx_stmts
        assert any("IF NOT EXISTS" in s.upper() for s in idx_stmts)


class TestDowngradeSQLShape:
    """Verify downgrade() emits correct DROP statements."""

    def test_downgrade_drops_listed_column(self):
        sqls = _collect_downgrade_sqls()
        drop_col_stmts = [s for s in sqls if "DROP COLUMN" in s.upper() and "listed" in s.lower()]
        assert drop_col_stmts, "downgrade() must emit DROP COLUMN for 'listed'"

    def test_downgrade_drop_column_targets_entities(self):
        sqls = _collect_downgrade_sqls()
        drop_stmts = [s for s in sqls if "DROP COLUMN" in s.upper() and "listed" in s.lower()]
        assert drop_stmts
        assert any("entities" in s.lower() for s in drop_stmts)

    def test_downgrade_drop_column_is_safe(self):
        sqls = _collect_downgrade_sqls()
        drop_stmts = [s for s in sqls if "DROP COLUMN" in s.upper() and "listed" in s.lower()]
        assert drop_stmts
        assert any("IF EXISTS" in s.upper() for s in drop_stmts)

    def test_downgrade_drops_partial_index(self):
        sqls = _collect_downgrade_sqls()
        drop_idx_stmts = [
            s for s in sqls if "DROP INDEX" in s.upper() and "ix_entities_listed_active" in s
        ]
        assert drop_idx_stmts, "downgrade() must drop ix_entities_listed_active"

    def test_downgrade_drops_index_before_column(self):
        """Index must be dropped before the column it depends on."""
        sqls = _collect_downgrade_sqls()
        idx_drop_pos = next(
            (i for i, s in enumerate(sqls) if "DROP INDEX" in s.upper() and "listed" in s.lower()),
            None,
        )
        col_drop_pos = next(
            (i for i, s in enumerate(sqls) if "DROP COLUMN" in s.upper() and "listed" in s.lower()),
            None,
        )
        assert idx_drop_pos is not None, "downgrade() must drop the index"
        assert col_drop_pos is not None, "downgrade() must drop the column"
        assert idx_drop_pos < col_drop_pos, "Index must be dropped before the column in downgrade()"


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


async def _run_sqls(pool, sqls: list[str]) -> None:
    """Execute SQL strings against the pool; skip idempotent re-runs."""
    import asyncpg

    for sql in sqls:
        try:
            await pool.execute(sql)
        except (asyncpg.DuplicateObjectError, asyncpg.DuplicateTableError):
            pass  # idempotent


async def _provision_entities_table(pool) -> None:
    """Create public.entities with the pre-103 schema (no listed column)."""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.entities (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical_name  TEXT        NOT NULL,
            entity_type     TEXT        NOT NULL DEFAULT 'person',
            aliases         TEXT[]      NOT NULL DEFAULT '{}',
            metadata        JSONB       DEFAULT '{}'::jsonb,
            roles           TEXT[]      NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


async def _run_upgrade(pool) -> None:
    sqls = _collect_upgrade_sqls()
    await _run_sqls(pool, sqls)


async def _run_downgrade(pool) -> None:
    sqls = _collect_downgrade_sqls()
    for sql in sqls:
        await pool.execute(sql)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_column_exists_after_upgrade(provisioned_postgres_pool) -> None:
    """public.entities.listed exists with correct type/default after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_entities_table(pool)
        await _run_upgrade(pool)

        row = await pool.fetchrow(
            """
            SELECT column_name, data_type, column_default, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'entities'
              AND column_name  = 'listed'
            """
        )
        assert row is not None, "Column 'listed' must exist on public.entities after upgrade"
        assert row["data_type"] == "boolean", f"Expected boolean, got {row['data_type']}"
        assert row["is_nullable"] == "NO", "Column must be NOT NULL"
        assert row["column_default"] is not None, "Column must have a server default"
        assert "true" in row["column_default"].lower(), (
            f"Default must be 'true', got {row['column_default']}"
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_new_entity_gets_default_true(provisioned_postgres_pool) -> None:
    """Inserting an entity without specifying 'listed' yields listed=true."""
    async with provisioned_postgres_pool() as pool:
        await _provision_entities_table(pool)
        await _run_upgrade(pool)

        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Alice') RETURNING id"
        )
        listed = await pool.fetchval("SELECT listed FROM public.entities WHERE id = $1", entity_id)
        assert listed is True, f"Expected listed=True for new entity, got {listed}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_existing_rows_backfilled_to_true(provisioned_postgres_pool) -> None:
    """Pre-existing entities (inserted before column added) get listed=true via server default."""
    async with provisioned_postgres_pool() as pool:
        await _provision_entities_table(pool)

        # Insert a row BEFORE the migration
        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Bob') RETURNING id"
        )

        await _run_upgrade(pool)

        listed = await pool.fetchval("SELECT listed FROM public.entities WHERE id = $1", entity_id)
        assert listed is True, (
            f"Pre-existing entity must have listed=true after migration, got {listed}"
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_can_set_listed_false(provisioned_postgres_pool) -> None:
    """listed can be set to false (archive semantics)."""
    async with provisioned_postgres_pool() as pool:
        await _provision_entities_table(pool)
        await _run_upgrade(pool)

        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, listed) VALUES ('Carol', false) RETURNING id"
        )
        listed = await pool.fetchval("SELECT listed FROM public.entities WHERE id = $1", entity_id)
        assert listed is False, f"Expected listed=False for archived entity, got {listed}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_partial_index_exists_after_upgrade(provisioned_postgres_pool) -> None:
    """ix_entities_listed_active partial index exists after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_entities_table(pool)
        await _run_upgrade(pool)

        row = await pool.fetchrow(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename  = 'entities'
              AND indexname  = 'ix_entities_listed_active'
            """
        )
        assert row is not None, "ix_entities_listed_active must exist after upgrade"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_column_absent_after_downgrade(provisioned_postgres_pool) -> None:
    """public.entities.listed is absent after downgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_entities_table(pool)
        await _run_upgrade(pool)
        await _run_downgrade(pool)

        row = await pool.fetchrow(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'entities'
              AND column_name  = 'listed'
            """
        )
        assert row is None, "Column 'listed' must be absent after downgrade"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_index_absent_after_downgrade(provisioned_postgres_pool) -> None:
    """ix_entities_listed_active is absent after downgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_entities_table(pool)
        await _run_upgrade(pool)
        await _run_downgrade(pool)

        row = await pool.fetchrow(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename  = 'entities'
              AND indexname  = 'ix_entities_listed_active'
            """
        )
        assert row is None, "ix_entities_listed_active must be absent after downgrade"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_is_reversible(provisioned_postgres_pool) -> None:
    """Upgrade → downgrade → upgrade again succeeds without error."""
    async with provisioned_postgres_pool() as pool:
        await _provision_entities_table(pool)

        await _run_upgrade(pool)
        await _run_downgrade(pool)
        await _run_upgrade(pool)  # must not raise

        row = await pool.fetchrow(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'entities'
              AND column_name  = 'listed'
            """
        )
        assert row is not None, "Column 'listed' must exist after second upgrade"

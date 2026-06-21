"""Tests for rel_022 prefers-channel predicate seed migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. upgrade() SQL shape: seeds prefers-channel with kind='override',
   object_kind='literal', and an explicit cardinality='single' UPDATE.
3. downgrade() SQL shape: deletes only the prefers-channel row; does NOT drop
   the table, the cardinality column, or the schema.
4. Integration: row present with correct kind/object_kind/cardinality after
   upgrade; idempotent re-run; absent after downgrade; sibling rows survive.

Issue: bu-ctsgh
Parent epic: bu-sbdwt — entity-keyed preferred_channel
Spec: openspec/changes/entity-keyed-preferred-channel/specs/relationship-facts/spec.md
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "022_prefers_channel_predicate.py"
)
_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "014_predicate_registry.py"
)


def _load_migration(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_sqls(fn_name: str) -> list[str]:
    mod = _load_migration(_MIGRATION_PATH, "rel_022")
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    return sqls


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


class TestMigrationFileAndChain:
    def test_revision_chain(self) -> None:
        """rel_022 -> rel_021, no branch/depends."""
        mod = _load_migration(_MIGRATION_PATH, "rel_022")
        assert mod.revision == "rel_022"
        assert mod.down_revision == "rel_021"
        assert mod.branch_labels is None
        assert mod.depends_on is None


class TestDowngradeSQLShape:
    def test_deletes_only_prefers_channel_row(self) -> None:
        sqls = _collect_sqls("downgrade")
        deletes = [s for s in sqls if "DELETE FROM" in s.upper()]
        assert deletes, "downgrade() must DELETE the prefers-channel row"
        assert all("prefers-channel" in s for s in deletes)

    def test_does_not_drop_table_column_or_schema(self) -> None:
        sqls = _collect_sqls("downgrade")
        joined = " ".join(sqls).upper()
        assert "DROP TABLE" not in joined
        assert "DROP COLUMN" not in joined
        assert "DROP SCHEMA" not in joined


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


async def _provision_registry(pool) -> None:
    """Create the registry table (with cardinality) and seed rel_014 rows."""
    await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
            predicate   TEXT        NOT NULL PRIMARY KEY,
            kind        TEXT        NOT NULL CHECK (kind IN ('contact', 'relational', 'override')),
            object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
            description TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            cardinality TEXT        NOT NULL DEFAULT 'multi'
                            CHECK (cardinality IN ('single', 'multi'))
        )
        """
    )
    # Seed a couple of sibling rows to prove downgrade leaves them intact.
    await pool.execute(
        """
        INSERT INTO relationship.entity_predicate_registry
            (predicate, kind, object_kind, description, cardinality)
        VALUES
            ('has-email', 'contact', 'literal', 'Email.', 'multi'),
            ('has-birthday', 'contact', 'literal', 'DOB.', 'single')
        ON CONFLICT (predicate) DO NOTHING
        """
    )


def _run(pool, sqls: list[str]):
    async def _inner():
        for sql in sqls:
            await pool.execute(sql)

    return _inner()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_seed_row_present_after_upgrade(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await _run(pool, _collect_sqls("upgrade"))

        row = await pool.fetchrow(
            """
            SELECT kind, object_kind, cardinality
            FROM relationship.entity_predicate_registry
            WHERE predicate = 'prefers-channel'
            """
        )
        assert row is not None, "prefers-channel must be seeded after upgrade"
        assert row["kind"] == "override"
        assert row["object_kind"] == "literal"
        assert row["cardinality"] == "single"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_is_idempotent(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await _run(pool, _collect_sqls("upgrade"))
        await _run(pool, _collect_sqls("upgrade"))

        count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_predicate_registry "
            "WHERE predicate = 'prefers-channel'"
        )
        assert count == 1, "Re-running upgrade must not duplicate the prefers-channel row"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_fixes_cardinality_on_preexisting_row(provisioned_postgres_pool) -> None:
    """If the row pre-exists at cardinality='multi', upgrade() corrects it to 'single'."""
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await pool.execute(
            """
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, description, cardinality)
            VALUES ('prefers-channel', 'override', 'literal', 'stale', 'multi')
            """
        )
        await _run(pool, _collect_sqls("upgrade"))

        cardinality = await pool.fetchval(
            "SELECT cardinality FROM relationship.entity_predicate_registry "
            "WHERE predicate = 'prefers-channel'"
        )
        assert cardinality == "single"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_removes_only_prefers_channel(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await _run(pool, _collect_sqls("upgrade"))
        await _run(pool, _collect_sqls("downgrade"))

        gone = await pool.fetchval(
            "SELECT 1 FROM relationship.entity_predicate_registry "
            "WHERE predicate = 'prefers-channel'"
        )
        assert gone is None, "prefers-channel must be absent after downgrade"

        # Sibling rows survive, and the table itself survives.
        survivors = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_predicate_registry "
            "WHERE predicate IN ('has-email', 'has-birthday')"
        )
        assert survivors == 2, "Downgrade must not touch sibling registry rows"

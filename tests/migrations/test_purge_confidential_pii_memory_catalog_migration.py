"""Tests for core_183 purge of confidential/pii public.memory_catalog rows.

Owner ruling (bu-6gsmh): EXCLUDE. This migration is the one-off backfill
purge companion to the write-time exclusion guard added to
``butlers/modules/memory/storage.py`` (see
``tests/modules/memory/test_catalog_write_time_sensitivity.py`` and
``tests/modules/memory/test_memory_migration_integration.py``).

The purge has two passes:

1. Rows whose recorded ``sensitivity`` column is already ``'pii'`` or
   ``'confidential'``.
2. Rows whose recorded ``sensitivity`` is NULL but whose canonical source
   fact/rule (in the owning butler's schema, resolved via the catalog row's
   own ``source_schema``/``source_table``/``source_id`` provenance) is
   genuinely ``'pii'``/``'confidential'``. This second pass exists because a
   real bug (also fixed by bu-6gsmh, see storage.py's ``store_fact``/
   ``store_rule`` write-behind calls) meant every catalog row written via the
   live path landed with ``sensitivity IS NULL`` regardless of the source's
   true classification -- pass 1 alone would silently miss those rows.

Covers:
1. Migration file structure and revision chain (unit -- no DB required).
2. Upgrade SQL shape: to_regclass guards, both DELETE passes present.
3. Integration: no-op when the table does not exist.
4. Integration: pass 1 (correctly-labeled rows) and pass 2 (join-back for
   NULL-sensitivity rows) both work, normal/unprovable rows survive, and a
   second run is idempotent.

Integration tests (marked pytest.mark.integration) require Docker + Postgres
provisioned via the shared ``provisioned_postgres_pool`` fixture.
"""

from __future__ import annotations

import importlib.util
import uuid
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
    / "core_183_purge_confidential_pii_memory_catalog.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("core_183", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Unit tests -- no DB required
# ---------------------------------------------------------------------------


class TestMigrationFileAndChain:
    """Revision-chain contract test."""

    def test_revision_chain(self) -> None:
        """core_183 -> core_182, no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "core_183"
        assert mod.down_revision == "core_182"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_chain_head_resolves_to_core_183(self) -> None:
        """The real migration-chain scan (used by the migration-drift sentinel
        and CI's migration-integrity gate) must see core_183 as the current
        head -- proves this migration was chained onto the true head rather
        than a stale/guessed revision number."""
        from butlers.migrations import get_chain_head

        assert get_chain_head("core") == "core_183"


class TestUpgradeSQLShape:
    def _collect_execute_calls(self) -> list[str]:
        mod = _load_migration()
        calls_collected: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: calls_collected.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.upgrade()
        return calls_collected

    def test_guarded_with_to_regclass(self) -> None:
        """upgrade() must no-op safely when public.memory_catalog is absent,
        and must guard each per-schema source table before touching it."""
        sqls = self._collect_execute_calls()
        joined = "\n".join(sqls)
        assert "to_regclass('public.memory_catalog')" in joined
        assert "to_regclass(format('%I.facts'" in joined
        assert "to_regclass(format('%I.rules'" in joined

    def test_pass_one_deletes_exactly_pii_and_confidential(self) -> None:
        sqls = self._collect_execute_calls()
        joined = "\n".join(sqls)
        assert "DELETE FROM public.memory_catalog" in joined
        # The DELETE's WHERE targets exactly {'pii', 'confidential'} -- 'normal'
        # only appears as the COALESCE fallback for NULL sensitivity, never as
        # a value being deleted.
        assert "IN ('pii', 'confidential')" in joined

    def test_pass_two_joins_back_to_source_schema_for_null_rows(self) -> None:
        """The NULL-sensitivity recovery pass must iterate discovered schemas
        and join back to each owning butler's facts/rules table."""
        sqls = self._collect_execute_calls()
        joined = "\n".join(sqls)
        assert "sensitivity IS NULL" in joined
        assert "SELECT DISTINCT source_schema" in joined
        assert "FOR catalog_schema IN" in joined
        # Dynamic per-schema DELETE using EXISTS against the real source table.
        assert "EXECUTE format(" in joined
        assert "%I.facts f" in joined
        assert "%I.rules ru" in joined

    def test_downgrade_does_not_resurrect_rows(self) -> None:
        """downgrade() is intentionally a no-op (purged rows are not recreated)."""
        mod = _load_migration()
        mock_op = MagicMock()
        with patch.object(mod, "op", mock_op):
            mod.downgrade()
        mock_op.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Integration tests -- require Docker + Postgres
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPurgeMigrationIntegration:
    @pytest.fixture
    async def bare_pool(self, provisioned_postgres_pool):
        """A fresh DB with no public.memory_catalog table at all."""
        async with provisioned_postgres_pool() as pool:
            yield pool

    @pytest.fixture
    async def catalog_pool(self, provisioned_postgres_pool):
        """A fresh DB with a minimal public.memory_catalog table (mirrors core_009)
        plus a "health" butler schema with minimal facts/rules tables, so the
        join-back pass has a real source to resolve against."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute(
                """
                CREATE TABLE IF NOT EXISTS public.memory_catalog (
                    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id     TEXT NOT NULL DEFAULT 'shared',
                    source_schema TEXT NOT NULL,
                    source_table  TEXT NOT NULL,
                    source_id     UUID NOT NULL,
                    source_butler TEXT NOT NULL DEFAULT 'memory',
                    memory_type   TEXT NOT NULL,
                    summary       TEXT,
                    sensitivity   TEXT,
                    invalid_at    TIMESTAMPTZ
                )
                """
            )
            await pool.execute("CREATE SCHEMA IF NOT EXISTS health")
            await pool.execute(
                """
                CREATE TABLE IF NOT EXISTS health.facts (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    sensitivity TEXT
                )
                """
            )
            await pool.execute(
                """
                CREATE TABLE IF NOT EXISTS health.rules (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    sensitivity TEXT
                )
                """
            )
            yield pool

    async def _run_upgrade(self, pool) -> None:
        mod = _load_migration()
        sqls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.upgrade()
        for sql in sqls:
            await pool.execute(sql)

    async def _insert_catalog_row(
        self, pool, *, source_table: str, source_id: uuid.UUID, sensitivity: str | None
    ) -> uuid.UUID:
        await pool.execute(
            """
            INSERT INTO public.memory_catalog
                (source_schema, source_table, source_id, memory_type, summary, sensitivity)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            "health",
            source_table,
            source_id,
            "fact" if source_table == "facts" else "rule",
            "some summary",
            sensitivity,
        )
        return source_id

    async def _insert_source_fact(self, pool, *, sensitivity: str | None) -> uuid.UUID:
        row = await pool.fetchrow(
            "INSERT INTO health.facts (sensitivity) VALUES ($1) RETURNING id", sensitivity
        )
        return row["id"]

    async def _insert_source_rule(self, pool, *, sensitivity: str | None) -> uuid.UUID:
        row = await pool.fetchrow(
            "INSERT INTO health.rules (sensitivity) VALUES ($1) RETURNING id", sensitivity
        )
        return row["id"]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_noop_when_table_absent(self, bare_pool) -> None:
        """upgrade() must not raise when public.memory_catalog does not exist."""
        await self._run_upgrade(bare_pool)  # must not raise

    @pytest.mark.asyncio(loop_scope="session")
    async def test_pass_one_deletes_correctly_labeled_pii_and_confidential(
        self, catalog_pool
    ) -> None:
        """Rows whose own sensitivity column already says pii/confidential are
        deleted regardless of what the source table says."""
        pool = catalog_pool
        normal_id = await self._insert_catalog_row(
            pool, source_table="facts", source_id=uuid.uuid4(), sensitivity="normal"
        )
        pii_id = await self._insert_catalog_row(
            pool, source_table="facts", source_id=uuid.uuid4(), sensitivity="pii"
        )
        confidential_id = await self._insert_catalog_row(
            pool, source_table="facts", source_id=uuid.uuid4(), sensitivity="confidential"
        )

        await self._run_upgrade(pool)

        remaining_ids = {
            r["source_id"] for r in await pool.fetch("SELECT source_id FROM public.memory_catalog")
        }
        assert remaining_ids == {normal_id}
        assert pii_id not in remaining_ids
        assert confidential_id not in remaining_ids

    @pytest.mark.asyncio(loop_scope="session")
    async def test_pass_two_recovers_null_sensitivity_row_with_confidential_source(
        self, catalog_pool
    ) -> None:
        """A catalog row with NULL sensitivity, but whose source fact is truly
        confidential, must be deleted -- this is the join-back recovery the
        review found missing (COALESCE(NULL, 'normal') alone would miss it)."""
        pool = catalog_pool
        confidential_fact_id = await self._insert_source_fact(pool, sensitivity="confidential")
        await self._insert_catalog_row(
            pool, source_table="facts", source_id=confidential_fact_id, sensitivity=None
        )

        pii_fact_id = await self._insert_source_fact(pool, sensitivity="pii")
        await self._insert_catalog_row(
            pool, source_table="facts", source_id=pii_fact_id, sensitivity=None
        )

        await self._run_upgrade(pool)

        remaining = await pool.fetch("SELECT source_id FROM public.memory_catalog")
        remaining_ids = {r["source_id"] for r in remaining}
        assert confidential_fact_id not in remaining_ids
        assert pii_fact_id not in remaining_ids

    @pytest.mark.asyncio(loop_scope="session")
    async def test_pass_two_recovers_null_sensitivity_rule_with_pii_source(
        self, catalog_pool
    ) -> None:
        """Same recovery, rules counterpart (store_rule had the identical bug)."""
        pool = catalog_pool
        pii_rule_id = await self._insert_source_rule(pool, sensitivity="pii")
        await self._insert_catalog_row(
            pool, source_table="rules", source_id=pii_rule_id, sensitivity=None
        )

        await self._run_upgrade(pool)

        remaining_ids = {
            r["source_id"] for r in await pool.fetch("SELECT source_id FROM public.memory_catalog")
        }
        assert pii_rule_id not in remaining_ids

    @pytest.mark.asyncio(loop_scope="session")
    async def test_null_sensitivity_row_with_normal_source_survives(self, catalog_pool) -> None:
        """A NULL-sensitivity catalog row backed by a genuinely normal source
        fact must NOT be deleted -- the join-back only recovers true
        pii/confidential sources, it is not a blanket NULL purge."""
        pool = catalog_pool
        normal_fact_id = await self._insert_source_fact(pool, sensitivity="normal")
        await self._insert_catalog_row(
            pool, source_table="facts", source_id=normal_fact_id, sensitivity=None
        )

        await self._run_upgrade(pool)

        remaining_ids = {
            r["source_id"] for r in await pool.fetch("SELECT source_id FROM public.memory_catalog")
        }
        assert normal_fact_id in remaining_ids

    @pytest.mark.asyncio(loop_scope="session")
    async def test_null_sensitivity_orphaned_row_with_no_source_survives(
        self, catalog_pool
    ) -> None:
        """A NULL-sensitivity catalog row whose source_id has no matching row
        in health.facts at all (e.g. the source was since hard-deleted) is
        left alone -- there is nothing to prove it was confidential, and
        COALESCE(NULL, 'normal') matches the same fail-open convention the
        read-time ceiling already applies to unrecoverable NULLs."""
        pool = catalog_pool
        orphaned_id = await self._insert_catalog_row(
            pool, source_table="facts", source_id=uuid.uuid4(), sensitivity=None
        )

        await self._run_upgrade(pool)

        remaining_ids = {
            r["source_id"] for r in await pool.fetch("SELECT source_id FROM public.memory_catalog")
        }
        assert orphaned_id in remaining_ids

    @pytest.mark.asyncio(loop_scope="session")
    async def test_idempotent_second_run_deletes_nothing_further(self, catalog_pool) -> None:
        pool = catalog_pool
        await self._insert_catalog_row(
            pool, source_table="facts", source_id=uuid.uuid4(), sensitivity="normal"
        )
        await self._insert_catalog_row(
            pool, source_table="facts", source_id=uuid.uuid4(), sensitivity="confidential"
        )
        confidential_fact_id = await self._insert_source_fact(pool, sensitivity="confidential")
        await self._insert_catalog_row(
            pool, source_table="facts", source_id=confidential_fact_id, sensitivity=None
        )

        await self._run_upgrade(pool)
        count_after_first = await pool.fetchval("SELECT COUNT(*) FROM public.memory_catalog")

        await self._run_upgrade(pool)
        count_after_second = await pool.fetchval("SELECT COUNT(*) FROM public.memory_catalog")

        assert count_after_first == 1
        assert count_after_second == count_after_first

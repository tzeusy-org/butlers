"""Tests for core_234 reclassification of health condition/symptom/medication facts.

Background (bu-2jtfw.3): the health butler's condition_add/symptom_log/
medication_add/medication_log_dose write paths never passed a
``sensitivity`` argument to ``store_fact``, so every one of these facts
landed at the ``'normal'`` default -- NOT excluded from the fleet-shared
``public.memory_catalog`` write-behind. Live symptom: an aortic-valve
regurgitation condition fact was catalog-searchable by any caller at the
default read ceiling.

The write paths are fixed going forward (see
``tests/tools/test_health_condition_sensitivity.py``). This migration is the
backfill: reclassify already-written canonical facts as ``confidential`` and
purge any already-cataloged rows sourced from them.

Covers:
1. Migration file structure and revision chain (unit -- no DB required).
2. Upgrade SQL shape: to_regclass guards, both passes present.
3. Integration: no-op when no facts/catalog tables exist.
4. Integration: canonical reclassification + catalog purge, idempotent, and
   scoped only to the predicates that classify as diagnosis/treatment data
   (a measurement_weight fact/catalog row survives untouched).
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_234_reclassify_health_write_sensitivity.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_234", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Unit tests -- no DB required
# ---------------------------------------------------------------------------


class TestMigrationFileAndChain:
    def test_chain_head_is_single_and_contains_this_migration(self) -> None:
        from butlers.migrations import get_chain_head, get_chain_revision_ids

        revision_ids = get_chain_revision_ids("core")
        assert "core_234" in revision_ids
        assert get_chain_head("core") in revision_ids


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
        sqls = self._collect_execute_calls()
        joined = "\n".join(sqls)
        assert "to_regclass(format('%I.facts'" in joined
        assert "to_regclass('public.memory_catalog')" in joined

    def test_reclassification_targets_diagnosis_treatment_predicates_only(self) -> None:
        sqls = self._collect_execute_calls()
        joined = "\n".join(sqls)
        assert "SET sensitivity = ''confidential''" in joined
        for predicate in ("condition", "symptom", "medication", "took_dose"):
            assert f"''{predicate}''" in joined
        # measurement predicates must never appear in the reclassification set
        assert "measurement" not in joined

    def test_downgrade_does_not_undo_reclassification(self) -> None:
        mod = _load_migration()
        mock_op = MagicMock()
        with patch.object(mod, "op", mock_op):
            mod.downgrade()
        mock_op.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Integration tests -- require Docker + Postgres
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReclassifyMigrationIntegration:
    @pytest.fixture
    async def bare_pool(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as pool:
            yield pool

    @pytest.fixture
    async def health_pool(self, provisioned_postgres_pool):
        """A fresh DB with a "health" schema (minimal facts table) plus
        public.memory_catalog, mirroring the real memory-module schema shape
        just enough for this migration's two passes."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute("CREATE SCHEMA IF NOT EXISTS health")
            await pool.execute(
                """
                CREATE TABLE IF NOT EXISTS health.facts (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    scope       TEXT NOT NULL DEFAULT 'global',
                    predicate   TEXT NOT NULL,
                    sensitivity TEXT
                )
                """
            )
            await pool.execute(
                """
                CREATE TABLE IF NOT EXISTS public.memory_catalog (
                    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id     TEXT NOT NULL DEFAULT 'shared',
                    source_schema TEXT NOT NULL,
                    source_table  TEXT NOT NULL,
                    source_id     UUID NOT NULL,
                    source_butler TEXT NOT NULL DEFAULT 'health',
                    memory_type   TEXT NOT NULL DEFAULT 'fact',
                    summary       TEXT,
                    sensitivity   TEXT,
                    invalid_at    TIMESTAMPTZ
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

    async def _insert_fact(self, pool, *, predicate: str, sensitivity: str | None) -> uuid.UUID:
        row = await pool.fetchrow(
            "INSERT INTO health.facts (scope, predicate, sensitivity)"
            " VALUES ('health', $1, $2) RETURNING id",
            predicate,
            sensitivity,
        )
        return row["id"]

    async def _insert_catalog_row(
        self, pool, *, source_id: uuid.UUID, sensitivity: str | None
    ) -> None:
        await pool.execute(
            "INSERT INTO public.memory_catalog"
            " (source_schema, source_table, source_id, summary, sensitivity)"
            " VALUES ('health', 'facts', $1, 'some summary', $2)",
            source_id,
            sensitivity,
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_noop_when_no_facts_or_catalog_tables(self, bare_pool) -> None:
        await self._run_upgrade(bare_pool)  # must not raise

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reclassifies_condition_and_purges_catalog_row(self, health_pool) -> None:
        """Reproduction: a condition fact stuck at 'normal' is reclassified
        and its already-cataloged row is purged -- the aortic-valve scenario."""
        pool = health_pool
        condition_id = await self._insert_fact(pool, predicate="condition", sensitivity="normal")
        await self._insert_catalog_row(pool, source_id=condition_id, sensitivity="normal")

        await self._run_upgrade(pool)

        fact_row = await pool.fetchrow(
            "SELECT sensitivity FROM health.facts WHERE id = $1", condition_id
        )
        assert fact_row["sensitivity"] == "confidential"

        catalog_rows = await pool.fetch(
            "SELECT id FROM public.memory_catalog WHERE source_id = $1", condition_id
        )
        assert catalog_rows == []

    @pytest.mark.asyncio(loop_scope="session")
    async def test_symptom_and_medication_and_took_dose_reclassified(self, health_pool) -> None:
        pool = health_pool
        ids = {
            predicate: await self._insert_fact(pool, predicate=predicate, sensitivity="normal")
            for predicate in ("symptom", "medication", "took_dose")
        }

        await self._run_upgrade(pool)

        for predicate, fact_id in ids.items():
            row = await pool.fetchrow("SELECT sensitivity FROM health.facts WHERE id = $1", fact_id)
            assert row["sensitivity"] == "confidential", predicate

    @pytest.mark.asyncio(loop_scope="session")
    async def test_measurement_predicate_survives_untouched(self, health_pool) -> None:
        """measurement_weight stays at 'normal' and its catalog row survives."""
        pool = health_pool
        measurement_id = await self._insert_fact(
            pool, predicate="measurement_weight", sensitivity="normal"
        )
        await self._insert_catalog_row(pool, source_id=measurement_id, sensitivity="normal")

        await self._run_upgrade(pool)

        fact_row = await pool.fetchrow(
            "SELECT sensitivity FROM health.facts WHERE id = $1", measurement_id
        )
        assert fact_row["sensitivity"] == "normal"

        catalog_rows = await pool.fetch(
            "SELECT id FROM public.memory_catalog WHERE source_id = $1", measurement_id
        )
        assert len(catalog_rows) == 1

    @pytest.mark.asyncio(loop_scope="session")
    async def test_idempotent_second_run_changes_nothing_further(self, health_pool) -> None:
        pool = health_pool
        condition_id = await self._insert_fact(pool, predicate="condition", sensitivity="normal")
        await self._insert_catalog_row(pool, source_id=condition_id, sensitivity="normal")

        await self._run_upgrade(pool)
        await self._run_upgrade(pool)  # must not raise, no further change

        fact_row = await pool.fetchrow(
            "SELECT sensitivity FROM health.facts WHERE id = $1", condition_id
        )
        assert fact_row["sensitivity"] == "confidential"
        catalog_rows = await pool.fetch(
            "SELECT id FROM public.memory_catalog WHERE source_id = $1", condition_id
        )
        assert catalog_rows == []

    @pytest.mark.asyncio(loop_scope="session")
    async def test_already_confidential_fact_left_alone(self, health_pool) -> None:
        """A fact already correctly classified is not touched (UPDATE WHERE
        excludes it), proving the migration only fixes the under-classified."""
        pool = health_pool
        condition_id = await self._insert_fact(
            pool, predicate="condition", sensitivity="confidential"
        )

        await self._run_upgrade(pool)

        fact_row = await pool.fetchrow(
            "SELECT sensitivity FROM health.facts WHERE id = $1", condition_id
        )
        assert fact_row["sensitivity"] == "confidential"

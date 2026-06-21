"""Tests for mem_003 wellness predicate migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. Upgrade idempotency: running twice yields same row count, no exception.
3. Downgrade removes exactly the nine wellness predicates.
4. All nine predicate names present after upgrade.
5. Metadata fields (scope, status, is_temporal, expected_subject_type) match spec.

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
# Module loading helpers
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "butlers"
    / "modules"
    / "memory"
    / "migrations"
    / "003_wellness_predicates.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("mem_003", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


class TestMigrationFileAndChain:
    """Revision-chain + predicate-name/collision contract tests."""

    def test_revision_chain(self) -> None:
        """mem_003 -> mem_002, no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "mem_003"
        assert mod.down_revision == "mem_002"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_nine_predicate_names_defined(self) -> None:
        """WELLNESS_PREDICATE_NAMES exports exactly nine names."""
        mod = _load_migration()
        assert len(mod.WELLNESS_PREDICATE_NAMES) == 9

    def test_all_expected_names_present(self) -> None:
        """All nine canonical predicate names are in WELLNESS_PREDICATE_NAMES."""
        mod = _load_migration()
        expected = {
            "sleep_session",
            "sleep_stage_summary",
            "measurement_resting_hr",
            "measurement_hrv",
            "measurement_spo2",
            "measurement_breathing_rate",
            "measurement_steps",
            "measurement_active_minutes",
            "measurement_vo2_max",
        }
        assert set(mod.WELLNESS_PREDICATE_NAMES) == expected

    def test_no_measurement_heart_rate_collision(self) -> None:
        """measurement_resting_hr must not collide with the pre-existing measurement_heart_rate."""
        mod = _load_migration()
        assert "measurement_heart_rate" not in mod.WELLNESS_PREDICATE_NAMES
        assert "measurement_resting_hr" in mod.WELLNESS_PREDICATE_NAMES


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
#
# The upgrade/downgrade SQL shape (9 idempotent INSERTs with
# scope=health/status=active/is_temporal=true/subject=person/is_edge=false, and
# the WHERE name IN (...) DELETE) is exercised end-to-end by
# test_predicate_metadata_matches_spec + test_downgrade_removes_exactly_nine.
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWellnessPredicatesMigrationIntegration:
    """Integration tests requiring a real PostgreSQL instance.

    These tests exercise the full upgrade / downgrade round-trip against
    a real database with the predicate_registry table created inline
    (mirrors the shape from 001_memory_schema.py).
    """

    @pytest.fixture
    async def predicate_registry_pool(self, provisioned_postgres_pool):
        """Provision a fresh DB with predicate_registry and return a pool."""
        async with provisioned_postgres_pool() as pool:
            # Create the predicate_registry table mirroring 001_memory_schema.py
            await pool.execute("""
                CREATE TABLE IF NOT EXISTS predicate_registry (
                    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name           TEXT NOT NULL UNIQUE,
                    description    TEXT,
                    expected_subject_type TEXT,
                    expected_object_type  TEXT,
                    is_edge        BOOLEAN NOT NULL DEFAULT false,
                    is_temporal    BOOLEAN NOT NULL DEFAULT false,
                    scope          TEXT NOT NULL DEFAULT 'global',
                    status         TEXT NOT NULL DEFAULT 'active',
                    superseded_by  TEXT,
                    deprecated_at  TIMESTAMPTZ,
                    inverse_of     TEXT,
                    is_symmetric   BOOLEAN NOT NULL DEFAULT false,
                    example_json   JSONB,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            yield pool

    async def _run_upgrade(self, pool) -> None:
        """Execute upgrade() SQL against the pool using op.execute shim."""
        mod = _load_migration()

        class _OpShim:
            def execute(self, sql: str) -> None:
                import asyncio

                asyncio.get_event_loop().run_until_complete(pool.execute(sql))

        # Use the synchronous pool.execute via a thread-safe shim.
        # asyncpg pools are async; we execute via pool.execute in an async
        # context here, so we patch op with an async-aware shim.
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
    async def test_upgrade_inserts_nine_predicates(self, predicate_registry_pool) -> None:
        """After upgrade, all nine wellness predicates exist in predicate_registry."""
        pool = predicate_registry_pool
        mod = _load_migration()

        await self._run_upgrade(pool)

        rows = await pool.fetch(
            "SELECT name FROM predicate_registry WHERE name = ANY($1::text[])",
            mod.WELLNESS_PREDICATE_NAMES,
        )
        found = {r["name"] for r in rows}
        assert found == set(mod.WELLNESS_PREDICATE_NAMES), (
            f"Missing predicates after upgrade: {set(mod.WELLNESS_PREDICATE_NAMES) - found}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_upgrade_is_idempotent(self, predicate_registry_pool) -> None:
        """Running upgrade() twice yields the same row count and no error."""
        pool = predicate_registry_pool
        mod = _load_migration()

        await self._run_upgrade(pool)
        count_after_first = await pool.fetchval(
            "SELECT COUNT(*) FROM predicate_registry WHERE name = ANY($1::text[])",
            mod.WELLNESS_PREDICATE_NAMES,
        )
        assert count_after_first == 9

        # Second run must not raise and must not change row count
        await self._run_upgrade(pool)
        count_after_second = await pool.fetchval(
            "SELECT COUNT(*) FROM predicate_registry WHERE name = ANY($1::text[])",
            mod.WELLNESS_PREDICATE_NAMES,
        )
        assert count_after_second == 9, (
            f"Row count changed on second upgrade: {count_after_first} -> {count_after_second}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_downgrade_removes_exactly_nine_predicates(self, predicate_registry_pool) -> None:
        """downgrade() removes exactly the nine wellness predicates and nothing else."""
        pool = predicate_registry_pool
        mod = _load_migration()

        # Insert a sentinel row that must survive the downgrade
        await pool.execute("""
            INSERT INTO predicate_registry (name, scope, status, is_edge, is_temporal)
            VALUES ('sentinel_predicate_xyz', 'global', 'active', false, false)
        """)

        await self._run_upgrade(pool)
        await self._run_downgrade(pool)

        # Wellness predicates gone
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM predicate_registry WHERE name = ANY($1::text[])",
            mod.WELLNESS_PREDICATE_NAMES,
        )
        assert count == 0, f"Expected 0 wellness predicates after downgrade, found {count}"

        # Sentinel still present
        sentinel = await pool.fetchval(
            "SELECT COUNT(*) FROM predicate_registry WHERE name = 'sentinel_predicate_xyz'"
        )
        assert sentinel == 1, "Sentinel row was incorrectly removed by downgrade()"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_predicate_metadata_matches_spec(self, predicate_registry_pool) -> None:
        """Verify scope, status, is_temporal, and expected_subject_type for each predicate."""
        pool = predicate_registry_pool
        mod = _load_migration()

        await self._run_upgrade(pool)

        rows = await pool.fetch(
            """
            SELECT name, scope, status, is_temporal, expected_subject_type,
                   is_edge, expected_object_type
            FROM predicate_registry
            WHERE name = ANY($1::text[])
            """,
            mod.WELLNESS_PREDICATE_NAMES,
        )
        by_name = {r["name"]: r for r in rows}

        for name in mod.WELLNESS_PREDICATE_NAMES:
            assert name in by_name, f"Predicate {name!r} missing from DB after upgrade"
            row = by_name[name]
            assert row["scope"] == "health", (
                f"{name}: expected scope='health', got {row['scope']!r}"
            )
            assert row["status"] == "active", (
                f"{name}: expected status='active', got {row['status']!r}"
            )
            assert row["is_temporal"] is True, (
                f"{name}: expected is_temporal=True, got {row['is_temporal']!r}"
            )
            assert row["expected_subject_type"] == "person", (
                f"{name}: expected subject_type='person', got {row['expected_subject_type']!r}"
            )
            assert row["is_edge"] is False, (
                f"{name}: expected is_edge=False, got {row['is_edge']!r}"
            )
            assert row["expected_object_type"] is None, (
                f"{name}: expected object_type=None, got {row['expected_object_type']!r}"
            )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_no_measurement_heart_rate_in_upgrade(self, predicate_registry_pool) -> None:
        """upgrade() must not insert measurement_heart_rate (collision guard)."""
        pool = predicate_registry_pool

        await self._run_upgrade(pool)

        row = await pool.fetchrow(
            "SELECT name FROM predicate_registry WHERE name = 'measurement_heart_rate'"
        )
        assert row is None, (
            "measurement_heart_rate should NOT be inserted by the wellness migration; "
            "it is the pre-existing point-in-time reading predicate from mem_002"
        )

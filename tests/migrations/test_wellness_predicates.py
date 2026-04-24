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
    """File-level and revision-chain contract tests."""

    def test_migration_file_exists(self) -> None:
        """003_wellness_predicates.py exists at expected path."""
        assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"

    def test_revision_id(self) -> None:
        """Revision is mem_003."""
        mod = _load_migration()
        assert mod.revision == "mem_003"

    def test_down_revision(self) -> None:
        """down_revision points to mem_002 (the collapsed seed migration)."""
        mod = _load_migration()
        assert mod.down_revision == "mem_002"

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


class TestMigrationSQLShape:
    """Verify the SQL emitted by upgrade/downgrade matches the spec."""

    def _collect_execute_calls(self, fn_name: str) -> list[str]:
        """Run upgrade() or downgrade() with op.execute mocked; return SQL strings."""
        mod = _load_migration()
        calls_collected: list[str] = []

        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: calls_collected.append(sql)

        with patch.object(mod, "op", mock_op):
            getattr(mod, fn_name)()

        return calls_collected

    def test_upgrade_emits_nine_inserts(self) -> None:
        """upgrade() emits exactly 9 INSERT statements."""
        sqls = self._collect_execute_calls("upgrade")
        inserts = [s for s in sqls if s.strip().upper().startswith("INSERT")]
        assert len(inserts) == 9, f"Expected 9 INSERTs, got {len(inserts)}"

    def test_upgrade_uses_on_conflict_do_nothing(self) -> None:
        """All INSERT statements use ON CONFLICT (name) DO NOTHING for idempotency."""
        sqls = self._collect_execute_calls("upgrade")
        for sql in sqls:
            upper = sql.upper()
            assert "ON CONFLICT" in upper and "DO NOTHING" in upper, (
                f"INSERT missing ON CONFLICT DO NOTHING:\n{sql}"
            )

    def test_upgrade_all_predicates_scope_health(self) -> None:
        """Every INSERT sets scope = 'health'."""
        sqls = self._collect_execute_calls("upgrade")
        for sql in sqls:
            assert "'health'" in sql, f"Missing scope=health in:\n{sql}"

    def test_upgrade_all_predicates_status_active(self) -> None:
        """Every INSERT sets status = 'active'."""
        sqls = self._collect_execute_calls("upgrade")
        for sql in sqls:
            assert "'active'" in sql, f"Missing status=active in:\n{sql}"

    def test_upgrade_all_predicates_is_edge_false(self) -> None:
        """Every INSERT has is_edge = false."""
        sqls = self._collect_execute_calls("upgrade")
        for sql in sqls:
            assert "false" in sql.lower(), f"Missing is_edge=false in:\n{sql}"

    def test_upgrade_sleep_predicates_is_temporal_true(self) -> None:
        """sleep_session and sleep_stage_summary are inserted with is_temporal=True."""
        sqls = self._collect_execute_calls("upgrade")
        sleep_sqls = [s for s in sqls if "sleep_session" in s or "sleep_stage_summary" in s]
        assert len(sleep_sqls) == 2
        for sql in sleep_sqls:
            # is_temporal is the 5th value in the VALUES clause — check 'true' appears
            assert "true" in sql.lower(), f"sleep predicate missing is_temporal=true:\n{sql}"

    def test_upgrade_measurement_predicates_is_temporal_true(self) -> None:
        """All seven measurement_ predicates are inserted with is_temporal=True."""
        mod = _load_migration()
        measurement_names = [
            n for n in mod.WELLNESS_PREDICATE_NAMES if n.startswith("measurement_")
        ]
        sqls = self._collect_execute_calls("upgrade")
        for name in measurement_names:
            matching = [s for s in sqls if f"'{name}'" in s]
            assert len(matching) == 1, f"Expected 1 INSERT for {name}, got {len(matching)}"
            assert "true" in matching[0].lower(), (
                f"{name} missing is_temporal=true in:\n{matching[0]}"
            )

    def test_upgrade_expected_subject_type_person(self) -> None:
        """All nine predicates have expected_subject_type='person'."""
        sqls = self._collect_execute_calls("upgrade")
        for sql in sqls:
            assert "'person'" in sql, f"Missing expected_subject_type=person in:\n{sql}"

    def test_downgrade_emits_one_delete(self) -> None:
        """downgrade() emits exactly one DELETE statement."""
        sqls = self._collect_execute_calls("downgrade")
        deletes = [s for s in sqls if s.strip().upper().startswith("DELETE")]
        assert len(deletes) == 1

    def test_downgrade_targets_all_nine_names(self) -> None:
        """The DELETE statement includes all nine predicate names."""
        mod = _load_migration()
        sqls = self._collect_execute_calls("downgrade")
        delete_sql = sqls[0]
        for name in mod.WELLNESS_PREDICATE_NAMES:
            assert name in delete_sql, f"Predicate name {name!r} missing from downgrade DELETE"

    def test_downgrade_uses_where_in_clause(self) -> None:
        """The DELETE is scoped to a WHERE name IN (...) clause."""
        sqls = self._collect_execute_calls("downgrade")
        upper = sqls[0].upper()
        assert "WHERE" in upper and "NAME" in upper and "IN" in upper


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
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

"""Tests for mem_003 wellness predicate migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. Upgrade idempotency: running twice yields same row count, no exception.
3. Downgrade removes exactly the nine wellness predicates.
4. All nine predicate names present after upgrade.
5. Metadata fields (scope, status, is_temporal, expected_subject_type) match spec.

Integration tests (marked pytest.mark.integration) require Docker + Postgres
provisioned via the shared ``db`` factory at the subject's predecessor revision.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.db import register_jsonb_codec, schema_search_path
from butlers.migrations import _build_alembic_config
from butlers.modules.memory import storage
from butlers.testing.migration import create_migrated_test_db, load_migration, migration_db_name
from tests.modules.memory._test_helpers import make_embedding_engine_mock

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


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


class TestMigrationFileAndChain:
    """Revision-chain + predicate-name/collision contract tests."""

    def test_nine_predicate_names_defined(self) -> None:
        """WELLNESS_PREDICATE_NAMES exports exactly nine names."""
        mod = load_migration(_MIGRATION_PATH, "mem_003")
        assert len(mod.WELLNESS_PREDICATE_NAMES) == 9

    def test_all_expected_names_present(self) -> None:
        """All nine canonical predicate names are in WELLNESS_PREDICATE_NAMES."""
        mod = load_migration(_MIGRATION_PATH, "mem_003")
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
        mod = load_migration(_MIGRATION_PATH, "mem_003")
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
    a real database migrated to mem_002, the revision before the subject.
    """

    @pytest.fixture
    async def predicate_registry_pool(self, db):
        """The real memory predicate_registry as mem_003's predecessor left it."""
        return await db(
            "core",
            "memory",
            schemas={"core": "health", "memory": "health"},
            schema="health",
            revisions={"memory": "mem_002"},
        )

    async def _run_upgrade(self, pool) -> None:
        """Execute upgrade() SQL against the pool using op.execute shim."""
        mod = load_migration(_MIGRATION_PATH, "mem_003")

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
        mod = load_migration(_MIGRATION_PATH, "mem_003")

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
        mod = load_migration(_MIGRATION_PATH, "mem_003")

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
        mod = load_migration(_MIGRATION_PATH, "mem_003")

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
        mod = load_migration(_MIGRATION_PATH, "mem_003")

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
        mod = load_migration(_MIGRATION_PATH, "mem_003")

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
        # mem_002 seeds measurement_heart_rate; remove it so only upgrade() could re-add it.
        await pool.execute("DELETE FROM predicate_registry WHERE name = 'measurement_heart_rate'")

        await self._run_upgrade(pool)

        row = await pool.fetchrow(
            "SELECT name FROM predicate_registry WHERE name = 'measurement_heart_rate'"
        )
        assert row is None, (
            "measurement_heart_rate should NOT be inserted by the wellness migration; "
            "it is the pre-existing point-in-time reading predicate from mem_002"
        )


_WORKOUT_DEFINITION = """
    SELECT to_jsonb(reg) - ARRAY['usage_count', 'last_used_at']
    FROM predicate_registry AS reg
    WHERE name = 'workout_session'
"""


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("initial", ["absent", "proposed", "active"])
async def test_workout_predicate_round_trip_with_real_fact_write(postgres_container, initial):
    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
        schemas={"core": "health", "memory": "health"},
        revisions={"memory": "mem_019"},
    )
    config = _build_alembic_config(url, chains=["memory"], target_schema="health")
    search_path = schema_search_path("health")
    assert search_path is not None
    pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=2,
        init=register_jsonb_codec,
        server_settings={"search_path": search_path},
    )
    try:
        if initial != "absent":
            await pool.execute(
                """
                INSERT INTO predicate_registry
                    (name, expected_subject_type, description, scope, status,
                     aliases, example_json, description_embedding, usage_count)
                VALUES ('workout_session', 'person', 'Existing fixture description',
                        'global', $1, ARRAY['fixture_activity'], '{"source":"fixture"}',
                        array_fill(0.25::real, ARRAY[384])::vector, 7)
                """,
                initial,
            )
        before = await pool.fetchval(_WORKOUT_DEFINITION)
        await asyncio.to_thread(command.upgrade, config, "memory@mem_020")
        applied = await pool.fetchval(_WORKOUT_DEFINITION)
        assert applied is not None
        if initial == "active":
            assert applied == before
        else:
            assert applied["status"] == "active"
            assert applied["scope"] == "health"
            assert applied["expected_subject_type"] == "person"
            assert applied["expected_object_type"] is None
            assert applied["is_edge"] is False
            assert applied["is_temporal"] is True
            assert applied["description_embedding"] is None
            assert "Workout" in applied["description"]
            assert applied["example_json"]["metadata"]["activity_type"] == "running"

        # Use the real store and spy on the actual novelty branch.
        embedding = make_embedding_engine_mock()
        with patch.object(
            storage, "_fuzzy_match_predicates", wraps=storage._fuzzy_match_predicates
        ) as fuzzy:
            result = await storage.store_fact(
                pool,
                "Fixture athlete",
                "workout_session",
                "Fixture run",
                embedding,
                scope="health",
                valid_at=datetime(2026, 4, 24, 9, tzinfo=UTC),
                idempotency_key="mem020-fixture-workout",
            )
        fuzzy.assert_not_awaited()
        assert "suggestions" not in result
        fact = await pool.fetchrow("SELECT predicate, content FROM facts WHERE id=$1", result["id"])
        assert fact is not None
        assert dict(fact) == {"predicate": "workout_session", "content": "Fixture run"}
        assert await pool.fetchval(_WORKOUT_DEFINITION) == applied

        await asyncio.to_thread(command.upgrade, config, "memory@mem_020")
        assert await pool.fetchval(_WORKOUT_DEFINITION) == applied

        if initial == "absent":
            edit_description = (
                "UPDATE predicate_registry SET description=$1 WHERE name='workout_session'"
            )
            await pool.execute(edit_description, "Fixture curated edit")
            with pytest.raises(DBAPIError, match="workout_session predicate definition changed"):
                await asyncio.to_thread(command.downgrade, config, "memory@mem_019")
            assert (await pool.fetchval(_WORKOUT_DEFINITION))[
                "description"
            ] == "Fixture curated edit"
            await pool.execute(edit_description, applied["description"])

        await asyncio.to_thread(command.downgrade, config, "memory@mem_019")
        assert await pool.fetchval(_WORKOUT_DEFINITION) == before
        assert (
            await pool.fetchrow("SELECT predicate, content FROM facts WHERE id=$1", result["id"])
            == fact
        )
        if initial != "absent":
            assert (
                await pool.fetchval(
                    "SELECT usage_count FROM predicate_registry WHERE name='workout_session'"
                )
                == 8
            )
    finally:
        await pool.close()

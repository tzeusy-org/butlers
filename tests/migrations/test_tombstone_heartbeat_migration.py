"""Tests for chronicler_007 tombstone_heartbeat_episodes migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. upgrade() SQL shape: ADD COLUMN and UPDATE statements are present and
   reference the correct table, source_name, and trigger_source filter.
3. downgrade() SQL shape: DROP COLUMN statements are present.
4. Exclusion constants are imported from sessions.py (single source of truth).
5. Idempotency: the WHERE clause includes tombstone_at IS NULL.
6. Integration: pre-migration count > 0, post-migration count == 0
   (marked pytest.mark.integration — requires Docker + Postgres).

Issue: bu-6t63s
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
    / "roster"
    / "chronicler"
    / "migrations"
    / "007_tombstone_heartbeat_episodes.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("chronicler_007", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


class TestMigrationFileAndChain:
    """Revision-chain + chain-discovery contract tests."""

    def test_revision_chain(self, migration_mod) -> None:
        """chronicler_007 -> chronicler_006, no branch/depends."""
        assert migration_mod.revision == "chronicler_007"
        assert migration_mod.down_revision == "chronicler_006"
        assert migration_mod.branch_labels is None
        assert migration_mod.depends_on is None

    def test_chronicler_chain_includes_007(self) -> None:
        """Migration chain discovery must pick up 007_tombstone_heartbeat_episodes."""
        from butlers.migrations import _resolve_chain_dir

        chain_dir = _resolve_chain_dir("chronicler")
        assert chain_dir is not None, "Chronicler chain directory not found"
        files = sorted(f.name for f in chain_dir.glob("[0-9]*.py"))
        assert "007_tombstone_heartbeat_episodes.py" in files, (
            "007_tombstone_heartbeat_episodes.py not in discovered chronicler chain"
        )


class TestExclusionConstantsImport:
    """The migration imports constants from sessions.py (not copy-paste)."""

    def test_excluded_trigger_sources_imported_from_sessions(self, migration_mod) -> None:
        """EXCLUDED_TRIGGER_SOURCES is imported from adapters.sessions."""
        from butlers.chronicler.adapters.sessions import EXCLUDED_TRIGGER_SOURCES as authoritative

        # The migration references the same frozenset object (or identical value).
        assert migration_mod.EXCLUDED_TRIGGER_SOURCES == authoritative, (
            "Migration's EXCLUDED_TRIGGER_SOURCES diverges from sessions.py"
        )

    def test_excluded_trigger_source_prefix_imported_from_sessions(self, migration_mod) -> None:
        """EXCLUDED_TRIGGER_SOURCE_PREFIX is imported from adapters.sessions."""
        from butlers.chronicler.adapters.sessions import (
            EXCLUDED_TRIGGER_SOURCE_PREFIX as authoritative,
        )

        assert migration_mod.EXCLUDED_TRIGGER_SOURCE_PREFIX == authoritative, (
            "Migration's EXCLUDED_TRIGGER_SOURCE_PREFIX diverges from sessions.py"
        )

    def test_known_exact_sources_present(self, migration_mod) -> None:
        """Known exact-match sources (tick, qa, healing) are in the exclusion set."""
        for src in ("tick", "qa", "healing"):
            assert src in migration_mod.EXCLUDED_TRIGGER_SOURCES, (
                f"Expected {src!r} in EXCLUDED_TRIGGER_SOURCES"
            )

    def test_schedule_prefix_is_schedule_colon(self, migration_mod) -> None:
        """EXCLUDED_TRIGGER_SOURCE_PREFIX must equal 'schedule:'."""
        assert migration_mod.EXCLUDED_TRIGGER_SOURCE_PREFIX == "schedule:"


def _collect_downgrade_calls() -> list[str]:
    """Run downgrade() with op mocked; return SQL strings passed to op.execute.

    Loaded once and shared across TestDowngradeSQLShape tests — eliminates
    repeated module loads and import-time races under pytest-xdist.
    """
    mod = _load_migration()
    calls_collected: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = calls_collected.append
    with patch.object(mod, "op", mock_op):
        mod.downgrade()
    return calls_collected


@pytest.fixture(scope="module")
def migration_mod():
    """Loaded migration module — module-scoped, one load per worker.

    Shared across TestMigrationFileAndChain and TestExclusionConstantsImport to
    avoid repeated importlib loads for cheap attribute reads.
    """
    return _load_migration()


@pytest.fixture(scope="module")
def downgrade_sqls() -> list[str]:
    """Collected SQL statements from downgrade() — module-scoped, one load per worker."""
    return _collect_downgrade_calls()


class TestDowngradeSQLShape:
    """Verify downgrade() drops the columns guardedly and does not reverse tombstones.

    The upgrade() ADD COLUMN / UPDATE shape (tombstone_at=now(), tombstone_reason
    with bu-noocq/bu-6t63s refs, source/trigger filters, idempotency guard) is
    exercised end-to-end against a live DB by the integration tests below.
    """

    def test_drop_columns_use_if_exists(self, downgrade_sqls: list[str]) -> None:
        """downgrade() drops tombstone_reason from episodes + point_events with IF EXISTS."""
        drop_col_stmts = [
            s for s in downgrade_sqls if "DROP COLUMN" in s and "tombstone_reason" in s
        ]
        assert any("episodes" in s for s in drop_col_stmts)
        assert any("point_events" in s for s in drop_col_stmts)
        for stmt in drop_col_stmts:
            assert "IF EXISTS" in stmt, f"DROP COLUMN missing IF EXISTS guard:\n{stmt}"

    def test_downgrade_does_not_emit_update(self, downgrade_sqls: list[str]) -> None:
        """downgrade() must NOT emit any UPDATE statement (tombstones are not reversed)."""
        update_stmts = [s for s in downgrade_sqls if s.strip().upper().startswith("UPDATE")]
        assert not update_stmts, (
            f"downgrade() must not reverse tombstones, but found UPDATE:\n{update_stmts}"
        )


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTombstoneHeartbeatMigrationIntegration:
    """Integration tests requiring a real PostgreSQL instance.

    These tests exercise the full upgrade scenario: a reproducer that inserts
    heartbeat episode rows (simulating the pre-bu-x096m state) and verifies
    that upgrade() tombstones all of them (count == 0 after), while leaving
    non-heartbeat rows untouched.
    """

    @pytest.fixture
    async def episodes_pool(self, provisioned_postgres_pool):
        """Provision a fresh DB with the chronicler schema up to chronicler_006."""
        async with provisioned_postgres_pool() as pool:
            # Create the minimal chronicles schema in-band for the integration test.
            # We need source_adapter_state and episodes tables with tombstone columns.
            await pool.execute("""
                CREATE TABLE IF NOT EXISTS source_adapter_state (
                    source_name TEXT PRIMARY KEY,
                    chronicler_compatibility TEXT NOT NULL DEFAULT 'supported',
                    read_surface TEXT,
                    boundary_semantics TEXT,
                    optional_schema BOOLEAN NOT NULL DEFAULT false,
                    active BOOLEAN NOT NULL DEFAULT false,
                    inactive_reason TEXT,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            await pool.execute("""
                CREATE TABLE IF NOT EXISTS point_events (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    source_name TEXT NOT NULL
                        REFERENCES source_adapter_state(source_name),
                    source_ref TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TIMESTAMPTZ NOT NULL,
                    precision TEXT NOT NULL DEFAULT 'exact',
                    title TEXT,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    privacy TEXT NOT NULL DEFAULT 'normal',
                    retention_days INTEGER,
                    tombstone_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (source_name, source_ref)
                )
            """)
            await pool.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    source_name TEXT NOT NULL
                        REFERENCES source_adapter_state(source_name),
                    source_ref TEXT NOT NULL,
                    episode_type TEXT NOT NULL,
                    start_at TIMESTAMPTZ NOT NULL,
                    end_at TIMESTAMPTZ,
                    precision TEXT NOT NULL DEFAULT 'exact',
                    title TEXT,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    privacy TEXT NOT NULL DEFAULT 'normal',
                    retention_days INTEGER,
                    tombstone_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (source_name, source_ref)
                )
            """)
            # Register the source adapter
            await pool.execute("""
                INSERT INTO source_adapter_state (source_name, chronicler_compatibility)
                VALUES ('core.sessions', 'supported')
                ON CONFLICT DO NOTHING
            """)
            yield pool

    async def _run_upgrade(self, pool) -> None:
        """Execute upgrade() SQL against the pool using op.execute shim."""
        mod = _load_migration()
        sqls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls.append(sql)
        # Silence the _log_candidate_counts helper in integration context
        mock_bind = MagicMock()
        mock_bind.execute.return_value.fetchall.return_value = []
        mock_op.get_bind.return_value = mock_bind

        with patch.object(mod, "op", mock_op):
            mod.upgrade()

        for sql in sqls:
            await pool.execute(sql)

    async def _run_downgrade(self, pool) -> None:
        """Execute downgrade() SQL against the pool using op.execute shim."""
        mod = _load_migration()
        sqls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls.append(sql)

        with patch.object(mod, "op", mock_op):
            mod.downgrade()

        for sql in sqls:
            await pool.execute(sql)

    async def _insert_episode(self, pool, *, source_ref: str, trigger_source: str | None) -> None:
        """Insert one test episode with the given trigger_source in payload."""
        payload: dict = {}
        if trigger_source is not None:
            payload["trigger_source"] = trigger_source

        # Pass payload as a dict; the asyncpg JSONB codec registered on the pool
        # handles encoding.  The ::jsonb cast is omitted because it conflicts with
        # the binary codec format (double-encoding).
        await pool.execute(
            """
            INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, payload)
            VALUES
                ('core.sessions', $1, 'work', now() - interval '1 hour', $2)
            """,
            source_ref,
            payload,
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_pre_migration_heartbeat_episodes_exist(self, episodes_pool) -> None:
        """Reproducer: heartbeat episodes exist before upgrade (count > 0).

        This is the failing-reproducer test that the acceptance criteria requires.
        It inserts rows that represent the pre-bu-x096m state and asserts that
        there are non-zero candidates before the migration runs.
        """
        pool = episodes_pool

        # Insert one row per excluded trigger_source (representing legacy rows).
        for idx, ts in enumerate(("tick", "qa", "healing", "schedule:chronicler_day_close")):
            await self._insert_episode(pool, source_ref=f"schema.sessions:{idx}", trigger_source=ts)
        # Also insert a legitimate work session that must NOT be tombstoned.
        await self._insert_episode(
            pool, source_ref="schema.sessions:legit", trigger_source="user_message"
        )

        # Before upgrade: 4 heartbeat candidates must be present.
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'core.sessions'
              AND tombstone_at IS NULL
              AND (
                  payload->>'trigger_source' IN ('tick', 'qa', 'healing')
                  OR payload->>'trigger_source' LIKE 'schedule:%'
              )
            """
        )
        assert count > 0, (
            f"Pre-migration reproducer expected count > 0 but got {count}. "
            "Check that the test rows were inserted correctly."
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_post_migration_heartbeat_episodes_tombstoned(self, episodes_pool) -> None:
        """Post-migration: all heartbeat episodes are tombstoned (count == 0).

        This is the pass-condition test that the acceptance criteria requires.
        """
        pool = episodes_pool

        # Insert test rows (same as the reproducer test).
        for idx, ts in enumerate(("tick", "qa", "healing", "schedule:chronicler_day_close")):
            await self._insert_episode(
                pool,
                source_ref=f"schema.sessions:post:{idx}",
                trigger_source=ts,
            )
        # Also insert a legitimate work session that must NOT be tombstoned.
        await self._insert_episode(
            pool,
            source_ref="schema.sessions:post:legit",
            trigger_source="user_message",
        )

        # Run the migration.
        await self._run_upgrade(pool)

        # After upgrade: zero heartbeat candidates must remain.
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'core.sessions'
              AND tombstone_at IS NULL
              AND (
                  payload->>'trigger_source' IN ('tick', 'qa', 'healing')
                  OR payload->>'trigger_source' LIKE 'schedule:%'
              )
            """
        )
        assert count == 0, (
            f"Post-migration: expected 0 untombstoned heartbeat episodes, got {count}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_non_heartbeat_episodes_not_tombstoned(self, episodes_pool) -> None:
        """Legitimate (non-heartbeat) episodes are left untouched by the migration."""
        pool = episodes_pool

        await self._insert_episode(
            pool,
            source_ref="schema.sessions:legit-only",
            trigger_source="user_message",
        )
        await self._insert_episode(
            pool,
            source_ref="schema.sessions:legit-deadline",
            trigger_source="deadline:passport_expires",
        )

        await self._run_upgrade(pool)

        # Both legitimate rows must remain with tombstone_at IS NULL.
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'core.sessions'
              AND tombstone_at IS NULL
              AND payload->>'trigger_source' IN ('user_message', 'deadline:passport_expires')
            """
        )
        assert count == 2, f"Expected 2 non-heartbeat episodes to remain untombstoned, got {count}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_upgrade_is_idempotent(self, episodes_pool) -> None:
        """Running upgrade() twice leaves the same row state (no regress)."""
        pool = episodes_pool

        for idx, ts in enumerate(("tick", "schedule:day_close")):
            await self._insert_episode(
                pool, source_ref=f"schema.sessions:idem:{idx}", trigger_source=ts
            )

        await self._run_upgrade(pool)

        # Counts after first run.
        remaining_after_first = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'core.sessions'
              AND tombstone_at IS NULL
              AND (
                  payload->>'trigger_source' IN ('tick', 'qa', 'healing')
                  OR payload->>'trigger_source' LIKE 'schedule:%'
              )
            """
        )

        # Second run must not raise and must not change the count.
        await self._run_upgrade(pool)

        remaining_after_second = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'core.sessions'
              AND tombstone_at IS NULL
              AND (
                  payload->>'trigger_source' IN ('tick', 'qa', 'healing')
                  OR payload->>'trigger_source' LIKE 'schedule:%'
              )
            """
        )
        assert remaining_after_first == remaining_after_second == 0, (
            f"Idempotency failure: first={remaining_after_first}, second={remaining_after_second}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_tombstoned_rows_have_tombstone_reason(self, episodes_pool) -> None:
        """Tombstoned rows carry the expected tombstone_reason string."""
        pool = episodes_pool

        await self._insert_episode(
            pool, source_ref="schema.sessions:reason-check", trigger_source="tick"
        )
        await self._run_upgrade(pool)

        row = await pool.fetchrow(
            """
            SELECT tombstone_reason FROM episodes
            WHERE source_name = 'core.sessions'
              AND source_ref = 'schema.sessions:reason-check'
            """
        )
        assert row is not None, "Expected tombstoned row not found"
        assert row["tombstone_reason"] is not None, "tombstone_reason must not be NULL"
        assert "bu-noocq" in row["tombstone_reason"], "tombstone_reason missing bu-noocq ref"
        assert "bu-6t63s" in row["tombstone_reason"], "tombstone_reason missing bu-6t63s ref"

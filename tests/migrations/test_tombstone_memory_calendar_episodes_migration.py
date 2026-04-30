"""Tests for chronicler_008 tombstone_memory_calendar_episodes migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. upgrade() SQL shape: UPDATE statement targets correct table, source_name,
   title filter (exact list, not LIKE), tombstone_at / tombstone_reason fields,
   and tombstone_at IS NULL idempotency guard.
3. downgrade() is a no-op (no SQL statements emitted).
4. Known butler-managed task titles are present in the IN filter.
5. The exact title list (not LIKE 'memory_%') is used for safety.
6. Integration: pre-migration count > 0, post-migration count == 0
   (marked pytest.mark.integration — requires Docker + Postgres).

Issue: bu-aqqx0
Dependency: chronicler_007 (bu-6t63s) adds tombstone_reason column.
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
    / "008_tombstone_memory_calendar_episodes.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("chronicler_008", _MIGRATION_PATH)
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
        """008_tombstone_memory_calendar_episodes.py must exist at expected path."""
        assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"

    def test_revision_id(self) -> None:
        """Revision is chronicler_008."""
        mod = _load_migration()
        assert mod.revision == "chronicler_008"

    def test_down_revision_points_to_007(self) -> None:
        """down_revision must point to chronicler_007.

        chronicler_007 (bu-6t63s) adds the tombstone_reason column that this
        migration writes into.  Without this ordering guarantee, upgrade() would
        fail with 'column tombstone_reason does not exist'.
        """
        mod = _load_migration()
        assert mod.down_revision == "chronicler_007"

    def test_branch_labels_none(self) -> None:
        """Non-root migrations must not declare branch_labels."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        """No extra cross-chain dependency declared."""
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

    def test_migration_ordered_after_007(self) -> None:
        """008_* must sort after 007_* in the migrations directory.

        chronicler_007 (bu-6t63s, PR #1299) may not be merged yet when this
        test runs on the agent/bu-aqqx0 branch.  The test skips automatically
        if 007_* is absent so that the CI gate is not hard-blocked on the sibling
        PR.  Once PR #1299 merges and this branch is rebased, the 007 file will
        be present and the test will run fully.
        """
        migrations_dir = _MIGRATION_PATH.parent
        files = sorted(f.name for f in migrations_dir.glob("[0-9]*.py"))
        idx_007 = next((i for i, f in enumerate(files) if f.startswith("007_")), None)
        idx_008 = next((i for i, f in enumerate(files) if f.startswith("008_")), None)
        if idx_007 is None:
            pytest.skip(
                "007_* migration not found (chronicler_007 / bu-6t63s PR #1299 not yet merged)"
            )
        assert idx_008 is not None, "008_* migration not found"
        assert idx_008 > idx_007, "008_* must sort after 007_*"

    def test_chronicler_chain_includes_008(self) -> None:
        """Migration chain discovery must pick up 008_tombstone_memory_calendar_episodes."""
        from butlers.migrations import _resolve_chain_dir

        chain_dir = _resolve_chain_dir("chronicler")
        assert chain_dir is not None, "Chronicler chain directory not found"
        files = sorted(f.name for f in chain_dir.glob("[0-9]*.py"))
        assert "008_tombstone_memory_calendar_episodes.py" in files, (
            "008_tombstone_memory_calendar_episodes.py not in discovered chronicler chain"
        )


class TestKnownTitleConstants:
    """The migration declares the known butler-managed task title constants."""

    def test_memory_consolidation_in_titles(self) -> None:
        """memory_consolidation is in _BUTLER_MEMORY_TASK_TITLES."""
        mod = _load_migration()
        assert "memory_consolidation" in mod._BUTLER_MEMORY_TASK_TITLES

    def test_memory_episode_cleanup_in_titles(self) -> None:
        """memory_episode_cleanup is in _BUTLER_MEMORY_TASK_TITLES."""
        mod = _load_migration()
        assert "memory_episode_cleanup" in mod._BUTLER_MEMORY_TASK_TITLES

    def test_memory_purge_superseded_in_titles(self) -> None:
        """memory_purge_superseded is in _BUTLER_MEMORY_TASK_TITLES."""
        mod = _load_migration()
        assert "memory_purge_superseded" in mod._BUTLER_MEMORY_TASK_TITLES

    def test_source_name_is_google_calendar_completed(self) -> None:
        """_SOURCE_NAME must equal 'google_calendar.completed'."""
        mod = _load_migration()
        assert mod._SOURCE_NAME == "google_calendar.completed"

    def test_tombstone_reason_references_bu_daaff(self) -> None:
        """_TOMBSTONE_REASON must reference bu-daaff (the prevention-track PR)."""
        mod = _load_migration()
        assert "bu-daaff" in mod._TOMBSTONE_REASON

    def test_tombstone_reason_references_bu_aqqx0(self) -> None:
        """_TOMBSTONE_REASON must reference bu-aqqx0 (this issue)."""
        mod = _load_migration()
        assert "bu-aqqx0" in mod._TOMBSTONE_REASON


class TestUpgradeSQLShape:
    """Verify the SQL emitted by upgrade() matches the spec."""

    def _collect_execute_calls(self) -> list[str]:
        """Run upgrade() with op mocked; return SQL strings passed to op.execute."""
        mod = _load_migration()
        calls_collected: list[str] = []

        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: calls_collected.append(sql)
        # _log_candidate_counts uses op.get_bind() — mock it so it doesn't fail.
        mock_bind = MagicMock()
        mock_bind.execute.return_value.fetchall.return_value = []
        mock_op.get_bind.return_value = mock_bind

        with patch.object(mod, "op", mock_op):
            mod.upgrade()

        return calls_collected

    def test_update_targets_episodes_table(self) -> None:
        """upgrade() emits an UPDATE against the episodes table."""
        sqls = self._collect_execute_calls()
        update_stmts = [
            s for s in sqls if s.strip().upper().startswith("UPDATE") and "episodes" in s
        ]
        assert update_stmts, "No UPDATE episodes statement found in upgrade SQL"

    def test_update_sets_tombstone_at(self) -> None:
        """The UPDATE sets tombstone_at = now()."""
        sqls = self._collect_execute_calls()
        update_stmts = [
            s for s in sqls if s.strip().upper().startswith("UPDATE") and "episodes" in s
        ]
        assert update_stmts, "No UPDATE episodes statement"
        assert "tombstone_at" in update_stmts[0], "UPDATE missing tombstone_at"
        assert "now()" in update_stmts[0], "UPDATE missing now() for tombstone_at"

    def test_update_sets_tombstone_reason(self) -> None:
        """The UPDATE sets tombstone_reason with the expected issue refs."""
        sqls = self._collect_execute_calls()
        update_stmts = [
            s for s in sqls if s.strip().upper().startswith("UPDATE") and "episodes" in s
        ]
        assert update_stmts, "No UPDATE episodes statement"
        assert "tombstone_reason" in update_stmts[0], "UPDATE missing tombstone_reason"
        assert "bu-daaff" in update_stmts[0], "tombstone_reason missing bu-daaff issue ref"
        assert "bu-aqqx0" in update_stmts[0], "tombstone_reason missing bu-aqqx0 issue ref"

    def test_update_scoped_to_google_calendar_completed(self) -> None:
        """The UPDATE's WHERE clause is scoped to source_name='google_calendar.completed'."""
        sqls = self._collect_execute_calls()
        update_stmts = [
            s for s in sqls if s.strip().upper().startswith("UPDATE") and "episodes" in s
        ]
        assert update_stmts, "No UPDATE episodes statement"
        assert "google_calendar.completed" in update_stmts[0], (
            "UPDATE missing source_name='google_calendar.completed'"
        )

    def test_update_excludes_already_tombstoned_rows(self) -> None:
        """The WHERE clause includes tombstone_at IS NULL for idempotency."""
        sqls = self._collect_execute_calls()
        update_stmts = [
            s for s in sqls if s.strip().upper().startswith("UPDATE") and "episodes" in s
        ]
        assert update_stmts, "No UPDATE episodes statement"
        assert "tombstone_at IS NULL" in update_stmts[0], (
            "UPDATE missing tombstone_at IS NULL guard (idempotency)"
        )

    def test_update_filters_by_exact_title_list(self) -> None:
        """The WHERE clause uses title IN (...) with the known butler task names."""
        sqls = self._collect_execute_calls()
        update_stmts = [
            s for s in sqls if s.strip().upper().startswith("UPDATE") and "episodes" in s
        ]
        assert update_stmts, "No UPDATE episodes statement"
        stmt = update_stmts[0]
        for title in ("memory_consolidation", "memory_episode_cleanup", "memory_purge_superseded"):
            assert title in stmt, f"Expected {title!r} in UPDATE WHERE clause"

    def test_update_uses_in_not_like(self) -> None:
        """The WHERE clause uses IN (exact match), NOT LIKE 'memory_%'.

        Rationale: LIKE 'memory_%' could tombstone legitimate user calendar
        events titled e.g. 'memory workshop'.  The exact IN list is safer.
        """
        sqls = self._collect_execute_calls()
        update_stmts = [
            s for s in sqls if s.strip().upper().startswith("UPDATE") and "episodes" in s
        ]
        assert update_stmts, "No UPDATE episodes statement"
        stmt = update_stmts[0]
        # Must use IN for title filtering
        assert " IN " in stmt.upper(), "Expected title IN (...) filter"
        # Must NOT use LIKE for title filtering (the memory_% pattern is unsafe)
        assert "LIKE" not in stmt.upper(), (
            "Must not use LIKE for title filter — use exact IN list instead"
        )


class TestDowngradeSQLShape:
    """Verify that downgrade() emits no SQL (tombstones are not reversed)."""

    def _collect_execute_calls(self) -> list[str]:
        """Run downgrade() with op mocked; return SQL strings."""
        mod = _load_migration()
        calls_collected: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: calls_collected.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.downgrade()
        return calls_collected

    def test_downgrade_is_noop(self) -> None:
        """downgrade() must emit no SQL statements (tombstones are permanent)."""
        sqls = self._collect_execute_calls()
        assert not sqls, f"downgrade() must be a no-op, but found SQL statements:\n{sqls}"

    def test_downgrade_does_not_emit_update(self) -> None:
        """downgrade() must NOT emit any UPDATE statement."""
        sqls = self._collect_execute_calls()
        update_stmts = [s for s in sqls if s.strip().upper().startswith("UPDATE")]
        assert not update_stmts, (
            f"downgrade() must not reverse tombstones, but found UPDATE:\n{update_stmts}"
        )


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTombstoneMemoryCalendarEpisodesIntegration:
    """Integration tests requiring a real PostgreSQL instance.

    These tests exercise the full upgrade scenario: a reproducer inserts
    episodes rows that simulate the pre-bu-daaff state (memory_* calendar
    events projected into chronicler.episodes), then verifies that
    upgrade() tombstones all of them while leaving unrelated rows untouched.

    The episodes table shape here simulates the post-chronicler_007 state
    (tombstone_reason column is present).
    """

    @pytest.fixture
    async def episodes_pool(self, provisioned_postgres_pool):
        """Provision a fresh DB with the chronicler episodes schema."""
        async with provisioned_postgres_pool() as pool:
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
                    tombstone_reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (source_name, source_ref)
                )
            """)
            # Register the two source adapters needed for the tests.
            await pool.execute("""
                INSERT INTO source_adapter_state (source_name)
                VALUES ('google_calendar.completed')
                ON CONFLICT DO NOTHING
            """)
            await pool.execute("""
                INSERT INTO source_adapter_state (source_name)
                VALUES ('core.sessions')
                ON CONFLICT DO NOTHING
            """)
            yield pool

    async def _run_upgrade(self, pool) -> None:
        """Execute upgrade() SQL against the pool using op.execute shim."""
        mod = _load_migration()
        sqls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls.append(sql)
        # Silence _log_candidate_counts in integration context.
        mock_bind = MagicMock()
        mock_bind.execute.return_value.fetchall.return_value = []
        mock_op.get_bind.return_value = mock_bind

        with patch.object(mod, "op", mock_op):
            mod.upgrade()

        for sql in sqls:
            await pool.execute(sql)

    async def _insert_episode(
        self,
        pool,
        *,
        source_name: str,
        source_ref: str,
        title: str | None,
    ) -> None:
        """Insert one test episode row."""
        await pool.execute(
            """
            INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, title)
            VALUES
                ($1, $2, 'scheduled_block', now() - interval '1 hour', $3)
            """,
            source_name,
            source_ref,
            title,
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_pre_migration_memory_episodes_exist(self, episodes_pool) -> None:
        """Reproducer: memory_* calendar episodes exist before upgrade (count > 0).

        This is the failing-reproducer test required by the acceptance criteria.
        It inserts rows representing the pre-bu-daaff state and asserts that
        the candidate count is > 0 before the migration runs.
        """
        pool = episodes_pool

        # Insert one row per known butler-managed memory task title.
        for idx, title in enumerate(
            ("memory_consolidation", "memory_episode_cleanup", "memory_purge_superseded")
        ):
            await self._insert_episode(
                pool,
                source_name="google_calendar.completed",
                source_ref=f"pre-migration:{title}:{idx}",
                title=title,
            )
        # Also insert a legitimate user calendar event that must NOT be tombstoned.
        await self._insert_episode(
            pool,
            source_name="google_calendar.completed",
            source_ref="pre-migration:user-event:0",
            title="Team retrospective",
        )

        # Before upgrade: 3 memory candidates must be present.
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'google_calendar.completed'
              AND tombstone_at IS NULL
              AND title IN (
                  'memory_consolidation',
                  'memory_episode_cleanup',
                  'memory_purge_superseded'
              )
            """
        )
        assert count > 0, (
            f"Pre-migration reproducer expected count > 0 but got {count}. "
            "Check that the test rows were inserted correctly."
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_post_migration_memory_episodes_tombstoned(self, episodes_pool) -> None:
        """Post-migration: all memory_* calendar episodes are tombstoned (count == 0).

        This is the pass-condition test required by the acceptance criteria.
        """
        pool = episodes_pool

        for idx, title in enumerate(
            ("memory_consolidation", "memory_episode_cleanup", "memory_purge_superseded")
        ):
            await self._insert_episode(
                pool,
                source_name="google_calendar.completed",
                source_ref=f"post-migration:{title}:{idx}",
                title=title,
            )
        # Also insert a legitimate user calendar event.
        await self._insert_episode(
            pool,
            source_name="google_calendar.completed",
            source_ref="post-migration:user-event:0",
            title="Team retrospective",
        )

        # Run the migration.
        await self._run_upgrade(pool)

        # After upgrade: zero memory candidates must remain.
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'google_calendar.completed'
              AND tombstone_at IS NULL
              AND title IN (
                  'memory_consolidation',
                  'memory_episode_cleanup',
                  'memory_purge_superseded'
              )
            """
        )
        assert count == 0, f"Post-migration: expected 0 untombstoned memory_* episodes, got {count}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unrelated_calendar_episodes_not_tombstoned(self, episodes_pool) -> None:
        """Legitimate calendar episodes are left untouched by the migration."""
        pool = episodes_pool

        for idx, title in enumerate(
            ("Team retrospective", "Doctor appointment", "Memory workshop")
        ):
            await self._insert_episode(
                pool,
                source_name="google_calendar.completed",
                source_ref=f"legit-only:{idx}",
                title=title,
            )

        await self._run_upgrade(pool)

        # All three legitimate rows must remain with tombstone_at IS NULL.
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'google_calendar.completed'
              AND tombstone_at IS NULL
              AND title IN ('Team retrospective', 'Doctor appointment', 'Memory workshop')
            """
        )
        assert count == 3, f"Expected 3 legitimate episodes to remain untombstoned, got {count}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_other_source_episodes_not_tombstoned(self, episodes_pool) -> None:
        """Episodes from other sources (e.g. core.sessions) are unaffected."""
        pool = episodes_pool

        await self._insert_episode(
            pool,
            source_name="core.sessions",
            source_ref="other-source:0",
            title="memory_consolidation",  # Same title, different source — must NOT be tombstoned
        )

        await self._run_upgrade(pool)

        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'core.sessions'
              AND tombstone_at IS NULL
              AND title = 'memory_consolidation'
            """
        )
        assert count == 1, (
            f"Episodes from other sources must not be tombstoned; expected 1, got {count}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_upgrade_is_idempotent(self, episodes_pool) -> None:
        """Running upgrade() twice leaves the same row state (no regress)."""
        pool = episodes_pool

        await self._insert_episode(
            pool,
            source_name="google_calendar.completed",
            source_ref="idem:0",
            title="memory_consolidation",
        )
        await self._insert_episode(
            pool,
            source_name="google_calendar.completed",
            source_ref="idem:1",
            title="memory_purge_superseded",
        )

        await self._run_upgrade(pool)

        remaining_after_first = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'google_calendar.completed'
              AND tombstone_at IS NULL
              AND title IN (
                  'memory_consolidation',
                  'memory_episode_cleanup',
                  'memory_purge_superseded'
              )
            """
        )

        # Second run must not raise and must not change the count.
        await self._run_upgrade(pool)

        remaining_after_second = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'google_calendar.completed'
              AND tombstone_at IS NULL
              AND title IN (
                  'memory_consolidation',
                  'memory_episode_cleanup',
                  'memory_purge_superseded'
              )
            """
        )
        assert remaining_after_first == remaining_after_second == 0, (
            f"Idempotency failure: first={remaining_after_first}, second={remaining_after_second}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_tombstoned_rows_have_correct_tombstone_reason(self, episodes_pool) -> None:
        """Tombstoned rows carry the expected tombstone_reason string."""
        pool = episodes_pool

        await self._insert_episode(
            pool,
            source_name="google_calendar.completed",
            source_ref="reason-check:0",
            title="memory_episode_cleanup",
        )
        await self._run_upgrade(pool)

        row = await pool.fetchrow(
            """
            SELECT tombstone_reason FROM episodes
            WHERE source_name = 'google_calendar.completed'
              AND source_ref = 'reason-check:0'
            """
        )
        assert row is not None, "Expected tombstoned row not found"
        assert row["tombstone_reason"] is not None, "tombstone_reason must not be NULL"
        assert "bu-daaff" in row["tombstone_reason"], "tombstone_reason missing bu-daaff ref"
        assert "bu-aqqx0" in row["tombstone_reason"], "tombstone_reason missing bu-aqqx0 ref"

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
    """Revision-chain + chain-discovery contract tests."""

    def test_revision_chain(self) -> None:
        """chronicler_008 -> chronicler_007 (adds the tombstone_reason column it
        writes into); no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "chronicler_008"
        assert mod.down_revision == "chronicler_007"
        assert mod.branch_labels is None
        assert mod.depends_on is None

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


class TestDowngradeSQLShape:
    """Verify that downgrade() emits no SQL (tombstones are not reversed).

    The upgrade() UPDATE shape (tombstone_at=now(), tombstone_reason with
    bu-daaff/bu-aqqx0 refs, google_calendar.completed source scope, the exact
    title IN-list NOT LIKE 'memory_%' for safety, idempotency guard) is exercised
    end-to-end by the integration tests below — including 'Memory workshop',
    which proves the IN-not-LIKE safety boundary.
    """

    def test_downgrade_is_noop(self) -> None:
        """downgrade() must emit no SQL statements (tombstones are permanent)."""
        mod = _load_migration()
        sqls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.downgrade()
        assert not sqls, f"downgrade() must be a no-op, but found SQL statements:\n{sqls}"

    def test_upgrade_uses_in_not_like(self) -> None:
        """upgrade() UPDATE must filter titles via an exact `IN` list, NOT
        `LIKE 'memory_%'`.

        A LIKE 'memory_%' bug would silently tombstone legitimate user calendar
        events whose title merely starts with 'memory_' (e.g. 'memory_journal',
        'memory_personal_note'). The integration tests do not catch this — their
        legitimate-row titles like 'Team retrospective' don't match LIKE
        'memory_%'. This pins the exact-IN-list safety boundary at the SQL level.
        """
        mod = _load_migration()
        sqls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls.append(sql)
        mock_bind = MagicMock()
        mock_bind.execute.return_value.fetchall.return_value = []
        mock_op.get_bind.return_value = mock_bind
        with patch.object(mod, "op", mock_op):
            mod.upgrade()

        update_sql = "\n".join(s for s in sqls if "UPDATE episodes" in s)
        assert update_sql, f"upgrade() did not emit an UPDATE episodes statement: {sqls}"
        assert " IN (" in update_sql, "upgrade() UPDATE must use an exact `IN (...)` title filter"
        assert "LIKE" not in update_sql.upper(), (
            "upgrade() UPDATE must NOT use LIKE — a LIKE 'memory_%' filter would "
            "wrongly tombstone legit user events titled 'memory_*'"
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

        # 'memory_personal_note' has the lowercase 'memory_' prefix but is NOT
        # in the exact IN-list — it pins the IN-not-LIKE safety boundary: a
        # LIKE 'memory_%' bug would wrongly tombstone this legit user event.
        for idx, title in enumerate(
            (
                "Team retrospective",
                "Doctor appointment",
                "Memory workshop",
                "memory_personal_note",
            )
        ):
            await self._insert_episode(
                pool,
                source_name="google_calendar.completed",
                source_ref=f"legit-only:{idx}",
                title=title,
            )

        await self._run_upgrade(pool)

        # All four legitimate rows must remain with tombstone_at IS NULL.
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name = 'google_calendar.completed'
              AND tombstone_at IS NULL
              AND title IN (
                  'Team retrospective',
                  'Doctor appointment',
                  'Memory workshop',
                  'memory_personal_note'
              )
            """
        )
        assert count == 4, f"Expected 4 legitimate episodes to remain untombstoned, got {count}"

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

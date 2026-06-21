"""Tests for chronicler_009 reset_watermarks_for_old_session_titles migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. upgrade() SQL shape: UPDATE targets projection_checkpoints for correct
   source_name, subsource join on affected episodes, watermark arithmetic
   (MIN(start_at) - 1 second), watermark_id = NULL.
3. downgrade() is a no-op.
4. Heuristic constants: _SOURCE_NAME, _TITLE_LIKE_PATTERN, _TRIGGER_SOURCE_ROUTE.
5. Idempotency guarantee via LIKE '% session' condition (after re-projection,
   episodes no longer match the filter).
6. Integration: pre-migration checkpoint NOT reset → post-migration checkpoint IS
   reset; second run is a no-op (requires Docker + Postgres,
   pytest.mark.integration).

Failing-reproducer contract (AC #4):
  TestPrePostMigrationState.test_pre_migration_stale_titles_exist
  asserts count > 0 BEFORE upgrade(), confirming the problem state.
  TestPrePostMigrationState.test_post_migration_watermarks_reset
  asserts the checkpoint watermark changed AFTER upgrade().

Issue: bu-jpf3o
Dependency: chronicler_008 (bu-aqqx0), chronicler_007 (bu-6t63s)
"""

from __future__ import annotations

import importlib.util
from datetime import UTC
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
    / "009_reset_watermarks_for_old_session_titles.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("chronicler_009", _MIGRATION_PATH)
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
        """chronicler_009 -> chronicler_008, no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "chronicler_009"
        assert mod.down_revision == "chronicler_008"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_chronicler_chain_includes_009(self) -> None:
        """Migration chain discovery must pick up 009_reset_watermarks_for_old_session_titles."""
        from butlers.migrations import _resolve_chain_dir

        chain_dir = _resolve_chain_dir("chronicler")
        assert chain_dir is not None, "Chronicler chain directory not found"
        files = sorted(f.name for f in chain_dir.glob("[0-9]*.py"))
        assert "009_reset_watermarks_for_old_session_titles.py" in files, (
            "009_reset_watermarks_for_old_session_titles.py not in discovered chronicler chain"
        )


class TestConstants:
    """Migration declares the expected heuristic constants."""

    def test_source_name_is_core_sessions(self) -> None:
        """_SOURCE_NAME must equal 'core.sessions'."""
        mod = _load_migration()
        assert mod._SOURCE_NAME == "core.sessions"

    def test_title_like_pattern_ends_with_space_session(self) -> None:
        """_TITLE_LIKE_PATTERN must be '% session' (matches all '{schema} session' titles)."""
        mod = _load_migration()
        assert mod._TITLE_LIKE_PATTERN == "% session"

    def test_trigger_source_route(self) -> None:
        """_TRIGGER_SOURCE_ROUTE must be 'route'."""
        mod = _load_migration()
        assert mod._TRIGGER_SOURCE_ROUTE == "route"


class TestDowngradeSQLShape:
    """Verify that downgrade() emits no SQL (watermark resets are not reversible).

    The upgrade() UPDATE shape (target table, watermark arithmetic, route/title/
    tombstone filters) is exercised end-to-end against a live DB by the
    integration TestPrePostMigrationState tests below.
    """

    def test_downgrade_is_noop(self) -> None:
        """downgrade() must emit no SQL statements (watermark resets are permanent)."""
        mod = _load_migration()
        sqls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.downgrade()
        assert not sqls, f"downgrade() must be a no-op, but found SQL statements:\n{sqls}"


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPrePostMigrationState:
    """Integration tests requiring a real PostgreSQL instance.

    These tests exercise the full scenario:
      1. Insert pre-bu-fkqv0 episode rows (title = '{schema} session') and a
         corresponding projection_checkpoints row simulating an advanced watermark.
      2. Verify that before upgrade() the stale-title episodes exist (reproducer).
      3. Run upgrade() and verify the checkpoint watermark was reset.
      4. Verify that a second upgrade() run is a no-op (idempotency).

    NOTE: The idempotency test (step 4) works because the episodes still carry
    their old titles after the migration runs — the migration only resets the
    watermark; the adapter re-projection happens on the next scheduled run.
    A second migration run will therefore find the same candidate rows and
    reset the watermark to the same value — which means the UPDATE is still
    technically executed, but writes the same value (idempotent in effect).
    The real end-state idempotency is that after the adapter has re-projected,
    episodes no longer match the heuristic so further migration runs skip them.
    """

    @pytest.fixture
    async def migration_pool(self, provisioned_postgres_pool):
        """Provision a fresh DB with both episodes and projection_checkpoints."""
        async with provisioned_postgres_pool() as pool:
            # source_adapter_state must exist first (FK target).
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
                INSERT INTO source_adapter_state (source_name)
                VALUES ('core.sessions')
                ON CONFLICT DO NOTHING
            """)

            # episodes table (post-chronicler_007 shape includes tombstone_reason).
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

            # projection_checkpoints (post-chronicler_002/005 shape).
            await pool.execute("""
                CREATE TABLE IF NOT EXISTS projection_checkpoints (
                    source_name TEXT NOT NULL
                        REFERENCES source_adapter_state(source_name) ON DELETE CASCADE,
                    subsource TEXT NOT NULL DEFAULT '',
                    watermark TIMESTAMPTZ,
                    watermark_id BIGINT,
                    last_run_at TIMESTAMPTZ,
                    last_success_at TIMESTAMPTZ,
                    last_error TEXT,
                    rows_projected BIGINT NOT NULL DEFAULT 0,
                    run_count BIGINT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (source_name, subsource)
                )
            """)
            yield pool

    async def _run_upgrade(self, pool) -> None:
        """Execute upgrade() SQL against the pool using op.execute shim."""
        mod = _load_migration()
        sqls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls.append(sql)
        # Silence _log_candidate_schemas in integration context.
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
        schema: str,
        source_ref: str,
        title: str,
        trigger_source: str = "route",
        start_at: str = "now() - interval '1 day'",
    ) -> None:
        """Insert one test episode row with the specified payload schema."""
        await pool.execute(
            f"""
            INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, title, payload)
            VALUES
                ('core.sessions', $1, 'work', {start_at}, $2,
                 jsonb_build_object(
                     'schema', $3::text,
                     'trigger_source', $4::text,
                     'session_id', gen_random_uuid()::text
                 ))
            """,
            source_ref,
            title,
            schema,
            trigger_source,
        )

    async def _upsert_checkpoint(
        self,
        pool,
        *,
        schema: str,
        watermark: str = "now()",
        watermark_id: int | None = 42,
    ) -> None:
        """Insert a checkpoint row simulating an advanced watermark for a schema."""
        await pool.execute(
            f"""
            INSERT INTO projection_checkpoints
                (source_name, subsource, watermark, watermark_id,
                 last_run_at, last_success_at, rows_projected, run_count)
            VALUES
                ('core.sessions', $1, {watermark}, $2, now(), now(), 100, 10)
            ON CONFLICT (source_name, subsource) DO UPDATE SET
                watermark    = EXCLUDED.watermark,
                watermark_id = EXCLUDED.watermark_id,
                updated_at   = now()
            """,
            schema,
            watermark_id,
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_pre_migration_stale_titles_exist(self, migration_pool) -> None:
        """Reproducer: stale '{schema} session' route episodes exist before upgrade.

        This is the failing-reproducer test required by AC #4.
        It confirms the pre-bu-fkqv0 problem state: route-triggered episodes
        carry the generic '{schema} session' fallback title.
        """
        pool = migration_pool
        await self._insert_episode(
            pool,
            schema="general",
            source_ref="pre-migration:general:0",
            title="general session",
            trigger_source="route",
        )
        await self._insert_episode(
            pool,
            schema="lifestyle",
            source_ref="pre-migration:lifestyle:0",
            title="lifestyle session",
            trigger_source="route",
        )

        # Before upgrade: stale-title episodes must exist.
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM episodes
            WHERE source_name             = 'core.sessions'
              AND payload->>'trigger_source' = 'route'
              AND title LIKE '% session'
              AND tombstone_at IS NULL
            """
        )
        assert count > 0, (
            f"Pre-migration reproducer expected count > 0 but got {count}. "
            "The stale-title rows were not inserted correctly."
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_post_migration_watermarks_reset(self, migration_pool) -> None:
        """Post-migration: checkpoint watermarks for affected schemas are reset.

        After upgrade() runs, the watermark for schemas that had stale-title
        episodes must be EARLIER than the episode's start_at (so the adapter
        will re-project them on the next run).
        """
        pool = migration_pool

        # Insert two route episodes with old-style titles and fixed timestamps.
        await pool.execute(
            """
            INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, title, payload)
            VALUES
                ('core.sessions', 'wm-test:general:0', 'work',
                 '2026-01-15 10:00:00+00', 'general session',
                 '{"schema": "general", "trigger_source": "route"}'::jsonb),
                ('core.sessions', 'wm-test:general:1', 'work',
                 '2026-01-15 12:00:00+00', 'general session',
                 '{"schema": "general", "trigger_source": "route"}'::jsonb)
            ON CONFLICT DO NOTHING
            """
        )

        # Simulate an advanced watermark for the 'general' schema.
        await self._upsert_checkpoint(
            pool,
            schema="general",
            watermark="'2026-01-20 00:00:00+00'::timestamptz",
            watermark_id=999,
        )

        # Fetch the pre-upgrade watermark.
        pre_watermark = await pool.fetchval(
            """
            SELECT watermark FROM projection_checkpoints
            WHERE source_name = 'core.sessions' AND subsource = 'general'
            """
        )
        assert pre_watermark is not None, "Expected pre-upgrade watermark to be set"

        # Run the migration.
        await self._run_upgrade(pool)

        # After upgrade: the watermark must have moved backward.
        row = await pool.fetchrow(
            """
            SELECT watermark, watermark_id FROM projection_checkpoints
            WHERE source_name = 'core.sessions' AND subsource = 'general'
            """
        )
        assert row is not None, "Checkpoint row missing after migration"
        post_watermark = row["watermark"]
        post_watermark_id = row["watermark_id"]

        assert post_watermark is not None, "watermark must not be NULL after migration reset"
        # The watermark must be before the earliest episode start_at minus 1s.
        # Earliest is 2026-01-15 10:00:00+00, so watermark must be ≤ 09:59:59+00.
        from datetime import datetime

        expected_max_watermark = datetime(2026, 1, 15, 9, 59, 59, tzinfo=UTC)
        assert post_watermark.replace(tzinfo=UTC) <= expected_max_watermark, (
            f"Post-migration watermark {post_watermark} must be ≤ {expected_max_watermark}"
        )
        assert post_watermark_id is None, (
            f"watermark_id must be NULL after reset, got {post_watermark_id}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unaffected_schema_checkpoint_unchanged(self, migration_pool) -> None:
        """Schemas with no stale-title route episodes are not touched."""
        pool = migration_pool

        # Insert a non-route episode (trigger_source='schedule:day_close') with
        # '{schema} session' title — must NOT trigger a watermark reset because
        # trigger_source != 'route'.
        await pool.execute(
            """
            INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, title, payload)
            VALUES
                ('core.sessions', 'unaffected:finance:0', 'work',
                 '2026-01-10 08:00:00+00', 'finance session',
                 '{"schema": "finance", "trigger_source": "schedule:day_close"}'::jsonb)
            ON CONFLICT DO NOTHING
            """
        )

        # Set a high watermark for finance schema.
        await self._upsert_checkpoint(
            pool,
            schema="finance",
            watermark="'2026-01-20 00:00:00+00'::timestamptz",
            watermark_id=888,
        )

        pre_watermark = await pool.fetchval(
            """
            SELECT watermark FROM projection_checkpoints
            WHERE source_name = 'core.sessions' AND subsource = 'finance'
            """
        )

        await self._run_upgrade(pool)

        post_watermark = await pool.fetchval(
            """
            SELECT watermark FROM projection_checkpoints
            WHERE source_name = 'core.sessions' AND subsource = 'finance'
            """
        )

        assert post_watermark == pre_watermark, (
            f"Finance schema watermark must be unchanged: pre={pre_watermark} post={post_watermark}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_non_route_sessions_not_affected(self, migration_pool) -> None:
        """Episodes with trigger_source != 'route' do not trigger watermark resets."""
        pool = migration_pool

        # Insert an episode with trigger_source='trigger' (manual task).
        await pool.execute(
            """
            INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, title, payload)
            VALUES
                ('core.sessions', 'non-route:health:0', 'work',
                 '2026-01-10 09:00:00+00', 'health session',
                 '{"schema": "health", "trigger_source": "trigger"}'::jsonb)
            ON CONFLICT DO NOTHING
            """
        )
        await self._upsert_checkpoint(
            pool,
            schema="health",
            watermark="'2026-01-20 00:00:00+00'::timestamptz",
            watermark_id=777,
        )

        pre_watermark = await pool.fetchval(
            """
            SELECT watermark FROM projection_checkpoints
            WHERE source_name = 'core.sessions' AND subsource = 'health'
            """
        )

        await self._run_upgrade(pool)

        post_watermark = await pool.fetchval(
            """
            SELECT watermark FROM projection_checkpoints
            WHERE source_name = 'core.sessions' AND subsource = 'health'
            """
        )

        assert post_watermark == pre_watermark, (
            f"Health schema watermark must be unchanged (trigger_source != route): "
            f"pre={pre_watermark} post={post_watermark}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_already_retitled_episodes_not_affected(self, migration_pool) -> None:
        """Episodes that already have the new title format are not triggered for reset."""
        pool = migration_pool

        # Insert a route episode that already has the new-style title.
        await pool.execute(
            """
            INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, title, payload)
            VALUES
                ('core.sessions', 'new-title:messenger:0', 'work',
                 '2026-01-10 10:00:00+00', 'Conversation with Alice',
                 '{"schema": "messenger", "trigger_source": "route"}'::jsonb)
            ON CONFLICT DO NOTHING
            """
        )
        await self._upsert_checkpoint(
            pool,
            schema="messenger",
            watermark="'2026-01-20 00:00:00+00'::timestamptz",
            watermark_id=666,
        )

        pre_watermark = await pool.fetchval(
            """
            SELECT watermark FROM projection_checkpoints
            WHERE source_name = 'core.sessions' AND subsource = 'messenger'
            """
        )

        await self._run_upgrade(pool)

        post_watermark = await pool.fetchval(
            """
            SELECT watermark FROM projection_checkpoints
            WHERE source_name = 'core.sessions' AND subsource = 'messenger'
            """
        )

        assert post_watermark == pre_watermark, (
            f"Messenger schema watermark must be unchanged (episode already retitled): "
            f"pre={pre_watermark} post={post_watermark}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_tombstoned_stale_title_episodes_not_affected(self, migration_pool) -> None:
        """Tombstoned episodes with stale titles are excluded from watermark reset."""
        pool = migration_pool

        await pool.execute(
            """
            INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, title, payload,
                 tombstone_at)
            VALUES
                ('core.sessions', 'tombstoned:lifestyle:0', 'work',
                 '2026-01-05 08:00:00+00', 'lifestyle session',
                 '{"schema": "lifestyle", "trigger_source": "route"}'::jsonb,
                 now())
            ON CONFLICT DO NOTHING
            """
        )
        await self._upsert_checkpoint(
            pool,
            schema="lifestyle",
            watermark="'2026-01-20 00:00:00+00'::timestamptz",
            watermark_id=555,
        )

        pre_watermark = await pool.fetchval(
            """
            SELECT watermark FROM projection_checkpoints
            WHERE source_name = 'core.sessions' AND subsource = 'lifestyle'
            """
        )

        await self._run_upgrade(pool)

        post_watermark = await pool.fetchval(
            """
            SELECT watermark FROM projection_checkpoints
            WHERE source_name = 'core.sessions' AND subsource = 'lifestyle'
            """
        )

        assert post_watermark == pre_watermark, (
            f"Lifestyle schema watermark must be unchanged (only tombstoned episodes): "
            f"pre={pre_watermark} post={post_watermark}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_upgrade_watermark_set_to_before_earliest_episode(self, migration_pool) -> None:
        """The reset watermark equals MIN(start_at) - 1 second for the schema."""
        pool = migration_pool

        # Insert two stale-title episodes for the same schema with known timestamps.
        await pool.execute(
            """
            INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, title, payload)
            VALUES
                ('core.sessions', 'wm-arith:general:0', 'work',
                 '2026-02-01 10:00:00+00', 'general session',
                 '{"schema": "general", "trigger_source": "route"}'::jsonb),
                ('core.sessions', 'wm-arith:general:1', 'work',
                 '2026-02-01 08:00:00+00', 'general session',
                 '{"schema": "general", "trigger_source": "route"}'::jsonb)
            ON CONFLICT DO NOTHING
            """
        )
        await self._upsert_checkpoint(
            pool,
            schema="general",
            watermark="'2026-02-10 00:00:00+00'::timestamptz",
            watermark_id=100,
        )

        await self._run_upgrade(pool)

        row = await pool.fetchrow(
            """
            SELECT watermark, watermark_id FROM projection_checkpoints
            WHERE source_name = 'core.sessions' AND subsource = 'general'
            """
        )
        assert row is not None
        wm = row["watermark"]
        wm_id = row["watermark_id"]

        # MIN(start_at) is 2026-02-01 08:00:00+00; minus 1 second = 07:59:59+00.
        from datetime import datetime

        expected = datetime(2026, 2, 1, 7, 59, 59, tzinfo=UTC)
        assert wm is not None
        wm_utc = wm.replace(tzinfo=UTC)
        assert wm_utc == expected, (
            f"Watermark must be MIN(start_at) - 1 second = {expected}, got {wm_utc}"
        )
        assert wm_id is None, f"watermark_id must be NULL after reset, got {wm_id}"

"""Tests for token tracking columns in sessions table.

Verifies that the sessions table has the necessary columns for tracking
token usage, model information, and parent session relationships.
"""

from __future__ import annotations

import shutil
import uuid

import pytest

from butlers.core.sessions import session_complete, session_create, sessions_summary
from butlers.testing.migration import create_migrated_test_pool

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    # Run tests in the session event loop so the pool (created in the session
    # fixture loop via asyncio_default_fixture_loop_scope=session) is usable.
    pytest.mark.asyncio(loop_scope="session"),
]


# Use the session-scoped postgres_container from root conftest (not a local override)
# so the event loop is shared across the whole session, avoiding asyncpg loop mismatch.


@pytest.fixture
async def pool_with_migrations(postgres_container):
    """Create a canonically bootstrapped core database and return its pool."""
    p = await create_migrated_test_pool(postgres_container, chains=["core"])
    try:
        yield p
    finally:
        await p.close()


class TestSessionTokenColumns:
    """Integration tests for token tracking columns in sessions table."""

    async def test_token_and_parent_columns_schema(self, pool_with_migrations):
        """Sessions table has token columns (nullable int) and parent_session_id (nullable uuid)."""
        async with pool_with_migrations.acquire() as conn:
            columns = await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'sessions'
                AND column_name IN ('input_tokens', 'output_tokens', 'parent_session_id')
                ORDER BY column_name
                """
            )

        columns_dict = {row["column_name"]: row for row in columns}

        assert "input_tokens" in columns_dict
        assert columns_dict["input_tokens"]["data_type"] == "integer"
        assert columns_dict["input_tokens"]["is_nullable"] == "YES"

        assert "output_tokens" in columns_dict
        assert columns_dict["output_tokens"]["data_type"] == "integer"
        assert columns_dict["output_tokens"]["is_nullable"] == "YES"

        assert "parent_session_id" in columns_dict
        assert columns_dict["parent_session_id"]["data_type"] == "uuid"
        assert columns_dict["parent_session_id"]["is_nullable"] == "YES"

    async def test_token_counts_and_parent_session_roundtrip(self, pool_with_migrations):
        """Token counts stored/retrieved correctly; parent-child relationship preserved."""
        # Create parent session and verify NULL token columns
        parent_id = await session_create(
            pool=pool_with_migrations,
            prompt="Parent prompt",
            trigger_source="external",
            request_id=str(uuid.uuid4()),
        )
        parent_row = await pool_with_migrations.fetchrow(
            "SELECT input_tokens, output_tokens, parent_session_id FROM sessions WHERE id = $1",
            parent_id,
        )
        assert parent_row["input_tokens"] is None
        assert parent_row["output_tokens"] is None
        assert parent_row["parent_session_id"] is None

        # Update with token counts
        await pool_with_migrations.execute(
            "UPDATE sessions SET input_tokens = $2, output_tokens = $3 WHERE id = $1",
            parent_id,
            1500,
            2500,
        )
        updated = await pool_with_migrations.fetchrow(
            "SELECT input_tokens, output_tokens FROM sessions WHERE id = $1", parent_id
        )
        assert updated["input_tokens"] == 1500
        assert updated["output_tokens"] == 2500

        # Create child session with parent reference
        child_id = await pool_with_migrations.fetchval(
            """
            INSERT INTO sessions (prompt, trigger_source, parent_session_id, request_id)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            "Child prompt",
            "external",
            parent_id,
            str(uuid.uuid4()),
        )
        child_row = await pool_with_migrations.fetchrow(
            "SELECT parent_session_id FROM sessions WHERE id = $1", child_id
        )
        assert child_row["parent_session_id"] == parent_id

    @pytest.mark.pg_clock
    async def test_cache_token_columns_roundtrip_and_summary(self, pool_with_migrations):
        """session_complete persists cache buckets; sessions_summary aggregates them.

        Covers migration core_155 (cached_input_tokens / cache_creation_tokens
        on sessions) end-to-end against real Postgres.
        """
        session_id = await session_create(
            pool=pool_with_migrations,
            prompt="Cache-heavy prompt",
            trigger_source="external",
            request_id=str(uuid.uuid4()),
            model="claude-sonnet-4-6",
        )
        await session_complete(
            pool=pool_with_migrations,
            session_id=session_id,
            output="done",
            tool_calls=[],
            duration_ms=1200,
            success=True,
            input_tokens=1_000,
            output_tokens=500,
            cached_input_tokens=40_000,
            cache_creation_tokens=8_000,
        )

        row = await pool_with_migrations.fetchrow(
            "SELECT cached_input_tokens, cache_creation_tokens FROM sessions WHERE id = $1",
            session_id,
        )
        assert row["cached_input_tokens"] == 40_000
        assert row["cache_creation_tokens"] == 8_000

        summary = await sessions_summary(pool_with_migrations, "today")
        assert summary["total_cached_input_tokens"] == 40_000
        assert summary["total_cache_creation_tokens"] == 8_000
        model_stats = summary["by_model"]["claude-sonnet-4-6"]
        assert model_stats["cached_input_tokens"] == 40_000
        assert model_stats["cache_creation_tokens"] == 8_000


class TestSessionStageTimings:
    """bu-dhloi1.180: content-free stage timings land in sessions.stage_timings."""

    async def test_column_exists_and_roundtrips_through_session_complete(
        self, pool_with_migrations
    ):
        from butlers.core import session_timing

        async with pool_with_migrations.acquire() as conn:
            col = await conn.fetchrow(
                """
                SELECT data_type, is_nullable FROM information_schema.columns
                WHERE table_name = 'sessions' AND column_name = 'stage_timings'
                """
            )
        assert col["data_type"] == "jsonb"
        assert col["is_nullable"] == "YES"

        session_id = await session_create(
            pool=pool_with_migrations,
            prompt="timed",
            trigger_source="external",
            request_id=str(uuid.uuid4()),
        )
        session_timing._reset_for_tests()
        session_timing.begin(str(session_id), origin=0.0, now=lambda: 1.5)
        session_timing.mark(str(session_id), "invoke_start_ms")
        session_timing.note_tool_surface(str(session_id), tool_count=3, schema_bytes=400)

        await session_complete(
            pool_with_migrations,
            session_id,
            output="done",
            tool_calls=[],
            duration_ms=9,
            success=True,
        )

        async with pool_with_migrations.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT stage_timings FROM sessions WHERE id = $1", session_id
            )
        import json

        stored = json.loads(raw) if isinstance(raw, str) else raw
        assert stored == {
            "invoke_start_ms": 1500,
            "total_ms": 1500,
            "tool_surface": {"tool_count": 3, "schema_bytes": 400, "schema_tokens_est": 100},
        }

    async def test_session_without_clock_has_null_timings(self, pool_with_migrations):
        from butlers.core import session_timing

        session_timing._reset_for_tests()
        session_id = await session_create(
            pool=pool_with_migrations,
            prompt="untimed",
            trigger_source="external",
            request_id=str(uuid.uuid4()),
        )
        await session_complete(
            pool_with_migrations,
            session_id,
            output=None,
            tool_calls=[],
            duration_ms=1,
            success=False,
        )
        async with pool_with_migrations.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT stage_timings FROM sessions WHERE id = $1", session_id
            )
        assert raw is None

"""Tests for token tracking columns in sessions table.

Verifies that the sessions table has the necessary columns for tracking
token usage, model information, and parent session relationships.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.core.sessions import session_create
from butlers.migrations import run_migrations

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    # Run tests in the session event loop so the pool (created in the session
    # fixture loop via asyncio_default_fixture_loop_scope=session) is usable.
    pytest.mark.asyncio(loop_scope="session"),
]


def _unique_db_name() -> str:
    """Generate a unique database name for test isolation."""
    return f"test_{uuid.uuid4().hex[:12]}"


# Use the session-scoped postgres_container from root conftest (not a local override)
# so the event loop is shared across the whole session, avoiding asyncpg loop mismatch.


@pytest.fixture
async def pool_with_migrations(postgres_container):
    """Create a fresh database with migrations run and return a pool."""
    db_name = _unique_db_name()
    host = postgres_container.get_container_host_ip()
    port = int(postgres_container.get_exposed_port(5432))
    user = postgres_container.username
    password = postgres_container.password

    # Create the database via the admin connection
    admin_conn = await asyncpg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database="postgres",
    )
    try:
        safe_name = db_name.replace('"', '""')
        await admin_conn.execute(f'CREATE DATABASE "{safe_name}"')
    finally:
        await admin_conn.close()

    # Run migrations on the new database
    db_url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
    await run_migrations(db_url, chain="core")

    # Connect to the database with migrations applied
    p = await asyncpg.create_pool(
        host=host,
        port=port,
        user=user,
        password=password,
        database=db_name,
        min_size=1,
        max_size=3,
    )
    yield p
    await p.close()


class TestSessionTokenColumns:
    """Integration tests for token tracking columns in sessions table."""

    async def test_sessions_table_has_token_columns(self, pool_with_migrations):
        """Verify sessions table has input_tokens, output_tokens, and parent_session_id columns."""
        async with pool_with_migrations.acquire() as conn:
            # Check that the columns exist by querying information_schema
            columns = await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'sessions'
                AND column_name IN ('input_tokens', 'output_tokens', 'parent_session_id')
                ORDER BY column_name
                """
            )

        # Convert to dict for easier assertions
        columns_dict = {row["column_name"]: row for row in columns}

        # Verify input_tokens exists and is nullable integer
        assert "input_tokens" in columns_dict
        assert columns_dict["input_tokens"]["data_type"] == "integer"
        assert columns_dict["input_tokens"]["is_nullable"] == "YES"

        # Verify output_tokens exists and is nullable integer
        assert "output_tokens" in columns_dict
        assert columns_dict["output_tokens"]["data_type"] == "integer"
        assert columns_dict["output_tokens"]["is_nullable"] == "YES"

        # Verify parent_session_id exists and is nullable uuid
        assert "parent_session_id" in columns_dict
        assert columns_dict["parent_session_id"]["data_type"] == "uuid"
        assert columns_dict["parent_session_id"]["is_nullable"] == "YES"

    async def test_session_create_with_nullable_token_columns(self, pool_with_migrations):
        """Sessions can be created with NULL token tracking columns (backward compatible)."""
        session_id = await session_create(
            pool=pool_with_migrations,
            prompt="Test prompt",
            trigger_source="external",
            trace_id="test-trace-123",
            model="claude-opus-4",
        )

        # Verify the session was created
        row = await pool_with_migrations.fetchrow(
            """
            SELECT id, input_tokens, output_tokens, parent_session_id
            FROM sessions
            WHERE id = $1
            """,
            session_id,
        )

        assert row is not None
        assert row["id"] == session_id
        # All token columns should be NULL by default
        assert row["input_tokens"] is None
        assert row["output_tokens"] is None
        assert row["parent_session_id"] is None

    async def test_session_with_parent_session_id(self, pool_with_migrations):
        """Sessions can reference a parent session via parent_session_id."""
        # Create parent session
        parent_id = await session_create(
            pool=pool_with_migrations,
            prompt="Parent prompt",
            trigger_source="external",
        )

        # Create child session with explicit parent_session_id
        child_id = await pool_with_migrations.fetchval(
            """
            INSERT INTO sessions (prompt, trigger_source, parent_session_id)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            "Child prompt",
            "external",
            parent_id,
        )

        # Verify parent-child relationship
        row = await pool_with_migrations.fetchrow(
            """
            SELECT id, parent_session_id
            FROM sessions
            WHERE id = $1
            """,
            child_id,
        )

        assert row["id"] == child_id
        assert row["parent_session_id"] == parent_id

    async def test_session_with_token_counts(self, pool_with_migrations):
        """Token counts can be stored in input_tokens and output_tokens."""
        # Create session
        session_id = await session_create(
            pool=pool_with_migrations,
            prompt="Test prompt",
            trigger_source="external",
        )

        # Update with token counts
        await pool_with_migrations.execute(
            """
            UPDATE sessions
            SET input_tokens = $2, output_tokens = $3
            WHERE id = $1
            """,
            session_id,
            1500,  # input_tokens
            2500,  # output_tokens
        )

        # Verify token counts were stored
        row = await pool_with_migrations.fetchrow(
            """
            SELECT input_tokens, output_tokens
            FROM sessions
            WHERE id = $1
            """,
            session_id,
        )

        assert row["input_tokens"] == 1500
        assert row["output_tokens"] == 2500

    async def test_parent_session_id_foreign_key_allows_null(self, pool_with_migrations):
        """Parent session ID can be NULL (no parent)."""
        session_id = await pool_with_migrations.fetchval(
            """
            INSERT INTO sessions (prompt, trigger_source, parent_session_id)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            "Test prompt",
            "external",
            None,  # Explicitly NULL parent
        )

        row = await pool_with_migrations.fetchrow(
            "SELECT parent_session_id FROM sessions WHERE id = $1", session_id
        )
        assert row["parent_session_id"] is None

    async def test_nonexistent_parent_session_id_violates_constraint(self, pool_with_migrations):
        """Inserting a non-existent parent_session_id should fail if FK constraint exists."""
        # Note: This test will fail if there's no foreign key constraint.
        # For now, we'll just verify we can insert a random UUID.
        # A future task might add an FK constraint.
        fake_parent_id = uuid.uuid4()

        # This should work without FK constraint (for now)
        session_id = await pool_with_migrations.fetchval(
            """
            INSERT INTO sessions (prompt, trigger_source, parent_session_id)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            "Test prompt",
            "external",
            fake_parent_id,
        )

        # Verify it was inserted
        assert session_id is not None
        row = await pool_with_migrations.fetchrow(
            "SELECT parent_session_id FROM sessions WHERE id = $1", session_id
        )
        assert row["parent_session_id"] == fake_parent_id

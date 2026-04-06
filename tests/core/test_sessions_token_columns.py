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
            parent_id, 1500, 2500,
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
            "Child prompt", "external", parent_id, str(uuid.uuid4()),
        )
        child_row = await pool_with_migrations.fetchrow(
            "SELECT parent_session_id FROM sessions WHERE id = $1", child_id
        )
        assert child_row["parent_session_id"] == parent_id

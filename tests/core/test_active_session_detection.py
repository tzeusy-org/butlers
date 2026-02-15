"""Tests for active session detection (butlers-26h.1.3).

Covers:
- sessions_active() returns only sessions with completed_at IS NULL
- sessions_active() returns empty list when no active sessions
- sessions_active() excludes completed sessions
- SpawnerResult includes session_id
- Spawner populates session_id on success and error
- Full flow: session is active before completion, inactive after
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.config import ButlerConfig, RuntimeConfig
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner, SpawnerResult

# Skip integration tests if Docker is not available
docker_available = shutil.which("docker") is not None


# ---------------------------------------------------------------------------
# Unit tests (no Docker needed)
# ---------------------------------------------------------------------------

pytestmark_unit = pytest.mark.unit


class MockAdapter(RuntimeAdapter):
    """Minimal mock adapter for unit tests."""

    def __init__(self, *, result_text: str = "", error: str | None = None):
        self._result_text = result_text
        self._error = error

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(self, prompt, system_prompt, mcp_servers, env, **kwargs):
        if self._error:
            raise RuntimeError(self._error)
        return self._result_text, [], None

    def build_config_file(self, mcp_servers, tmp_dir):
        return tmp_dir / "mock.json"

    def parse_system_prompt_file(self, config_dir):
        return ""


def _make_config(name: str = "test-butler", port: int = 9100) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        runtime=RuntimeConfig(),
        env_required=[],
        env_optional=[],
    )


@pytest.mark.unit
class TestSpawnerResultSessionId:
    """SpawnerResult.session_id field behavior."""

    def test_default_session_id_is_none(self):
        r = SpawnerResult()
        assert r.session_id is None

    def test_session_id_set_explicitly(self):
        sid = uuid.uuid4()
        r = SpawnerResult(session_id=sid)
        assert r.session_id == sid


@pytest.mark.unit
class TestSpawnerSessionIdPopulation:
    """Spawner populates session_id in SpawnerResult."""

    async def test_session_id_set_on_success(self, tmp_path: Path):
        """On successful invocation with a pool, SpawnerResult.session_id is set."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        fake_session_id = uuid.UUID("00000000-0000-0000-0000-000000000042")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = fake_session_id
            adapter = MockAdapter(result_text="ok")
            spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

            result = await spawner.trigger("test", "tick")

        assert result.session_id == fake_session_id
        assert result.success is True

    async def test_session_id_set_on_error(self, tmp_path: Path):
        """On failed invocation with a pool, SpawnerResult.session_id is still set."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        fake_session_id = uuid.UUID("00000000-0000-0000-0000-000000000043")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            mock_create.return_value = fake_session_id
            adapter = MockAdapter(error="boom")
            spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

            result = await spawner.trigger("test", "tick")

        assert result.session_id == fake_session_id
        assert result.success is False

    async def test_session_id_none_without_pool(self, tmp_path: Path):
        """Without a pool, session_id remains None."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(result_text="no pool")
        spawner = Spawner(config=config, config_dir=config_dir, pool=None, runtime=adapter)

        result = await spawner.trigger("test", "tick")

        assert result.session_id is None
        assert result.success is True


# ---------------------------------------------------------------------------
# Integration tests (require Docker for PostgreSQL)
# ---------------------------------------------------------------------------


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    if not docker_available:
        pytest.skip("Docker not available")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as postgres:
        yield postgres


@pytest.fixture
async def pool(postgres_container):
    """Create a fresh database with the sessions table and return a pool.

    WARNING: This fixture duplicates the 'sessions' table schema. If you update
    the schema via migrations, you MUST update it here as well to prevent
    schema drift in tests.
    """
    db_name = _unique_db_name()

    admin_conn = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database="postgres",
    )
    try:
        safe_name = db_name.replace('"', '""')
        await admin_conn.execute(f'CREATE DATABASE "{safe_name}"')
    finally:
        await admin_conn.close()

    p = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=3,
    )
    await p.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prompt TEXT NOT NULL,
            trigger_source TEXT NOT NULL,
            result TEXT,
            tool_calls JSONB NOT NULL DEFAULT '[]',
            duration_ms INTEGER,
            trace_id TEXT,
            model TEXT,
            cost JSONB,
            success BOOLEAN,
            error TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            input_tokens INTEGER,
            output_tokens INTEGER,
            parent_session_id UUID REFERENCES sessions(id),
            request_id TEXT
        )
    """)
    yield p
    await p.close()


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
class TestSessionsActive:
    """Integration tests for sessions_active() query."""

    async def test_newly_created_session_is_active(self, pool):
        """A session created via session_create has completed_at IS NULL and appears in active."""
        from butlers.core.sessions import session_create, sessions_active

        sid = await session_create(pool, prompt="active test", trigger_source="tick")
        active = await sessions_active(pool)

        active_ids = [s["id"] for s in active]
        assert sid in active_ids

        # Verify the active session has completed_at = None
        match = next(s for s in active if s["id"] == sid)
        assert match["completed_at"] is None

    async def test_completed_session_not_in_active(self, pool):
        """After session_complete, the session no longer appears in active list."""
        from butlers.core.sessions import session_complete, session_create, sessions_active

        sid = await session_create(pool, prompt="will complete", trigger_source="tick")

        # Before completion, should be active
        active_before = await sessions_active(pool)
        assert sid in [s["id"] for s in active_before]

        # Complete the session
        await session_complete(
            pool, sid, output="done", tool_calls=[], duration_ms=100, success=True
        )

        # After completion, should not be active
        active_after = await sessions_active(pool)
        assert sid not in [s["id"] for s in active_after]

    async def test_active_returns_empty_when_none_active(self, pool):
        """sessions_active returns empty list when all sessions are completed."""
        from butlers.core.sessions import session_complete, session_create, sessions_active

        sid = await session_create(pool, prompt="complete me", trigger_source="external")
        await session_complete(pool, sid, output="ok", tool_calls=[], duration_ms=50, success=True)

        # Create and complete another
        sid2 = await session_create(pool, prompt="also complete", trigger_source="external")
        await session_complete(
            pool, sid2, output="ok too", tool_calls=[], duration_ms=30, success=True
        )

        active = await sessions_active(pool)
        # Filter to only the sessions we created (there may be leftovers from other tests)
        our_active = [s for s in active if s["id"] in (sid, sid2)]
        assert len(our_active) == 0

    async def test_active_mixed_states(self, pool):
        """Only incomplete sessions appear in sessions_active, completed ones do not."""
        from butlers.core.sessions import session_complete, session_create, sessions_active

        # Create 3 sessions
        active_sid = await session_create(pool, prompt="still running", trigger_source="tick")
        done_sid = await session_create(pool, prompt="finished", trigger_source="external")
        failed_sid = await session_create(pool, prompt="crashed", trigger_source="trigger")

        # Complete 2 of them
        await session_complete(
            pool, done_sid, output="done", tool_calls=[], duration_ms=100, success=True
        )
        await session_complete(
            pool,
            failed_sid,
            output=None,
            tool_calls=[],
            duration_ms=200,
            success=False,
            error="crash",
        )

        active = await sessions_active(pool)
        active_ids = [s["id"] for s in active]

        assert active_sid in active_ids
        assert done_sid not in active_ids
        assert failed_sid not in active_ids

    async def test_active_sessions_ordered_by_started_at_desc(self, pool):
        """Active sessions are returned most-recent-first."""
        import asyncio

        from butlers.core.sessions import session_create, sessions_active

        sids = []
        for i in range(3):
            sid = await session_create(pool, prompt=f"order-{i}", trigger_source="tick")
            sids.append(sid)
            await asyncio.sleep(0.01)  # Ensure distinct started_at

        active = await sessions_active(pool)
        active_ids = [s["id"] for s in active]

        # Filter to our sessions
        our_order = [sid for sid in active_ids if sid in sids]
        # Most recent (sids[2]) should come first
        assert our_order.index(sids[2]) < our_order.index(sids[1])
        assert our_order.index(sids[1]) < our_order.index(sids[0])

    async def test_active_session_has_expected_fields(self, pool):
        """Active session records contain all expected fields."""
        from butlers.core.sessions import session_create, sessions_active

        sid = await session_create(
            pool, prompt="fields test", trigger_source="tick", trace_id="trace-42"
        )
        active = await sessions_active(pool)
        match = next(s for s in active if s["id"] == sid)

        expected_keys = {
            "id",
            "prompt",
            "trigger_source",
            "result",
            "tool_calls",
            "duration_ms",
            "trace_id",
            "model",
            "cost",
            "success",
            "error",
            "request_id",
            "started_at",
            "completed_at",
        }
        assert set(match.keys()) == expected_keys
        assert match["prompt"] == "fields test"
        assert match["trace_id"] == "trace-42"
        assert match["completed_at"] is None

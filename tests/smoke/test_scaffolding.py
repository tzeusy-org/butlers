"""Smoke scaffold — verifies the smoke tier infrastructure itself.

This file serves two purposes:
1. Demonstrate the canonical smoke test pattern (MockSpawner + postgres_container).
2. Catch immediate bootstrap regressions (broken imports, DB connectivity) before
   the full suite runs.

Real feature smoke tests live in sibling files (test_clean_start.py,
test_migrations.py, test_daemon_lifecycle.py, test_route_inbox.py).
"""

from __future__ import annotations

import shutil

import pytest

from butlers.testing.shared_fixtures import MockSpawner, SpawnerResult

pytestmark = pytest.mark.smoke

_docker_available = shutil.which("docker") is not None


# ---------------------------------------------------------------------------
# MockSpawner smoke — no Docker, no LLM
# ---------------------------------------------------------------------------


async def test_mock_spawner_intercepts_without_llm(mock_spawner: MockSpawner):
    """MockSpawner.spawn() resolves enqueued results without calling a real LLM.

    This is the invariant that every smoke test depends on: spawner calls are
    intercepted by the mock, never forwarded to the real LLM CLI subprocess.
    """
    mock_spawner.enqueue_result(SpawnerResult(output="smoke ok", success=True))
    result = await mock_spawner.spawn(prompt="smoke test prompt")

    assert result.success is True, "enqueued result should be returned"
    assert result.output == "smoke ok"
    assert len(mock_spawner.invocations) == 1, "spawn call should be recorded"
    assert mock_spawner.invocations[0]["prompt"] == "smoke test prompt"


async def test_mock_spawner_default_result_is_empty(mock_spawner: MockSpawner):
    """MockSpawner returns an empty default result when no result is enqueued.

    Smoke tests that do not enqueue an explicit result still get a valid
    SpawnerResult object, preventing AttributeError cascades.
    """
    result = await mock_spawner.spawn(prompt="no enqueue")

    # Default SpawnerResult has success=False and no output.
    assert result is not None
    assert result.success is False
    assert result.output is None


# ---------------------------------------------------------------------------
# postgres_container smoke — requires Docker
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
def test_postgres_container_reachable(postgres_container):
    """Session-scoped postgres_container provides a reachable PostgreSQL instance.

    This confirms the shared DB fixture the rest of the smoke suite depends on
    is healthy.  If this test fails, DB-backed smoke tests will also fail.
    """
    from sqlalchemy import create_engine, text

    connection_url = postgres_container.get_connection_url()
    engine = create_engine(connection_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1, "postgres_container should respond to SELECT 1"
    finally:
        engine.dispose()


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
def test_smoke_db_url_provisions_fresh_database(smoke_db_url: str):
    """smoke_db_url provisions a unique, extension-ready database for each module.

    Checks that the URL is a valid postgresql:// address and that the
    extensions bootstrapped by ``create_migration_db`` are installed.
    """
    from sqlalchemy import create_engine, text

    assert smoke_db_url.startswith("postgresql://"), "smoke_db_url must be a PostgreSQL URL"

    engine = create_engine(smoke_db_url)
    with engine.connect() as conn:
        # Verify pgvector extension was bootstrapped.
        result = conn.execute(text("SELECT extname FROM pg_extension WHERE extname = 'vector'"))
        row = result.fetchone()
        assert row is not None, "pgvector extension should be installed in smoke_db_url database"
    engine.dispose()

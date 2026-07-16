"""Real-Postgres regressions for optional-schema lifecycle classification.

The dashboard may query a schema whose optional tables were deliberately never
installed.  That absence is normal.  It must not, however, make an
``UndefinedTableError`` from a table that existed while the dashboard was
running look normal: a post-startup ``DROP TABLE`` is an operationally
degraded source.

These tests use the real core and memory migrations plus actual ``DROP TABLE``
statements.  They intentionally exercise the same asyncpg failures that the
secrets inventory and memory fan-out classifiers receive in production.
"""

from __future__ import annotations

import logging
import shutil
from urllib.parse import urlparse

import pytest

from butlers.api.db import DatabaseManager
from butlers.api.degraded import DegradedSources
from butlers.api.routers.memory import _fan_out_memory_queries
from butlers.api.routers.secrets_v2 import _fetch_system_secrets
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_LOGGER = logging.getLogger("test_optional_schema_lifecycle_db")
_TRACKED_RELATIONS = ("butler_secrets", "episodes", "facts", "rules")


@pytest.fixture
def migrated_db_url(postgres_container) -> str:
    """Provision one schema with the real core and memory tables."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
        schemas={"core": "lifecycle", "memory": "lifecycle"},
    )


async def _manager_for_schema(db_url: str, *, butler_name: str, schema: str) -> DatabaseManager:
    """Create a real dashboard pool scoped to *schema*."""
    parsed = urlparse(db_url)
    manager = DatabaseManager(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "postgres",
        min_pool_size=1,
        max_pool_size=2,
    )
    await manager.add_butler(
        butler_name,
        db_name=parsed.path.lstrip("/"),
        db_schema=schema,
    )
    return manager


async def _memory_fact_count(_: str, pool: object) -> int:
    """A real memory-table query used by the fan-out lifecycle assertions."""
    return await pool.fetchval("SELECT count(*) FROM facts") or 0  # type: ignore[attr-defined]


async def test_absent_at_startup_is_an_expected_optional_schema(
    migrated_db_url: str,
) -> None:
    """A never-migrated schema is skipped by both surfaces without degradation."""
    bootstrap = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    manager: DatabaseManager | None = None
    try:
        assert await bootstrap.pool("lifecycle").fetchval(
            "SELECT to_regclass('public.butler_secrets') IS NOT NULL"
        )
        await bootstrap.pool("lifecycle").execute("CREATE SCHEMA never_installed")
        manager = await _manager_for_schema(
            migrated_db_url,
            butler_name="never_installed",
            schema="never_installed",
        )
        await manager.snapshot_relation_presence("never_installed", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("never_installed", "butler_secrets") is False

        secrets_tracker = DegradedSources(_LOGGER)
        secret_rows = await _fetch_system_secrets(
            manager.pool("never_installed"),
            "never_installed",
            source_schema=manager.schema_for_butler("never_installed"),
            schema_absent_at_start=(
                manager.relation_observed_since_start("never_installed", "butler_secrets") is False
            ),
            tracker=secrets_tracker,
        )
        memory_tracker = DegradedSources(_LOGGER)
        memory_rows = await _fan_out_memory_queries(
            manager,
            query_name="lifecycle-absence",
            query_fn=_memory_fact_count,
            tracker=memory_tracker,
        )

        assert secret_rows == []
        assert secrets_tracker.failed is False
        assert memory_rows == []
        assert memory_tracker.failed is False
    finally:
        if manager is not None:
            await manager.close()
        await bootstrap.close()


async def test_dropped_after_startup_is_a_degraded_source(
    migrated_db_url: str,
) -> None:
    """Real post-startup drops surface on both secrets and memory trackers."""
    manager = await _manager_for_schema(
        migrated_db_url,
        butler_name="lifecycle",
        schema="lifecycle",
    )
    try:
        await manager.snapshot_relation_presence("lifecycle", _TRACKED_RELATIONS)
        assert manager.relation_observed_since_start("lifecycle", "butler_secrets") is True
        assert manager.relation_observed_since_start("lifecycle", "facts") is True

        pool = manager.pool("lifecycle")
        assert await pool.fetchval("SELECT to_regclass('public.butler_secrets') IS NOT NULL")
        await pool.execute("DROP TABLE butler_secrets")
        await pool.execute("DROP TABLE facts")

        secrets_tracker = DegradedSources(_LOGGER)
        secret_rows = await _fetch_system_secrets(
            pool,
            "lifecycle",
            source_schema=manager.schema_for_butler("lifecycle"),
            schema_absent_at_start=(
                manager.relation_observed_since_start("lifecycle", "butler_secrets") is False
            ),
            tracker=secrets_tracker,
        )
        memory_tracker = DegradedSources(_LOGGER)
        memory_rows = await _fan_out_memory_queries(
            manager,
            query_name="lifecycle-drop",
            query_fn=_memory_fact_count,
            tracker=memory_tracker,
        )

        assert secret_rows == []
        assert secrets_tracker.names == ["lifecycle"]
        assert memory_rows == []
        assert memory_tracker.names == ["lifecycle"]
    finally:
        await manager.close()

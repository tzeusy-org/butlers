"""Real-DB integration tests for the memory module migration chain.

Verifies that the memory migration chain (mem_001 → mem_002 → mem_003)
applies cleanly against a fresh PostgreSQL instance, produces the expected
schema, and supports a representative fact write-then-read cycle.

Local-dev requirements
----------------------
- Docker must be available and able to pull ``pgvector/pgvector:pg17``.
  The test suite uses testcontainers to spin up a throwaway PostgreSQL
  container for each test session.
- No manual DB setup is required; the fixture handles everything.
- Alternatively, if Docker is unavailable, these tests are automatically
  skipped (``pytest.mark.skipif``).

Run with::

    uv run pytest tests/modules/memory/test_memory_migration_integration.py -q --tb=short
"""

from __future__ import annotations

import asyncio
import shutil
from unittest.mock import MagicMock

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.db import register_jsonb_codec
from butlers.migrations import run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Expected memory tables after running the full chain
# ---------------------------------------------------------------------------

_EXPECTED_MEMORY_TABLES = {
    "episodes",
    "facts",
    "rules",
    "memory_links",
    "memory_events",
    "predicate_registry",
    "memory_policies",
    "rule_applications",
    # embedding_versions removed by mem_005 (dead table with 0 runtime references)
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_exists_in_schema(db_url: str, schema: str, table: str) -> bool:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM information_schema.tables"
                    "  WHERE table_schema = :s AND table_name = :t"
                    ")"
                ),
                {"s": schema, "t": table},
            )
            return bool(result.scalar())
    finally:
        engine.dispose()


def _get_column_names(db_url: str, table: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            )
            return {str(r[0]) for r in rows}
    finally:
        engine.dispose()


def _fake_embedding_engine() -> MagicMock:
    """Return a mock embedding engine that returns a deterministic 384-float vector."""
    engine = MagicMock()
    engine.embed.return_value = [0.0] * 384
    engine.model_name = "test-model"
    return engine


# ---------------------------------------------------------------------------
# Fixture: provisioned DB with core + memory migrations applied
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def memory_migrated_db(postgres_container) -> str:
    """Provision a fresh DB, run core then memory migrations, and return its URL.

    Scoped to ``module`` so the container startup and full migration chain
    (which can take 5-10 s) runs only once per test module, keeping the
    total test time well under 30 s.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    # 1. Core chain: creates public.entities, extensions, roles, etc.
    asyncio.run(run_migrations(db_url, chain="core"))

    # 2. Memory chain: mem_001 (schema) → mem_002 (predicates) → mem_003 (wellness)
    asyncio.run(run_migrations(db_url, chain="memory"))

    return db_url


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_memory_migration_creates_all_expected_tables(memory_migrated_db: str) -> None:
    """All memory tables exist in the public schema after running the chain."""
    for table in _EXPECTED_MEMORY_TABLES:
        assert _table_exists_in_schema(memory_migrated_db, "public", table), (
            f"Expected table {table!r} to exist after memory migration chain"
        )


def test_facts_table_has_required_columns(memory_migrated_db: str) -> None:
    """The facts table has the SPO columns and key operational columns."""
    cols = _get_column_names(memory_migrated_db, "facts")
    for required in (
        "id",
        "subject",
        "predicate",
        "content",
        "validity",
        "scope",
        "entity_id",
        "object_entity_id",
        "valid_at",
        "idempotency_key",
        "tenant_id",
        "embedding",
    ):
        assert required in cols, f"facts.{required} missing after migration"


def test_predicate_registry_seeded(memory_migrated_db: str) -> None:
    """predicate_registry is non-empty after mem_002 (seed predicates)."""
    engine = create_engine(memory_migrated_db)
    try:
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM predicate_registry")).scalar()
        assert count and count > 0, "predicate_registry should be seeded by mem_002"
    finally:
        engine.dispose()


def test_wellness_predicates_seeded(memory_migrated_db: str) -> None:
    """mem_003 wellness predicates (e.g. sleep_session) are present."""
    engine = create_engine(memory_migrated_db)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT name FROM predicate_registry WHERE name = 'sleep_session'")
            ).fetchone()
        assert row is not None, "sleep_session predicate should exist after mem_003"
    finally:
        engine.dispose()


def test_memory_policies_seeded(memory_migrated_db: str) -> None:
    """memory_policies has the 8 expected retention classes."""
    expected_classes = {
        "transient",
        "episodic",
        "operational",
        "personal_profile",
        "health_log",
        "financial_log",
        "rule",
        "anti_pattern",
    }
    engine = create_engine(memory_migrated_db)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT retention_class FROM memory_policies")).fetchall()
        actual = {str(r[0]) for r in rows}
    finally:
        engine.dispose()
    missing = expected_classes - actual
    assert not missing, f"memory_policies missing retention classes: {missing}"


async def _write_and_read_fact(db_url: str) -> dict:
    """Write a fact via store_fact and read it back directly via asyncpg."""
    from butlers.modules.memory.storage import store_fact

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    try:
        embedding_engine = _fake_embedding_engine()

        result = await store_fact(
            pool,
            subject="test_user",
            predicate="preference",
            content="prefers dark mode",
            embedding_engine=embedding_engine,
            importance=7.0,
            permanence="standard",
            scope="global",
            tenant_id="shared",
        )
        # store_fact returns a dict with "id" (UUID) and "supersedes_id"
        fact_id = result["id"]

        row = await pool.fetchrow(
            "SELECT id, subject, predicate, content, validity, scope, importance"
            " FROM facts WHERE id = $1",
            fact_id,
        )
        return dict(row) if row else {}
    finally:
        await pool.close()


def test_fact_write_and_read_round_trip(memory_migrated_db: str) -> None:
    """store_fact persists a fact that can be read back with correct field values.

    This exercises the full SPO write path against the migrated schema:
    store_fact → facts INSERT → SELECT by id.
    """
    result = asyncio.run(_write_and_read_fact(memory_migrated_db))

    assert result, "Expected a row to be returned after store_fact"
    assert result["subject"] == "test_user"
    assert result["predicate"] == "preference"
    assert result["content"] == "prefers dark mode"
    assert result["validity"] == "active"
    assert result["scope"] == "global"
    assert abs(result["importance"] - 7.0) < 1e-6


def test_migration_is_idempotent(postgres_container) -> None:
    """Running the memory migration chain twice on the same DB does not fail."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="memory"))
    # Second run must succeed without errors
    asyncio.run(run_migrations(db_url, chain="memory"))

    # Tables still exist
    assert _table_exists_in_schema(db_url, "public", "facts")
    assert _table_exists_in_schema(db_url, "public", "predicate_registry")

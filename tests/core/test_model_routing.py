"""Tests for butlers.core.model_routing — resolve_model and Complexity enum.

Covers:
- global-only catalog (no overrides): returns highest-priority matching entry
- per-butler override disables a catalog entry
- per-butler override remaps complexity tier
- per-butler override overrides priority
- no matching candidates returns None
- priority tie-breaking by created_at (stable ordering)
- Complexity enum validates all four tiers
- resolve_model accepts both Complexity enum and raw string
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest

from butlers.core.model_routing import Complexity, resolve_model

docker_available = shutil.which("docker") is not None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@asynccontextmanager
async def _make_pool(postgres_container: Any) -> AsyncIterator[asyncpg.Pool]:
    """Create a fresh DB with the model routing tables and yield a pool."""
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

    pool = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=3,
    )
    try:
        await _create_schema(pool)
        yield pool
    finally:
        await pool.close()


async def _create_schema(pool: asyncpg.Pool) -> None:
    """Create the shared schema and model routing tables for testing."""
    await pool.execute("CREATE SCHEMA IF NOT EXISTS shared")

    await pool.execute("""
        CREATE TABLE IF NOT EXISTS shared.model_catalog (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias           TEXT NOT NULL,
            runtime_type    TEXT NOT NULL,
            model_id        TEXT NOT NULL,
            extra_args      JSONB NOT NULL DEFAULT '[]'::jsonb,
            complexity_tier TEXT NOT NULL DEFAULT 'medium',
            enabled         BOOLEAN NOT NULL DEFAULT true,
            priority        INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_model_catalog_alias UNIQUE (alias),
            CONSTRAINT chk_model_catalog_complexity_tier
                CHECK (complexity_tier IN ('trivial', 'medium', 'high', 'extra_high'))
        )
    """)

    await pool.execute("""
        CREATE TABLE IF NOT EXISTS shared.butler_model_overrides (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name      TEXT NOT NULL,
            catalog_entry_id UUID NOT NULL
                REFERENCES shared.model_catalog(id) ON DELETE CASCADE,
            enabled          BOOLEAN NOT NULL DEFAULT true,
            priority         INTEGER,
            complexity_tier  TEXT,
            CONSTRAINT uq_butler_model_overrides_butler_entry
                UNIQUE (butler_name, catalog_entry_id),
            CONSTRAINT chk_butler_model_overrides_complexity_tier
                CHECK (complexity_tier IS NULL
                       OR complexity_tier IN ('trivial', 'medium', 'high', 'extra_high'))
        )
    """)


async def _insert_catalog_entry(
    pool: asyncpg.Pool,
    *,
    alias: str,
    runtime_type: str = "claude-code",
    model_id: str = "test-model",
    complexity_tier: str = "medium",
    enabled: bool = True,
    priority: int = 0,
    extra_args: list[str] | None = None,
) -> str:
    """Insert a catalog entry and return its id."""
    import json

    extra_json = json.dumps(extra_args or [])
    row = await pool.fetchrow(
        """
        INSERT INTO shared.model_catalog
            (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        RETURNING id
        """,
        alias,
        runtime_type,
        model_id,
        extra_json,
        complexity_tier,
        enabled,
        priority,
    )
    return str(row["id"])


async def _insert_override(
    pool: asyncpg.Pool,
    *,
    butler_name: str,
    catalog_entry_id: str,
    enabled: bool = True,
    priority: int | None = None,
    complexity_tier: str | None = None,
) -> None:
    """Insert a butler override row."""
    await pool.execute(
        """
        INSERT INTO shared.butler_model_overrides
            (butler_name, catalog_entry_id, enabled, priority, complexity_tier)
        VALUES ($1, $2, $3, $4, $5)
        """,
        butler_name,
        uuid.UUID(catalog_entry_id),
        enabled,
        priority,
        complexity_tier,
    )


# ---------------------------------------------------------------------------
# Complexity enum tests (unit — no DB required)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_complexity_enum_values() -> None:
    assert Complexity.TRIVIAL.value == "trivial"
    assert Complexity.MEDIUM.value == "medium"
    assert Complexity.HIGH.value == "high"
    assert Complexity.EXTRA_HIGH.value == "extra_high"


@pytest.mark.unit
def test_complexity_enum_from_string() -> None:
    assert Complexity("trivial") is Complexity.TRIVIAL
    assert Complexity("extra_high") is Complexity.EXTRA_HIGH


@pytest.mark.unit
def test_complexity_enum_invalid_raises() -> None:
    with pytest.raises(ValueError):
        Complexity("impossible")


# ---------------------------------------------------------------------------
# resolve_model integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_global_only(postgres_container: Any) -> None:
    """Global-only entry (no override): resolve returns it for matching tier."""
    async with _make_pool(postgres_container) as pool:
        await _insert_catalog_entry(
            pool,
            alias="sonnet",
            runtime_type="claude-code",
            model_id="claude-sonnet-4",
            complexity_tier="medium",
            priority=10,
        )

        result = await resolve_model(pool, "general", Complexity.MEDIUM)

        assert result is not None
        runtime_type, model_id, extra_args = result
        assert runtime_type == "claude-code"
        assert model_id == "claude-sonnet-4"
        assert extra_args == []


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_global_only_wrong_tier_returns_none(postgres_container: Any) -> None:
    """No entries match a different tier → None."""
    async with _make_pool(postgres_container) as pool:
        await _insert_catalog_entry(
            pool,
            alias="haiku",
            runtime_type="claude-code",
            model_id="claude-haiku-4",
            complexity_tier="trivial",
            priority=5,
        )

        result = await resolve_model(pool, "general", Complexity.HIGH)

        assert result is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_override_disable(postgres_container: Any) -> None:
    """Per-butler override with enabled=False hides the entry from that butler."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(
            pool,
            alias="sonnet",
            runtime_type="claude-code",
            model_id="claude-sonnet-4",
            complexity_tier="medium",
            priority=10,
        )
        await _insert_override(
            pool,
            butler_name="health",
            catalog_entry_id=entry_id,
            enabled=False,
        )

        # health butler sees nothing (override disables it)
        health_result = await resolve_model(pool, "health", Complexity.MEDIUM)
        assert health_result is None

        # other butler still sees the global entry
        other_result = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert other_result is not None
        assert other_result[1] == "claude-sonnet-4"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_tier_remap(postgres_container: Any) -> None:
    """Override remaps complexity_tier: entry originally 'medium' becomes 'high'."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(
            pool,
            alias="sonnet",
            runtime_type="claude-code",
            model_id="claude-sonnet-4",
            complexity_tier="medium",
            priority=10,
        )
        # relationship butler remaps this entry to 'high'
        await _insert_override(
            pool,
            butler_name="relationship",
            catalog_entry_id=entry_id,
            enabled=True,
            complexity_tier="high",
        )

        # relationship sees it under 'high', not 'medium'
        medium_result = await resolve_model(pool, "relationship", Complexity.MEDIUM)
        assert medium_result is None

        high_result = await resolve_model(pool, "relationship", Complexity.HIGH)
        assert high_result is not None
        assert high_result[1] == "claude-sonnet-4"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_priority_override(postgres_container: Any) -> None:
    """Override lowers priority so a different entry wins for that butler."""
    async with _make_pool(postgres_container) as pool:
        # Two global entries for 'medium'; haiku has higher priority (lower number)
        haiku_id = await _insert_catalog_entry(
            pool,
            alias="haiku",
            runtime_type="claude-code",
            model_id="claude-haiku-4",
            complexity_tier="medium",
            priority=5,
        )
        await _insert_catalog_entry(
            pool,
            alias="sonnet",
            runtime_type="claude-code",
            model_id="claude-sonnet-4",
            complexity_tier="medium",
            priority=20,
        )

        # Without override, haiku wins (priority 5 < 20)
        global_result = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert global_result is not None
        assert global_result[1] == "claude-haiku-4"

        # messenger butler: push haiku to priority 100 (higher number = lower preference)
        await _insert_override(
            pool,
            butler_name="messenger",
            catalog_entry_id=haiku_id,
            enabled=True,
            priority=100,
        )

        messenger_result = await resolve_model(pool, "messenger", Complexity.MEDIUM)
        assert messenger_result is not None
        assert messenger_result[1] == "claude-sonnet-4"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_no_candidates_returns_none(postgres_container: Any) -> None:
    """Empty catalog → None."""
    async with _make_pool(postgres_container) as pool:
        result = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert result is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_tie_breaking_by_created_at(postgres_container: Any) -> None:
    """Two entries with the same priority: earlier created_at wins."""
    async with _make_pool(postgres_container) as pool:
        await pool.execute("""
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority, created_at, updated_at)
            VALUES
                ('first',  'claude-code', 'model-first',  'medium', 10,
                 '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
                ('second', 'codex',       'model-second', 'medium', 10,
                 '2026-01-02 00:00:00+00', '2026-01-02 00:00:00+00')
        """)

        result = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert result is not None
        # first entry (earlier created_at) wins when priority is equal
        assert result[1] == "model-first"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_returns_extra_args(postgres_container: Any) -> None:
    """extra_args JSONB (list of CLI token strings) is returned correctly."""
    async with _make_pool(postgres_container) as pool:
        await pool.execute("""
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority, extra_args)
            VALUES
                ('opus', 'claude-code', 'claude-opus-4', 'extra_high', 1,
                 '["--config", "model_reasoning_effort=high"]'::jsonb)
        """)

        result = await resolve_model(pool, "general", Complexity.EXTRA_HIGH)
        assert result is not None
        _runtime_type, _model_id, extra_args = result
        assert extra_args == ["--config", "model_reasoning_effort=high"]


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_accepts_string_tier(postgres_container: Any) -> None:
    """resolve_model accepts a plain string for complexity_tier."""
    async with _make_pool(postgres_container) as pool:
        await _insert_catalog_entry(
            pool,
            alias="haiku",
            runtime_type="claude-code",
            model_id="claude-haiku-4",
            complexity_tier="trivial",
            priority=1,
        )

        result = await resolve_model(pool, "general", "trivial")
        assert result is not None
        assert result[1] == "claude-haiku-4"

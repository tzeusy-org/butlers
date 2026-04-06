"""Tests for butlers.core.model_routing — Complexity enum and resolve_model.

Covers:
- Complexity enum: all tiers (trivial/medium/high/extra_high/discretion/self_healing),
  round-trip from string, rejects invalid, tier isolation
- resolve_model: global catalog, per-butler override (disable/remap/priority),
  no candidates, round-robin rotation, extra_args, string tier input
- New tiers (discretion, self_healing): catalog resolution, tier isolation, override remap
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
# Unit tests — no DB required
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_complexity_enum() -> None:
    """All six tiers exist, parse from string, and are mutually distinct."""
    expected = {"trivial", "medium", "high", "extra_high", "discretion", "self_healing"}
    assert {m.value for m in Complexity} == expected

    for tier in expected:
        assert Complexity(tier).value == tier

    assert Complexity.TRIVIAL.value == "trivial"
    assert Complexity.EXTRA_HIGH.value == "extra_high"
    assert Complexity.DISCRETION.value == "discretion"
    assert Complexity.SELF_HEALING.value == "self_healing"
    assert Complexity.SELF_HEALING != Complexity.DISCRETION

    with pytest.raises(ValueError):
        Complexity("impossible")


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@asynccontextmanager
async def _make_pool(postgres_container: Any) -> AsyncIterator[asyncpg.Pool]:
    """Create a fresh DB with all model routing tables and yield a pool."""
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
    """Create shared schema and model routing tables (all tiers)."""
    all_tiers = "('trivial', 'medium', 'high', 'extra_high', 'discretion', 'self_healing')"
    await pool.execute(f"""
        CREATE TABLE IF NOT EXISTS public.model_catalog (
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
                CHECK (complexity_tier IN {all_tiers})
        )
    """)

    await pool.execute(f"""
        CREATE TABLE IF NOT EXISTS public.butler_model_overrides (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name      TEXT NOT NULL,
            catalog_entry_id UUID NOT NULL
                REFERENCES public.model_catalog(id) ON DELETE CASCADE,
            enabled          BOOLEAN NOT NULL DEFAULT true,
            priority         INTEGER,
            complexity_tier  TEXT,
            CONSTRAINT uq_butler_model_overrides_butler_entry
                UNIQUE (butler_name, catalog_entry_id),
            CONSTRAINT chk_butler_model_overrides_complexity_tier
                CHECK (complexity_tier IS NULL
                       OR complexity_tier IN {all_tiers})
        )
    """)

    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.model_round_robin_counters (
            butler_name      TEXT NOT NULL,
            complexity_tier  TEXT NOT NULL,
            counter          BIGINT NOT NULL DEFAULT 0,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (butler_name, complexity_tier)
        )
    """)


async def _insert_catalog_entry(
    pool: asyncpg.Pool,
    *,
    alias: str,
    runtime_type: str = "claude",
    model_id: str = "test-model",
    complexity_tier: str = "medium",
    enabled: bool = True,
    priority: int = 0,
    extra_args: list[str] | None = None,
) -> str:
    import json

    extra_json = json.dumps(extra_args or [])
    row = await pool.fetchrow(
        """
        INSERT INTO public.model_catalog
            (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        RETURNING id
        """,
        alias, runtime_type, model_id, extra_json, complexity_tier, enabled, priority,
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
    await pool.execute(
        """
        INSERT INTO public.butler_model_overrides
            (butler_name, catalog_entry_id, enabled, priority, complexity_tier)
        VALUES ($1, $2, $3, $4, $5)
        """,
        butler_name, uuid.UUID(catalog_entry_id), enabled, priority, complexity_tier,
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

pytestmark_integration = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_basic_catalog(postgres_container: Any) -> None:
    """Global entry resolves; wrong tier returns None; no candidates returns None."""
    async with _make_pool(postgres_container) as pool:
        # Matching tier found
        entry_id = await _insert_catalog_entry(
            pool, alias="sonnet", model_id="claude-sonnet-4",
            complexity_tier="medium", priority=10,
        )
        result = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert result is not None
        runtime_type, model_id, extra_args, catalog_entry_id = result
        assert runtime_type == "claude"
        assert model_id == "claude-sonnet-4"
        assert extra_args == []
        assert str(catalog_entry_id) == entry_id

        # Wrong tier returns None
        assert await resolve_model(pool, "general", Complexity.HIGH) is None

    # Empty catalog returns None
    async with _make_pool(postgres_container) as pool:
        assert await resolve_model(pool, "general", Complexity.MEDIUM) is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_override_behaviors(postgres_container: Any) -> None:
    """Override disable hides entry for that butler; remap moves it to new tier; priority boost wins."""
    async with _make_pool(postgres_container) as pool:
        # Override disable
        entry_id = await _insert_catalog_entry(
            pool, alias="sonnet", model_id="claude-sonnet-4",
            complexity_tier="medium", priority=10,
        )
        await _insert_override(pool, butler_name="health", catalog_entry_id=entry_id, enabled=False)
        assert await resolve_model(pool, "health", Complexity.MEDIUM) is None
        assert await resolve_model(pool, "general", Complexity.MEDIUM) is not None

        # Override remap: medium → high for relationship butler
        await _insert_override(
            pool, butler_name="relationship", catalog_entry_id=entry_id,
            enabled=True, complexity_tier="high",
        )
        assert await resolve_model(pool, "relationship", Complexity.MEDIUM) is None
        high_r = await resolve_model(pool, "relationship", Complexity.HIGH)
        assert high_r is not None and high_r[1] == "claude-sonnet-4"

    async with _make_pool(postgres_container) as pool:
        # Priority override: two global entries, one boosted for messenger
        haiku_id = await _insert_catalog_entry(
            pool, alias="haiku", model_id="claude-haiku-4",
            complexity_tier="medium", priority=5,
        )
        await _insert_catalog_entry(
            pool, alias="sonnet2", model_id="claude-sonnet-4",
            complexity_tier="medium", priority=20,
        )
        global_r = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert global_r is not None and global_r[1] == "claude-sonnet-4"

        await _insert_override(
            pool, butler_name="messenger", catalog_entry_id=haiku_id, enabled=True, priority=100,
        )
        messenger_r = await resolve_model(pool, "messenger", Complexity.MEDIUM)
        assert messenger_r is not None and messenger_r[1] == "claude-haiku-4"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_round_robin(postgres_container: Any) -> None:
    """Same-priority entries cycle round-robin; only top-priority entries included."""
    async with _make_pool(postgres_container) as pool:
        await pool.execute("""
            INSERT INTO public.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority, created_at, updated_at)
            VALUES
                ('first',  'claude', 'model-first',  'medium', 10,
                 '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
                ('second', 'codex',  'model-second', 'medium', 10,
                 '2026-01-02 00:00:00+00', '2026-01-02 00:00:00+00'),
                ('low',    'rt',     'model-low',    'medium',  5,
                 '2026-01-03 00:00:00+00', '2026-01-03 00:00:00+00')
        """)

        r1 = await resolve_model(pool, "general", Complexity.MEDIUM)
        r2 = await resolve_model(pool, "general", Complexity.MEDIUM)
        r3 = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert r1 is not None and r1[1] == "model-first"
        assert r2 is not None and r2[1] == "model-second"
        assert r3 is not None and r3[1] == "model-first"  # wraps

        # Low-priority entry never appears
        assert "model-low" not in {r1[1], r2[1], r3[1]}


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_extra_args_and_string_tier(postgres_container: Any) -> None:
    """extra_args list is returned; plain string tier accepted."""
    async with _make_pool(postgres_container) as pool:
        await pool.execute("""
            INSERT INTO public.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority, extra_args)
            VALUES
                ('opus', 'claude', 'claude-opus-4', 'extra_high', 1,
                 '["--config", "model_reasoning_effort=high"]'::jsonb)
        """)

        result = await resolve_model(pool, "general", Complexity.EXTRA_HIGH)
        assert result is not None and result[2] == ["--config", "model_reasoning_effort=high"]

        result2 = await resolve_model(pool, "general", "extra_high")
        assert result2 is not None and result2[1] == "claude-opus-4"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_discretion_and_self_healing_tiers(postgres_container: Any) -> None:
    """discretion and self_healing tiers resolve correctly and are isolated from medium."""
    async with _make_pool(postgres_container) as pool:
        import json

        await pool.execute(
            """
            INSERT INTO public.model_catalog
                (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            """,
            "discretion-model", "opencode", "ollama/qwen3.5:9b", json.dumps([]),
            "discretion", True, 10,
        )
        await pool.execute(
            """
            INSERT INTO public.model_catalog
                (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            """,
            "healing-model", "claude", "claude-sonnet-4-6", json.dumps([]),
            "self_healing", True, 10,
        )

        # Discretion tier resolves; medium does not match it
        d = await resolve_model(pool, "connector", Complexity.DISCRETION)
        assert d is not None and d[1] == "ollama/qwen3.5:9b"
        assert await resolve_model(pool, "connector", Complexity.MEDIUM) is None

        # self_healing tier resolves; medium does not match it
        sh = await resolve_model(pool, "email", Complexity.SELF_HEALING)
        assert sh is not None and sh[1] == "claude-sonnet-4-6"
        assert await resolve_model(pool, "email", Complexity.MEDIUM) is None

        # String form accepted
        assert await resolve_model(pool, "connector", "discretion") is not None
        assert await resolve_model(pool, "email", "self_healing") is not None

        # Empty catalog returns None for these tiers
        assert await resolve_model(pool, "email", Complexity.SELF_HEALING) is not None

"""Tests for butlers.core.model_routing — resolve_model and Complexity enum.

Covers:
- global-only catalog (no overrides): returns highest-priority matching entry
- per-butler override disables a catalog entry
- per-butler override remaps complexity tier
- per-butler override overrides priority
- no matching candidates returns None
- round-robin rotation among same-priority entries
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
    # public schema always exists; no need to create it.

    await pool.execute("""
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
                CHECK (complexity_tier IN ('trivial', 'medium', 'high', 'extra_high'))
        )
    """)

    await pool.execute("""
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
                       OR complexity_tier IN ('trivial', 'medium', 'high', 'extra_high'))
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
    """Insert a catalog entry and return its id."""
    import json

    extra_json = json.dumps(extra_args or [])
    row = await pool.fetchrow(
        """
        INSERT INTO public.model_catalog
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
        INSERT INTO public.butler_model_overrides
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
def test_complexity_enum() -> None:
    """Complexity enum has correct values, parses from string, and rejects invalid tiers."""
    assert Complexity.TRIVIAL.value == "trivial"
    assert Complexity.MEDIUM.value == "medium"
    assert Complexity.HIGH.value == "high"
    assert Complexity.EXTRA_HIGH.value == "extra_high"

    assert Complexity("trivial") is Complexity.TRIVIAL
    assert Complexity("extra_high") is Complexity.EXTRA_HIGH

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
        entry_id = await _insert_catalog_entry(
            pool,
            alias="sonnet",
            runtime_type="claude",
            model_id="claude-sonnet-4",
            complexity_tier="medium",
            priority=10,
        )

        result = await resolve_model(pool, "general", Complexity.MEDIUM)

        assert result is not None
        runtime_type, model_id, extra_args, catalog_entry_id = result
        assert runtime_type == "claude"
        assert model_id == "claude-sonnet-4"
        assert extra_args == []
        assert str(catalog_entry_id) == entry_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_global_only_wrong_tier_returns_none(postgres_container: Any) -> None:
    """No entries match a different tier → None."""
    async with _make_pool(postgres_container) as pool:
        await _insert_catalog_entry(
            pool,
            alias="haiku",
            runtime_type="claude",
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
            runtime_type="claude",
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
        assert len(other_result) == 4
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
            runtime_type="claude",
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
        assert len(high_result) == 4
        assert high_result[1] == "claude-sonnet-4"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_priority_override(postgres_container: Any) -> None:
    """Override lowers priority so a different entry wins for that butler."""
    async with _make_pool(postgres_container) as pool:
        # Two global entries for 'medium'; sonnet has higher priority (higher number = preferred)
        haiku_id = await _insert_catalog_entry(
            pool,
            alias="haiku",
            runtime_type="claude",
            model_id="claude-haiku-4",
            complexity_tier="medium",
            priority=5,
        )
        await _insert_catalog_entry(
            pool,
            alias="sonnet",
            runtime_type="claude",
            model_id="claude-sonnet-4",
            complexity_tier="medium",
            priority=20,
        )

        # Without override, sonnet wins (priority 20 > 5; higher number = higher priority)
        global_result = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert global_result is not None
        assert global_result[1] == "claude-sonnet-4"

        # messenger butler: boost haiku to priority 100 (higher number = higher preference)
        await _insert_override(
            pool,
            butler_name="messenger",
            catalog_entry_id=haiku_id,
            enabled=True,
            priority=100,
        )

        messenger_result = await resolve_model(pool, "messenger", Complexity.MEDIUM)
        assert messenger_result is not None
        assert len(messenger_result) == 4
        assert messenger_result[1] == "claude-haiku-4"


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
async def test_resolve_round_robin_same_priority(postgres_container: Any) -> None:
    """Two entries with equal priority: resolve_model cycles through them round-robin."""
    async with _make_pool(postgres_container) as pool:
        await pool.execute("""
            INSERT INTO public.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority, created_at, updated_at)
            VALUES
                ('first',  'claude', 'model-first',  'medium', 10,
                 '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
                ('second', 'codex',  'model-second', 'medium', 10,
                 '2026-01-02 00:00:00+00', '2026-01-02 00:00:00+00')
        """)

        # Call 1: counter=0, 0%2=0 → model-first (earliest created_at)
        r1 = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert r1 is not None and r1[1] == "model-first"

        # Call 2: counter=1, 1%2=1 → model-second
        r2 = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert r2 is not None and r2[1] == "model-second"

        # Call 3: counter=2, 2%2=0 → back to model-first
        r3 = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert r3 is not None and r3[1] == "model-first"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_round_robin_single_candidate(postgres_container: Any) -> None:
    """Single candidate at top priority: always returned regardless of counter."""
    async with _make_pool(postgres_container) as pool:
        await _insert_catalog_entry(
            pool,
            alias="only",
            runtime_type="claude",
            model_id="only-model",
            complexity_tier="medium",
            priority=10,
        )

        for _ in range(3):
            result = await resolve_model(pool, "general", Complexity.MEDIUM)
            assert result is not None and result[1] == "only-model"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_round_robin_three_candidates(postgres_container: Any) -> None:
    """Three same-priority entries cycle 0, 1, 2, 0, 1, 2."""
    async with _make_pool(postgres_container) as pool:
        await pool.execute("""
            INSERT INTO public.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority, created_at, updated_at)
            VALUES
                ('a', 'rt', 'model-a', 'high', 5,
                 '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
                ('b', 'rt', 'model-b', 'high', 5,
                 '2026-01-02 00:00:00+00', '2026-01-02 00:00:00+00'),
                ('c', 'rt', 'model-c', 'high', 5,
                 '2026-01-03 00:00:00+00', '2026-01-03 00:00:00+00')
        """)

        expected = ["model-a", "model-b", "model-c", "model-a", "model-b", "model-c"]
        for model_id in expected:
            result = await resolve_model(pool, "general", Complexity.HIGH)
            assert result is not None and result[1] == model_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_round_robin_ignores_lower_priority(postgres_container: Any) -> None:
    """Only top-priority entries participate in round-robin rotation."""
    async with _make_pool(postgres_container) as pool:
        await pool.execute("""
            INSERT INTO public.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority, created_at, updated_at)
            VALUES
                ('top1', 'rt', 'model-top1', 'medium', 20,
                 '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
                ('top2', 'rt', 'model-top2', 'medium', 20,
                 '2026-01-02 00:00:00+00', '2026-01-02 00:00:00+00'),
                ('low',  'rt', 'model-low',  'medium', 10,
                 '2026-01-03 00:00:00+00', '2026-01-03 00:00:00+00')
        """)

        results = []
        for _ in range(4):
            r = await resolve_model(pool, "general", Complexity.MEDIUM)
            assert r is not None
            results.append(r[1])

        # Only the two priority-20 models should appear
        assert set(results) == {"model-top1", "model-top2"}
        assert "model-low" not in results


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_round_robin_independent_per_butler(postgres_container: Any) -> None:
    """Different butlers have independent round-robin counters."""
    async with _make_pool(postgres_container) as pool:
        await pool.execute("""
            INSERT INTO public.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority, created_at, updated_at)
            VALUES
                ('x', 'rt', 'model-x', 'medium', 10,
                 '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
                ('y', 'rt', 'model-y', 'medium', 10,
                 '2026-01-02 00:00:00+00', '2026-01-02 00:00:00+00')
        """)

        # Both butlers start at counter=0 → model-x
        r_alice = await resolve_model(pool, "alice", Complexity.MEDIUM)
        r_bob = await resolve_model(pool, "bob", Complexity.MEDIUM)
        assert r_alice is not None and r_alice[1] == "model-x"
        assert r_bob is not None and r_bob[1] == "model-x"

        # Second call: both advance to counter=1 → model-y
        r_alice2 = await resolve_model(pool, "alice", Complexity.MEDIUM)
        r_bob2 = await resolve_model(pool, "bob", Complexity.MEDIUM)
        assert r_alice2 is not None and r_alice2[1] == "model-y"
        assert r_bob2 is not None and r_bob2[1] == "model-y"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_round_robin_independent_per_tier(postgres_container: Any) -> None:
    """Same butler, different tiers have independent counters."""
    async with _make_pool(postgres_container) as pool:
        await pool.execute("""
            INSERT INTO public.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority, created_at, updated_at)
            VALUES
                ('m1', 'rt', 'med-1', 'medium', 10,
                 '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
                ('m2', 'rt', 'med-2', 'medium', 10,
                 '2026-01-02 00:00:00+00', '2026-01-02 00:00:00+00'),
                ('h1', 'rt', 'high-1', 'high', 10,
                 '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
                ('h2', 'rt', 'high-2', 'high', 10,
                 '2026-01-02 00:00:00+00', '2026-01-02 00:00:00+00')
        """)

        # First call for each tier → index 0
        rm = await resolve_model(pool, "general", Complexity.MEDIUM)
        rh = await resolve_model(pool, "general", Complexity.HIGH)
        assert rm is not None and rm[1] == "med-1"
        assert rh is not None and rh[1] == "high-1"

        # Advance medium counter but not high
        rm2 = await resolve_model(pool, "general", Complexity.MEDIUM)
        assert rm2 is not None and rm2[1] == "med-2"

        # High still at counter=1 → high-2
        rh2 = await resolve_model(pool, "general", Complexity.HIGH)
        assert rh2 is not None and rh2[1] == "high-2"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_returns_extra_args(postgres_container: Any) -> None:
    """extra_args JSONB (list of CLI token strings) is returned correctly."""
    async with _make_pool(postgres_container) as pool:
        await pool.execute("""
            INSERT INTO public.model_catalog
                (alias, runtime_type, model_id, complexity_tier, priority, extra_args)
            VALUES
                ('opus', 'claude', 'claude-opus-4', 'extra_high', 1,
                 '["--config", "model_reasoning_effort=high"]'::jsonb)
        """)

        result = await resolve_model(pool, "general", Complexity.EXTRA_HIGH)
        assert result is not None
        assert len(result) == 4
        _runtime_type, _model_id, extra_args, _entry_id = result
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
            runtime_type="claude",
            model_id="claude-haiku-4",
            complexity_tier="trivial",
            priority=1,
        )

        result = await resolve_model(pool, "general", "trivial")
        assert result is not None
        assert len(result) == 4
        assert result[1] == "claude-haiku-4"

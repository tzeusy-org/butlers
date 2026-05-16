"""Tests for butlers.core.model_routing — Complexity enum and resolve_model.

Covers:
- Complexity enum: canonical six tiers (reasoning/workhorse/cheap/specialty/local/legacy),
  round-trip from string, rejects invalid, tier isolation
- resolve_model: global catalog, per-butler override (disable/remap/priority),
  no candidates, round-robin rotation, extra_args, string tier input
- §3.2 routing contract: tier fallthrough order, priority tie-break, state filter
- Deprecation shim: legacy vocabulary triggers loud warning and remaps
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.core.model_routing import (
    TIER_FALLTHROUGH_ORDER,
    Complexity,
    _check_deprecated_tier,
    resolve_model,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None

# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_complexity_enum() -> None:
    """Canonical six tiers exist, parse from string, and are mutually distinct."""
    expected = {"reasoning", "workhorse", "cheap", "specialty", "local", "legacy"}
    assert {m.value for m in Complexity} == expected

    for tier in expected:
        assert Complexity(tier).value == tier

    assert Complexity.REASONING.value == "reasoning"
    assert Complexity.WORKHORSE.value == "workhorse"
    assert Complexity.CHEAP.value == "cheap"
    assert Complexity.SPECIALTY.value == "specialty"
    assert Complexity.LOCAL.value == "local"
    assert Complexity.LEGACY.value == "legacy"
    assert Complexity.SPECIALTY != Complexity.WORKHORSE

    with pytest.raises(ValueError):
        Complexity("impossible")


@pytest.mark.unit
def test_tier_fallthrough_order() -> None:
    """Canonical fallthrough order is reasoning → workhorse → cheap → specialty → local → legacy."""
    assert TIER_FALLTHROUGH_ORDER == (
        "reasoning",
        "workhorse",
        "cheap",
        "specialty",
        "local",
        "legacy",
    )


@pytest.mark.unit
def test_deprecated_tier_shim_remaps_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Legacy tier values are remapped with a LOUD warning; unknown values pass through."""
    import logging

    with caplog.at_level(logging.WARNING, logger="butlers.core.model_routing"):
        assert _check_deprecated_tier("trivial") == "cheap"
        assert _check_deprecated_tier("medium") == "workhorse"
        assert _check_deprecated_tier("high") == "reasoning"
        assert _check_deprecated_tier("extra_high") == "reasoning"
        assert _check_deprecated_tier("discretion") == "specialty"
        assert _check_deprecated_tier("self_healing") == "specialty"

    assert len(caplog.records) == 6
    for record in caplog.records:
        assert "DEPRECATED" in record.message

    # Canonical values pass through unchanged with no warning
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="butlers.core.model_routing"):
        assert _check_deprecated_tier("reasoning") == "reasoning"
        assert _check_deprecated_tier("workhorse") == "workhorse"
    assert len(caplog.records) == 0


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    """Return an asyncpg pool with model routing tables cleared between tests."""
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    await p.execute(
        "TRUNCATE public.model_round_robin_counters, "
        "public.butler_model_overrides, public.model_catalog CASCADE"
    )
    yield p
    await p.close()


async def _insert_catalog_entry(
    pool: asyncpg.Pool,
    *,
    alias: str,
    runtime_type: str = "claude",
    model_id: str = "test-model",
    complexity_tier: str = "workhorse",
    enabled: bool = True,
    priority: int = 0,
    session_timeout_s: int = 1800,
    extra_args: list[str] | None = None,
) -> str:
    import json

    extra_json = json.dumps(extra_args or [])
    row = await pool.fetchrow(
        """
        INSERT INTO public.model_catalog
            (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority, session_timeout_s)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
        RETURNING id
        """,
        alias,
        runtime_type,
        model_id,
        extra_json,
        complexity_tier,
        enabled,
        priority,
        session_timeout_s,
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
        butler_name,
        uuid.UUID(catalog_entry_id),
        enabled,
        priority,
        complexity_tier,
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_basic_catalog(pool: asyncpg.Pool) -> None:
    """Global entry resolves; wrong tier returns None with no fallthrough; empty catalog returns None."""
    # Empty catalog returns None
    assert (
        await resolve_model(pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False)
        is None
    )

    # Matching tier found
    entry_id = await _insert_catalog_entry(
        pool,
        alias="sonnet",
        model_id="claude-sonnet-4",
        complexity_tier="workhorse",
        priority=10,
    )
    result = await resolve_model(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    assert result is not None
    runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s = result
    assert runtime_type == "claude"
    assert model_id == "claude-sonnet-4"
    assert extra_args == []
    assert str(catalog_entry_id) == entry_id
    assert session_timeout_s == 1800

    # Wrong tier returns None (no fallthrough)
    assert (
        await resolve_model(pool, "general", Complexity.REASONING, allow_tier_fallthrough=False)
        is None
    )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_returns_catalog_session_timeout(pool: asyncpg.Pool) -> None:
    """Resolved catalog rows include per-row session_timeout_s."""
    entry_id = await _insert_catalog_entry(
        pool,
        alias="timed-sonnet",
        model_id="claude-sonnet-4",
        complexity_tier="workhorse",
        session_timeout_s=2400,
    )
    result = await resolve_model(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    assert result is not None
    runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s = result
    assert runtime_type == "claude"
    assert model_id == "claude-sonnet-4"
    assert extra_args == []
    assert str(catalog_entry_id) == entry_id
    assert session_timeout_s == 2400


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_override_disable_and_remap(pool: asyncpg.Pool) -> None:
    """Override disable hides entry for that butler; remap moves it to new tier."""
    entry_id = await _insert_catalog_entry(
        pool,
        alias="sonnet",
        model_id="claude-sonnet-4",
        complexity_tier="workhorse",
        priority=10,
    )
    await _insert_override(pool, butler_name="health", catalog_entry_id=entry_id, enabled=False)
    assert (
        await resolve_model(pool, "health", Complexity.WORKHORSE, allow_tier_fallthrough=False)
        is None
    )
    assert (
        await resolve_model(pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False)
        is not None
    )

    # Override remap: workhorse → reasoning for relationship butler
    await _insert_override(
        pool,
        butler_name="relationship",
        catalog_entry_id=entry_id,
        enabled=True,
        complexity_tier="reasoning",
    )
    assert (
        await resolve_model(
            pool, "relationship", Complexity.WORKHORSE, allow_tier_fallthrough=False
        )
        is None
    )
    reasoning_r = await resolve_model(
        pool, "relationship", Complexity.REASONING, allow_tier_fallthrough=False
    )
    assert reasoning_r is not None and reasoning_r[1] == "claude-sonnet-4"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_override_priority_boost(pool: asyncpg.Pool) -> None:
    """Priority override boosts lower-priority entry above global default."""
    haiku_id = await _insert_catalog_entry(
        pool,
        alias="haiku",
        model_id="claude-haiku-4",
        complexity_tier="workhorse",
        priority=5,
    )
    await _insert_catalog_entry(
        pool,
        alias="sonnet2",
        model_id="claude-sonnet-4",
        complexity_tier="workhorse",
        priority=20,
    )
    global_r = await resolve_model(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    assert global_r is not None and global_r[1] == "claude-sonnet-4"

    await _insert_override(
        pool,
        butler_name="messenger",
        catalog_entry_id=haiku_id,
        enabled=True,
        priority=100,
    )
    messenger_r = await resolve_model(
        pool, "messenger", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    assert messenger_r is not None and messenger_r[1] == "claude-haiku-4"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_round_robin(pool: asyncpg.Pool) -> None:
    """Same-priority entries cycle round-robin; only top-priority entries included."""
    await pool.execute("""
        INSERT INTO public.model_catalog
            (alias, runtime_type, model_id, complexity_tier, priority, created_at, updated_at)
        VALUES
            ('first',  'claude', 'model-first',  'workhorse', 10,
             '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
            ('second', 'codex',  'model-second', 'workhorse', 10,
             '2026-01-02 00:00:00+00', '2026-01-02 00:00:00+00'),
            ('low',    'rt',     'model-low',    'workhorse',  5,
             '2026-01-03 00:00:00+00', '2026-01-03 00:00:00+00')
    """)

    r1 = await resolve_model(pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False)
    r2 = await resolve_model(pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False)
    r3 = await resolve_model(pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False)
    assert r1 is not None and r1[1] == "model-first"
    assert r2 is not None and r2[1] == "model-second"
    assert r3 is not None and r3[1] == "model-first"  # wraps

    # Low-priority entry never appears
    assert "model-low" not in {r1[1], r2[1], r3[1]}


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_extra_args_and_string_tier(pool: asyncpg.Pool) -> None:
    """extra_args list is returned; plain string tier accepted."""
    await pool.execute("""
        INSERT INTO public.model_catalog
            (alias, runtime_type, model_id, complexity_tier, priority, extra_args)
        VALUES
            ('opus', 'claude', 'claude-opus-4', 'reasoning', 1,
             '["--config", "model_reasoning_effort=high"]'::jsonb)
    """)

    result = await resolve_model(
        pool, "general", Complexity.REASONING, allow_tier_fallthrough=False
    )
    assert result is not None and result[2] == ["--config", "model_reasoning_effort=high"]

    result2 = await resolve_model(pool, "general", "reasoning", allow_tier_fallthrough=False)
    assert result2 is not None and result2[1] == "claude-opus-4"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_specialty_tier(pool: asyncpg.Pool) -> None:
    """specialty tier resolves correctly and is isolated from workhorse."""
    import json

    await pool.execute(
        """
        INSERT INTO public.model_catalog
            (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        """,
        "specialty-model",
        "opencode",
        "ollama/qwen3.5:9b",
        json.dumps([]),
        "specialty",
        True,
        10,
    )
    await pool.execute(
        """
        INSERT INTO public.model_catalog
            (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        """,
        "local-model",
        "claude",
        "claude-sonnet-4-6",
        json.dumps([]),
        "local",
        True,
        10,
    )

    # Specialty tier resolves; workhorse does not match it (no fallthrough)
    sp = await resolve_model(pool, "connector", Complexity.SPECIALTY, allow_tier_fallthrough=False)
    assert sp is not None and sp[1] == "ollama/qwen3.5:9b"
    assert (
        await resolve_model(pool, "connector", Complexity.WORKHORSE, allow_tier_fallthrough=False)
        is None
    )

    # local tier resolves
    lo = await resolve_model(pool, "email", Complexity.LOCAL, allow_tier_fallthrough=False)
    assert lo is not None and lo[1] == "claude-sonnet-4-6"

    # String form accepted
    assert (
        await resolve_model(pool, "connector", "specialty", allow_tier_fallthrough=False)
        is not None
    )
    assert await resolve_model(pool, "email", "local", allow_tier_fallthrough=False) is not None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_tier_fallthrough_order(pool: asyncpg.Pool) -> None:
    """§3.2: when requested tier has no entry, fall through to next canonical tier."""
    await _insert_catalog_entry(
        pool,
        alias="cheap-fallback",
        model_id="cheap-model",
        complexity_tier="cheap",
        priority=10,
    )

    # Requesting reasoning tier; no reasoning entry → falls through to workhorse → cheap
    result = await resolve_model(pool, "general", Complexity.REASONING, allow_tier_fallthrough=True)
    assert result is not None and result[1] == "cheap-model"

    # Requesting workhorse tier; falls through to cheap
    result2 = await resolve_model(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=True
    )
    assert result2 is not None and result2[1] == "cheap-model"

    # Requesting cheap tier; matches directly
    result3 = await resolve_model(pool, "general", Complexity.CHEAP, allow_tier_fallthrough=True)
    assert result3 is not None and result3[1] == "cheap-model"

    # Requesting specialty tier; no entry in specialty/local/legacy → None
    result4 = await resolve_model(
        pool, "general", Complexity.SPECIALTY, allow_tier_fallthrough=True
    )
    assert result4 is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_deprecated_string_tier_warns(
    pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    """Passing a legacy string tier to resolve_model triggers a deprecation warning."""
    import logging

    await _insert_catalog_entry(
        pool,
        alias="workhorse-model",
        model_id="workhorse-model-id",
        complexity_tier="workhorse",
        priority=10,
    )

    with caplog.at_level(logging.WARNING, logger="butlers.core.model_routing"):
        # "medium" maps to "workhorse" — should find the workhorse entry
        result = await resolve_model(pool, "general", "medium", allow_tier_fallthrough=False)

    assert result is not None and result[1] == "workhorse-model-id"
    assert any("DEPRECATED" in r.message for r in caplog.records)

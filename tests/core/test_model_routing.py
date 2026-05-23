"""Tests for butlers.core.model_routing — Complexity enum and resolve_model.

Covers:
- Complexity enum: canonical six tiers (reasoning/workhorse/cheap/specialty/local/legacy),
  round-trip from string, rejects invalid, tier isolation
- resolve_model: global catalog, per-butler override (disable/remap/priority),
  no candidates, round-robin rotation, extra_args, string tier input
- §3.2 routing contract: tier fallthrough order, priority tie-break, state filter
- Deprecation shim: legacy vocabulary triggers loud warning and remaps
- resolve_model_with_effective_tier: same semantics as resolve_model but includes effective tier
- next_same_tier_candidate: exact-tier failover candidates, exclusions, ordering, override semantics
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.core.model_routing import (
    _NEXT_SAME_TIER_SQL,
    _RESOLVE_SQL,
    TIER_FALLTHROUGH_ORDER,
    Complexity,
    _check_deprecated_tier,
    next_same_tier_candidate,
    resolve_model,
    resolve_model_with_effective_tier,
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


@pytest.mark.unit
def test_resolver_sql_excludes_failed_verification_rows() -> None:
    """The resolver must not dispatch models whose latest verification failed."""
    assert "mc.last_verified_ok IS DISTINCT FROM false" in _RESOLVE_SQL


@pytest.mark.unit
def test_next_same_tier_sql_excludes_failed_verification_rows() -> None:
    """The failover resolver SQL must also exclude failed-verification entries."""
    assert "mc.last_verified_ok IS DISTINCT FROM false" in _NEXT_SAME_TIER_SQL


@pytest.mark.unit
def test_next_same_tier_sql_applies_coalesce_overrides() -> None:
    """The failover resolver SQL must apply COALESCE for enabled, priority, and tier."""
    assert "COALESCE(bmo.enabled, mc.enabled)" in _NEXT_SAME_TIER_SQL
    assert "COALESCE(bmo.priority, mc.priority)" in _NEXT_SAME_TIER_SQL
    assert "COALESCE(bmo.complexity_tier, mc.complexity_tier)" in _NEXT_SAME_TIER_SQL


@pytest.mark.unit
def test_next_same_tier_sql_uses_deterministic_order() -> None:
    """Failover ordering must be deterministic (priority DESC, created_at ASC, id ASC)."""
    assert "ORDER BY effective_priority DESC, created_at ASC, id ASC" in _NEXT_SAME_TIER_SQL


@pytest.mark.unit
def test_next_same_tier_sql_excludes_attempted_ids() -> None:
    """Failover SQL must exclude the attempted catalog entry IDs."""
    assert "mc.id != ALL($3::uuid[])" in _NEXT_SAME_TIER_SQL


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
    last_verified_ok: bool | None = None,
) -> str:
    import json

    extra_json = json.dumps(extra_args or [])
    row = await pool.fetchrow(
        """
        INSERT INTO public.model_catalog
            (
                alias, runtime_type, model_id, extra_args, complexity_tier,
                enabled, priority, session_timeout_s, last_verified_ok
            )
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9)
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
        last_verified_ok,
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
async def test_resolve_excludes_failed_verification_rows(pool: asyncpg.Pool) -> None:
    """Rows that failed verification are not dispatch candidates.

    ``NULL`` means untested and remains eligible; ``true`` means verified and
    remains eligible. ``false`` records a recent verification failure such as a
    timeout and must not be selected again until verification succeeds.
    """
    await _insert_catalog_entry(
        pool,
        alias="timed-out-opencode",
        runtime_type="opencode",
        model_id="opencode-go/slow-model",
        complexity_tier="workhorse",
        priority=100,
        last_verified_ok=False,
    )
    verified_id = await _insert_catalog_entry(
        pool,
        alias="verified-codex",
        runtime_type="codex",
        model_id="gpt-5.4-mini",
        complexity_tier="workhorse",
        priority=10,
        last_verified_ok=True,
    )
    untested_id = await _insert_catalog_entry(
        pool,
        alias="untested-codex",
        runtime_type="codex",
        model_id="gpt-5.3-codex-spark",
        complexity_tier="cheap",
        priority=10,
        last_verified_ok=None,
    )

    result = await resolve_model(
        pool, "switchboard", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    assert result is not None
    assert result[1] == "gpt-5.4-mini"
    assert str(result[3]) == verified_id

    await pool.execute(
        "UPDATE public.model_catalog SET last_verified_ok = false WHERE id = $1",
        uuid.UUID(verified_id),
    )
    result = await resolve_model(
        pool, "switchboard", Complexity.WORKHORSE, allow_tier_fallthrough=True
    )
    assert result is not None
    assert result[1] == "gpt-5.3-codex-spark"
    assert str(result[3]) == untested_id


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
async def test_counter_only_increments_for_resolved_tier(pool: asyncpg.Pool) -> None:
    """Counter increments only for the tier that was actually selected.

    With fallthrough enabled and only a cheap entry present, requesting
    reasoning must increment the cheap counter (the resolved tier), NOT the
    reasoning or workhorse counters for the skipped empty tiers.
    """
    await _insert_catalog_entry(
        pool,
        alias="cheap-only",
        model_id="cheap-model",
        complexity_tier="cheap",
        priority=10,
    )

    # Resolve from reasoning → falls through to cheap.
    result = await resolve_model(pool, "general", Complexity.REASONING, allow_tier_fallthrough=True)
    assert result is not None and result[1] == "cheap-model"

    # Only the cheap counter should exist and be 0 (first use).
    rows = await pool.fetch(
        "SELECT complexity_tier, counter FROM public.model_round_robin_counters "
        "WHERE butler_name = $1 ORDER BY complexity_tier",
        "general",
    )
    tiers_with_counters = {r["complexity_tier"]: r["counter"] for r in rows}
    assert set(tiers_with_counters.keys()) == {"cheap"}, (
        "Expected only 'cheap' counter; found counters for empty tiers: "
        f"{set(tiers_with_counters.keys()) - {'cheap'}}"
    )
    assert tiers_with_counters["cheap"] == 0


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_empty_tier_fallthrough_does_not_increment_skipped_counters(
    pool: asyncpg.Pool,
) -> None:
    """Skipped empty tiers never appear in model_round_robin_counters.

    Multiple resolve calls falling through reasoning → workhorse → cheap must
    only accumulate a counter for cheap; reasoning and workhorse stay absent.
    """
    await _insert_catalog_entry(
        pool,
        alias="cheap-only-2",
        model_id="cheap-model-2",
        complexity_tier="cheap",
        priority=5,
    )

    # Three calls from reasoning tier; all fall through to cheap.
    for _ in range(3):
        r = await resolve_model(
            pool, "fallcheck", Complexity.REASONING, allow_tier_fallthrough=True
        )
        assert r is not None and r[1] == "cheap-model-2"

    rows = await pool.fetch(
        "SELECT complexity_tier, counter FROM public.model_round_robin_counters "
        "WHERE butler_name = $1",
        "fallcheck",
    )
    assert len(rows) == 1, f"Expected 1 counter row; got {[r['complexity_tier'] for r in rows]}"
    assert rows[0]["complexity_tier"] == "cheap"
    assert rows[0]["counter"] == 2  # 0, 1, 2 after three calls


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_no_fallthrough_does_not_increment_counter_on_miss(pool: asyncpg.Pool) -> None:
    """allow_tier_fallthrough=False with no matching entry returns None and increments nothing."""
    await _insert_catalog_entry(
        pool,
        alias="workhorse-only",
        model_id="workhorse-model",
        complexity_tier="workhorse",
        priority=10,
    )

    # Request reasoning tier with fallthrough disabled; no reasoning entry.
    result = await resolve_model(
        pool, "nofallcheck", Complexity.REASONING, allow_tier_fallthrough=False
    )
    assert result is None

    rows = await pool.fetch(
        "SELECT complexity_tier FROM public.model_round_robin_counters WHERE butler_name = $1",
        "nofallcheck",
    )
    assert rows == [], (
        f"Expected no counter rows on miss; got {[r['complexity_tier'] for r in rows]}"
    )


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


# ---------------------------------------------------------------------------
# Integration tests — resolve_model_with_effective_tier
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_with_effective_tier_returns_effective_tier(pool: asyncpg.Pool) -> None:
    """resolve_model_with_effective_tier returns 6-tuple with effective_tier appended."""
    entry_id = await _insert_catalog_entry(
        pool,
        alias="workhorse-ewt",
        model_id="model-workhorse-ewt",
        complexity_tier="workhorse",
        priority=5,
    )
    result = await resolve_model_with_effective_tier(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    assert result is not None
    assert len(result) == 6
    runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s, effective_tier = result
    assert runtime_type == "claude"
    assert model_id == "model-workhorse-ewt"
    assert extra_args == []
    assert str(catalog_entry_id) == entry_id
    assert session_timeout_s == 1800
    assert effective_tier == "workhorse"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_with_effective_tier_fallthrough_reports_resolved_tier(
    pool: asyncpg.Pool,
) -> None:
    """When tier fallthrough occurs, effective_tier reflects the actual tier found."""
    await _insert_catalog_entry(
        pool,
        alias="cheap-ewt",
        model_id="model-cheap-ewt",
        complexity_tier="cheap",
        priority=5,
    )
    # Request reasoning; no reasoning entry → falls through to cheap
    result = await resolve_model_with_effective_tier(
        pool, "general", Complexity.REASONING, allow_tier_fallthrough=True
    )
    assert result is not None
    assert result[1] == "model-cheap-ewt"
    assert result[5] == "cheap"  # effective_tier reflects cheap, not reasoning


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_with_effective_tier_none_when_no_match(pool: asyncpg.Pool) -> None:
    """Returns None when no entry qualifies in any tier."""
    result = await resolve_model_with_effective_tier(
        pool, "general", Complexity.REASONING, allow_tier_fallthrough=False
    )
    assert result is None


# ---------------------------------------------------------------------------
# Integration tests — next_same_tier_candidate
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_empty_catalog_returns_none(pool: asyncpg.Pool) -> None:
    """Returns None when catalog is empty."""
    result = await next_same_tier_candidate(pool, "general", "workhorse", [])
    assert result is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_returns_matching_entry(pool: asyncpg.Pool) -> None:
    """Returns a matching candidate when one exists in the tier."""
    entry_id = await _insert_catalog_entry(
        pool,
        alias="nst-basic",
        model_id="model-nst-basic",
        complexity_tier="workhorse",
        priority=5,
    )
    result = await next_same_tier_candidate(pool, "general", "workhorse", [])
    assert result is not None
    runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s = result
    assert runtime_type == "claude"
    assert model_id == "model-nst-basic"
    assert extra_args == []
    assert str(catalog_entry_id) == entry_id
    assert session_timeout_s == 1800


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_excludes_attempted_id(pool: asyncpg.Pool) -> None:
    """Excludes already-attempted catalog entry IDs."""
    entry_id = await _insert_catalog_entry(
        pool,
        alias="nst-exclude",
        model_id="model-nst-exclude",
        complexity_tier="workhorse",
        priority=5,
    )
    # Exclude the only entry → None
    result = await next_same_tier_candidate(pool, "general", "workhorse", [uuid.UUID(entry_id)])
    assert result is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_priority_ordering(pool: asyncpg.Pool) -> None:
    """Returns highest-priority entry first; deterministic by priority DESC, created_at ASC."""
    # Insert two entries at different priorities
    low_id = await _insert_catalog_entry(
        pool,
        alias="nst-ord-low",
        model_id="model-nst-low",
        complexity_tier="workhorse",
        priority=5,
    )
    high_id = await _insert_catalog_entry(
        pool,
        alias="nst-ord-high",
        model_id="model-nst-high",
        complexity_tier="workhorse",
        priority=20,
    )

    # Without exclusions, should return high-priority entry
    r1 = await next_same_tier_candidate(pool, "general", "workhorse", [])
    assert r1 is not None and r1[1] == "model-nst-high"

    # Exclude the high-priority entry → should return low-priority entry
    r2 = await next_same_tier_candidate(pool, "general", "workhorse", [uuid.UUID(high_id)])
    assert r2 is not None and r2[1] == "model-nst-low"

    # Exclude both → None
    r3 = await next_same_tier_candidate(
        pool, "general", "workhorse", [uuid.UUID(high_id), uuid.UUID(low_id)]
    )
    assert r3 is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_does_not_cross_tier(pool: asyncpg.Pool) -> None:
    """Failover does NOT cross tier boundaries — only exact effective tier is searched."""
    # Insert reasoning and cheap entries only (no workhorse)
    await _insert_catalog_entry(
        pool,
        alias="nst-cross-reasoning",
        model_id="model-nst-reasoning",
        complexity_tier="reasoning",
        priority=10,
    )
    await _insert_catalog_entry(
        pool,
        alias="nst-cross-cheap",
        model_id="model-nst-cheap",
        complexity_tier="cheap",
        priority=10,
    )

    # Searching workhorse tier → no results (reasoning and cheap not included)
    result = await next_same_tier_candidate(pool, "general", "workhorse", [])
    assert result is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_excludes_disabled_entries(pool: asyncpg.Pool) -> None:
    """Disabled catalog entries (enabled=False) are never returned as failover candidates."""
    await _insert_catalog_entry(
        pool,
        alias="nst-disabled",
        model_id="model-nst-disabled",
        complexity_tier="workhorse",
        priority=20,
        enabled=False,
    )
    enabled_id = await _insert_catalog_entry(
        pool,
        alias="nst-enabled",
        model_id="model-nst-enabled",
        complexity_tier="workhorse",
        priority=5,
        enabled=True,
    )

    result = await next_same_tier_candidate(pool, "general", "workhorse", [])
    assert result is not None and result[1] == "model-nst-enabled"
    assert str(result[3]) == enabled_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_excludes_failed_verification(pool: asyncpg.Pool) -> None:
    """Entries with last_verified_ok=false are excluded from failover candidates."""
    await _insert_catalog_entry(
        pool,
        alias="nst-failed-ok",
        model_id="model-nst-failed",
        complexity_tier="workhorse",
        priority=50,
        last_verified_ok=False,
    )
    good_id = await _insert_catalog_entry(
        pool,
        alias="nst-good-ok",
        model_id="model-nst-good",
        complexity_tier="workhorse",
        priority=5,
        last_verified_ok=None,  # untested — eligible
    )

    result = await next_same_tier_candidate(pool, "general", "workhorse", [])
    assert result is not None and result[1] == "model-nst-good"
    assert str(result[3]) == good_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_applies_override_disable(pool: asyncpg.Pool) -> None:
    """Butler override that disables an entry hides it for that butler only."""
    entry_id = await _insert_catalog_entry(
        pool,
        alias="nst-ovr-disable",
        model_id="model-nst-ovr-disabled",
        complexity_tier="workhorse",
        priority=10,
    )
    await _insert_catalog_entry(
        pool,
        alias="nst-ovr-visible",
        model_id="model-nst-ovr-visible",
        complexity_tier="workhorse",
        priority=5,
    )
    # Override disables the high-priority entry for butler "restricted"
    await _insert_override(pool, butler_name="restricted", catalog_entry_id=entry_id, enabled=False)

    # For "restricted": disabled entry hidden → returns lower-priority visible entry
    r_restricted = await next_same_tier_candidate(pool, "restricted", "workhorse", [])
    assert r_restricted is not None and r_restricted[1] == "model-nst-ovr-visible"

    # For "general": disabled override does not apply → returns high-priority entry
    r_general = await next_same_tier_candidate(pool, "general", "workhorse", [])
    assert r_general is not None and r_general[1] == "model-nst-ovr-disabled"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_applies_override_priority(pool: asyncpg.Pool) -> None:
    """Butler override that boosts priority of a lower-priority entry surfaces it first."""
    low_id = await _insert_catalog_entry(
        pool,
        alias="nst-prio-low",
        model_id="model-nst-prio-low",
        complexity_tier="workhorse",
        priority=5,
    )
    await _insert_catalog_entry(
        pool,
        alias="nst-prio-high",
        model_id="model-nst-prio-high",
        complexity_tier="workhorse",
        priority=20,
    )
    # Override boosts low-priority entry to 100 for butler "boosted"
    await _insert_override(
        pool,
        butler_name="boosted",
        catalog_entry_id=low_id,
        enabled=True,
        priority=100,
    )

    # For "general": high-priority catalog entry wins normally
    r_general = await next_same_tier_candidate(pool, "general", "workhorse", [])
    assert r_general is not None and r_general[1] == "model-nst-prio-high"

    # For "boosted": override lifts the low entry to priority 100 → it wins
    r_boosted = await next_same_tier_candidate(pool, "boosted", "workhorse", [])
    assert r_boosted is not None and r_boosted[1] == "model-nst-prio-low"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_override_tier_remap_excludes_from_original(
    pool: asyncpg.Pool,
) -> None:
    """Override remapping a workhorse entry to reasoning hides it from workhorse failover."""
    remapped_id = await _insert_catalog_entry(
        pool,
        alias="nst-remapped",
        model_id="model-nst-remapped",
        complexity_tier="workhorse",
        priority=20,
    )
    await _insert_catalog_entry(
        pool,
        alias="nst-remap-visible",
        model_id="model-nst-remap-visible",
        complexity_tier="workhorse",
        priority=5,
    )
    # Override remaps the high-priority workhorse entry to reasoning for butler "remapper"
    await _insert_override(
        pool,
        butler_name="remapper",
        catalog_entry_id=remapped_id,
        enabled=True,
        complexity_tier="reasoning",
    )

    # For "remapper" searching workhorse: remapped entry is gone → only visible remains
    r = await next_same_tier_candidate(pool, "remapper", "workhorse", [])
    assert r is not None and r[1] == "model-nst-remap-visible"

    # For "remapper" searching reasoning: remapped entry is found
    r_reasoning = await next_same_tier_candidate(pool, "remapper", "reasoning", [])
    assert r_reasoning is not None and r_reasoning[1] == "model-nst-remapped"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_deterministic_tiebreak_ordering(pool: asyncpg.Pool) -> None:
    """When multiple entries share the same priority, ordering is by created_at ASC then id ASC."""
    await pool.execute("""
        INSERT INTO public.model_catalog
            (alias, runtime_type, model_id, complexity_tier, priority, created_at, updated_at)
        VALUES
            ('nst-tie-first',  'claude', 'model-nst-tie-a', 'cheap', 10,
             '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
            ('nst-tie-second', 'codex',  'model-nst-tie-b', 'cheap', 10,
             '2026-01-02 00:00:00+00', '2026-01-02 00:00:00+00'),
            ('nst-tie-third',  'claude', 'model-nst-tie-c', 'cheap', 10,
             '2026-01-03 00:00:00+00', '2026-01-03 00:00:00+00')
    """)

    # First call: all available → returns the earliest-created (tie-a)
    r1 = await next_same_tier_candidate(pool, "general", "cheap", [])
    assert r1 is not None and r1[1] == "model-nst-tie-a"

    # Exclude tie-a → returns tie-b (next by created_at)
    r2 = await next_same_tier_candidate(pool, "general", "cheap", [r1[3]])
    assert r2 is not None and r2[1] == "model-nst-tie-b"

    # Exclude tie-a and tie-b → returns tie-c
    r3 = await next_same_tier_candidate(pool, "general", "cheap", [r1[3], r2[3]])
    assert r3 is not None and r3[1] == "model-nst-tie-c"

    # Exclude all → None
    r4 = await next_same_tier_candidate(pool, "general", "cheap", [r1[3], r2[3], r3[3]])
    assert r4 is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_same_tier_no_mutation_of_round_robin_counter(pool: asyncpg.Pool) -> None:
    """next_same_tier_candidate does NOT increment the round-robin counter.

    The round-robin counter is managed exclusively by resolve_model; failover
    candidate fetching must not interfere with it.
    """
    await _insert_catalog_entry(
        pool,
        alias="nst-rr-check",
        model_id="model-nst-rr",
        complexity_tier="workhorse",
        priority=10,
    )
    butler = "rr-check-butler"

    # Call resolve_model once to create the counter row at 0
    await resolve_model(pool, butler, Complexity.WORKHORSE, allow_tier_fallthrough=False)
    rows_before = await pool.fetch(
        "SELECT counter FROM public.model_round_robin_counters WHERE butler_name = $1",
        butler,
    )
    counter_before = rows_before[0]["counter"] if rows_before else None

    # Call next_same_tier_candidate multiple times
    for _ in range(3):
        await next_same_tier_candidate(pool, butler, "workhorse", [])

    rows_after = await pool.fetch(
        "SELECT counter FROM public.model_round_robin_counters WHERE butler_name = $1",
        butler,
    )
    counter_after = rows_after[0]["counter"] if rows_after else None

    # Counter must not have changed
    assert counter_before == counter_after

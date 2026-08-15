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
import time
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.core import model_routing as model_routing_module
from butlers.core.model_routing import (
    _BREAKER_FAILURE_THRESHOLD,
    _BREAKER_HALF_OPEN_COOLDOWN_MINUTES,
    _EVIDENCE_MIN_SAMPLES,
    BREAKER_OPEN_RULE_OVERRIDE_OUTCOME,
    BREAKER_OPEN_RULE_OVERRIDE_REASON_PREFIX,
    TIER_FALLTHROUGH_ORDER,
    Complexity,
    RoutingEvidence,
    TierQuotaExhausted,
    _check_deprecated_tier,
    _parse_max_cost_per_call,
    _rule_condition_matches,
    apply_spend_routing_rules,
    clear_routing_decision_cache,
    coerce_complexity_tier,
    compute_routing_score,
    get_breaker_state,
    get_breaker_states,
    get_routing_evidence,
    get_routing_scores,
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
def test_coerce_complexity_tier_canonical_passthrough() -> None:
    """Canonical tier strings resolve to their matching Complexity member."""
    for tier in Complexity:
        assert coerce_complexity_tier(tier.value) == tier
        assert coerce_complexity_tier(tier.value, strict=False) == tier


@pytest.mark.unit
def test_coerce_complexity_tier_defaults_to_workhorse_when_missing() -> None:
    """None/empty input defaults to workhorse under both strict and lenient modes."""
    assert coerce_complexity_tier(None) == Complexity.WORKHORSE
    assert coerce_complexity_tier("") == Complexity.WORKHORSE
    assert coerce_complexity_tier(None, strict=False) == Complexity.WORKHORSE


@pytest.mark.unit
def test_coerce_complexity_tier_remaps_legacy_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retired pre-core_092 vocabulary degrades gracefully instead of crashing."""
    import logging

    expected = {
        "trivial": Complexity.CHEAP,
        "medium": Complexity.WORKHORSE,
        "high": Complexity.REASONING,
        "extra_high": Complexity.REASONING,
        "discretion": Complexity.SPECIALTY,
        "self_healing": Complexity.SPECIALTY,
    }
    with caplog.at_level(logging.WARNING, logger="butlers.core.model_routing"):
        for legacy, canonical in expected.items():
            assert coerce_complexity_tier(legacy) == canonical
            assert coerce_complexity_tier(legacy, strict=False) == canonical

    assert len(caplog.records) == len(expected) * 2
    for record in caplog.records:
        assert "DEPRECATED" in record.message

    # Case/whitespace-insensitive.
    assert coerce_complexity_tier("  MEDIUM  ") == Complexity.WORKHORSE


@pytest.mark.unit
def test_coerce_complexity_tier_strict_raises_clear_error_on_garbage() -> None:
    """A genuinely-invalid value raises ValueError naming the valid tiers, not a bare enum error."""
    with pytest.raises(ValueError) as exc_info:
        coerce_complexity_tier("not_a_real_tier")

    message = str(exc_info.value)
    assert "not_a_real_tier" in message
    for tier in Complexity:
        assert tier.value in message


@pytest.mark.unit
def test_coerce_complexity_tier_lenient_fails_open_on_garbage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """strict=False never crashes — unrecognized values fall back to workhorse with a warning."""
    import logging

    with caplog.at_level(logging.WARNING, logger="butlers.core.model_routing"):
        assert coerce_complexity_tier("not_a_real_tier", strict=False) == Complexity.WORKHORSE

    assert len(caplog.records) == 1
    assert "not_a_real_tier" in caplog.records[0].message


# ---------------------------------------------------------------------------
# compute_routing_score (bu-ep4ks.13) — pure function, no DB required
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compute_routing_score_insufficient_data_below_min_samples() -> None:
    """Fewer than min_samples qualifying attempts -> score=None, never a fabricated number."""
    evidence = RoutingEvidence(
        success_count=2, failure_count=1, p50_duration_ms=1000.0, p95_duration_ms=1500.0
    )
    result = compute_routing_score(evidence, cost_usd_per_call=0.01, min_samples=5)
    assert result.score is None
    assert result.insufficient_data is True
    assert result.success_rate is None
    assert "n=3" in result.reason
    assert "need >= 5" in result.reason


@pytest.mark.unit
def test_compute_routing_score_exactly_at_min_samples_produces_a_score() -> None:
    """min_samples qualifying attempts is enough -- the threshold is inclusive."""
    evidence = RoutingEvidence(
        success_count=5, failure_count=0, p50_duration_ms=1000.0, p95_duration_ms=1200.0
    )
    result = compute_routing_score(evidence, cost_usd_per_call=0.0, min_samples=5)
    assert result.insufficient_data is False
    assert result.score is not None
    assert result.success_rate == 1.0
    assert result.sample_count == 5


@pytest.mark.unit
def test_compute_routing_score_penalizes_failures() -> None:
    """A model that fails more often scores lower than an otherwise-identical reliable one."""
    reliable = RoutingEvidence(
        success_count=10, failure_count=0, p50_duration_ms=1000.0, p95_duration_ms=1000.0
    )
    flaky = RoutingEvidence(
        success_count=5, failure_count=5, p50_duration_ms=1000.0, p95_duration_ms=1000.0
    )
    reliable_score = compute_routing_score(reliable, cost_usd_per_call=0.0)
    flaky_score = compute_routing_score(flaky, cost_usd_per_call=0.0)
    assert reliable_score.score > flaky_score.score


@pytest.mark.unit
def test_compute_routing_score_penalizes_latency() -> None:
    """A slower model scores lower than an otherwise-identical fast one."""
    fast = RoutingEvidence(
        success_count=10, failure_count=0, p50_duration_ms=1000.0, p95_duration_ms=1000.0
    )
    slow = RoutingEvidence(
        success_count=10, failure_count=0, p50_duration_ms=1000.0, p95_duration_ms=436_000.0
    )
    fast_score = compute_routing_score(fast, cost_usd_per_call=0.0)
    slow_score = compute_routing_score(slow, cost_usd_per_call=0.0)
    assert fast_score.score > slow_score.score


@pytest.mark.unit
def test_compute_routing_score_penalizes_cost() -> None:
    """A more expensive model scores lower than an otherwise-identical cheap one."""
    cheap = RoutingEvidence(
        success_count=10, failure_count=0, p50_duration_ms=1000.0, p95_duration_ms=1000.0
    )
    result_cheap = compute_routing_score(cheap, cost_usd_per_call=0.001)
    result_expensive = compute_routing_score(cheap, cost_usd_per_call=1.0)
    assert result_cheap.score > result_expensive.score


@pytest.mark.unit
def test_compute_routing_score_unpriced_model_treated_as_cost_neutral() -> None:
    """cost_usd_per_call=None (unpriced/free/local model) is not excluded or penalized to zero."""
    evidence = RoutingEvidence(
        success_count=10, failure_count=0, p50_duration_ms=1000.0, p95_duration_ms=1000.0
    )
    result = compute_routing_score(evidence, cost_usd_per_call=None)
    assert result.score is not None
    assert result.score > 0
    # Same as an explicit cost of 0.0.
    assert result.score == compute_routing_score(evidence, cost_usd_per_call=0.0).score


# ---------------------------------------------------------------------------
# Bounded routing-decision cache (bu-ep4ks.13 follow-up / bu-k9te9, slice 5)
#
# Pure-Python unit coverage of _fetch_resolve_rows's cache mechanics against a fake pool
# (no DB needed for hit/miss/expiry/eviction/bypass logic). Behavioral coverage against a
# real resolve_model/resolve_model_with_effective_tier call (round-robin counter still
# incrementing on a cache hit, quota_aware never caching) lives in the integration tests
# below, since those need real catalog/breaker/quota state.
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_fetch_resolve_rows_cache_hit_skips_second_db_call() -> None:
    model_routing_module.clear_routing_decision_cache()
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"id": "row-1"}])
    try:
        rows1 = await model_routing_module._fetch_resolve_rows(pool, "general", ["workhorse"])
        rows2 = await model_routing_module._fetch_resolve_rows(pool, "general", ["workhorse"])
        assert rows1 == rows2 == [{"id": "row-1"}]
        pool.fetch.assert_called_once()
    finally:
        model_routing_module.clear_routing_decision_cache()


@pytest.mark.unit
async def test_fetch_resolve_rows_cache_false_always_hits_db() -> None:
    """The quota-aware path (cache=False) never reads or writes the cache."""
    model_routing_module.clear_routing_decision_cache()
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"id": "row-1"}])
    try:
        await model_routing_module._fetch_resolve_rows(pool, "general", ["workhorse"], cache=False)
        await model_routing_module._fetch_resolve_rows(pool, "general", ["workhorse"], cache=False)
        assert pool.fetch.await_count == 2
        # And it never populated the cache for a later cache=True caller either.
        assert ("general", ("workhorse",)) not in model_routing_module._routing_rows_cache
    finally:
        model_routing_module.clear_routing_decision_cache()


@pytest.mark.unit
async def test_fetch_resolve_rows_empty_result_never_cached() -> None:
    model_routing_module.clear_routing_decision_cache()
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    try:
        await model_routing_module._fetch_resolve_rows(pool, "general", ["workhorse"])
        await model_routing_module._fetch_resolve_rows(pool, "general", ["workhorse"])
        assert pool.fetch.await_count == 2
    finally:
        model_routing_module.clear_routing_decision_cache()


@pytest.mark.unit
async def test_fetch_resolve_rows_expired_entry_refetches() -> None:
    model_routing_module.clear_routing_decision_cache()
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"id": "row-1"}])
    try:
        await model_routing_module._fetch_resolve_rows(pool, "general", ["workhorse"])
        key = ("general", ("workhorse",))
        rows, _expires_at = model_routing_module._routing_rows_cache[key]
        # Force expiry without sleeping the test for the real TTL.
        model_routing_module._routing_rows_cache[key] = (rows, time.monotonic() - 1)
        await model_routing_module._fetch_resolve_rows(pool, "general", ["workhorse"])
        assert pool.fetch.await_count == 2
    finally:
        model_routing_module.clear_routing_decision_cache()


@pytest.mark.unit
async def test_fetch_resolve_rows_bounded_lru_eviction() -> None:
    model_routing_module.clear_routing_decision_cache()
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"id": "row"}])
    max_entries = model_routing_module._ROUTING_CACHE_MAX_ENTRIES
    try:
        for i in range(max_entries + 10):
            await model_routing_module._fetch_resolve_rows(pool, f"butler-{i}", ["workhorse"])
        assert len(model_routing_module._routing_rows_cache) == max_entries
        # Oldest entries evicted; most-recently-inserted retained.
        assert ("butler-0", ("workhorse",)) not in model_routing_module._routing_rows_cache
        assert ("butler-9", ("workhorse",)) not in model_routing_module._routing_rows_cache
        assert (
            f"butler-{max_entries + 9}",
            ("workhorse",),
        ) in model_routing_module._routing_rows_cache
    finally:
        model_routing_module.clear_routing_decision_cache()


# ---------------------------------------------------------------------------
# Integration helpers
#
# NOTE: the resolver/failover SQL invariants (excludes failed-verification rows,
# excludes attempted ids, COALESCE override application, deterministic tiebreak
# ordering) are covered behaviorally below by the pool-backed tests
# test_resolve_excludes_failed_verification_rows,
# test_next_same_tier_excludes_attempted_id,
# test_next_same_tier_excludes_failed_verification,
# test_next_same_tier_applies_override_disable / _priority, and
# test_next_same_tier_deterministic_tiebreak_ordering.
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
    """Return an asyncpg pool with model routing tables cleared between tests.

    Also drops the bounded routing-decision cache (bu-k9te9, slice 5): it is a
    process-global, TTL-based cache keyed on (butler_name, tiers), so without this a test
    could observe another test's cached rows for the same key within the TTL window
    despite the DB being freshly truncated.
    """
    clear_routing_decision_cache()
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


@pytest.mark.unit
def test_rule_condition_matches_semantics() -> None:
    """Spend-rule condition matching: AND of constraints, catch-all, list, case-insensitive."""
    # Empty condition is a catch-all.
    assert _rule_condition_matches({}, butler_name="general", complexity_tier="workhorse")

    # Exact butler match.
    assert _rule_condition_matches(
        {"butler": "general"}, butler_name="general", complexity_tier="workhorse"
    )
    assert not _rule_condition_matches(
        {"butler": "health"}, butler_name="general", complexity_tier="workhorse"
    )

    # complexity / tier aliases both work; case-insensitive.
    assert _rule_condition_matches(
        {"complexity": "WORKHORSE"}, butler_name="general", complexity_tier="workhorse"
    )
    assert _rule_condition_matches(
        {"tier": "workhorse"}, butler_name="general", complexity_tier="workhorse"
    )

    # AND semantics: all constraints must hold.
    assert _rule_condition_matches(
        {"butler": "general", "complexity": "workhorse"},
        butler_name="general",
        complexity_tier="workhorse",
    )
    assert not _rule_condition_matches(
        {"butler": "general", "complexity": "reasoning"},
        butler_name="general",
        complexity_tier="workhorse",
    )

    # List membership.
    assert _rule_condition_matches(
        {"butler": ["general", "health"]}, butler_name="health", complexity_tier="cheap"
    )
    assert not _rule_condition_matches(
        {"butler": ["general", "health"]}, butler_name="travel", complexity_tier="cheap"
    )

    # Unknown constraint dimension fails closed (does NOT match-all).
    assert not _rule_condition_matches(
        {"weather": "sunny"}, butler_name="general", complexity_tier="workhorse"
    )


@pytest.mark.unit
def test_rule_condition_trigger_dim() -> None:
    """The ``trigger`` condition dim matches the dispatch trigger_source."""
    # Exact trigger match (case-insensitive).
    assert _rule_condition_matches(
        {"trigger": "healing"},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source="healing",
    )
    assert _rule_condition_matches(
        {"trigger": "QA"},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source="qa",
    )
    # Non-matching trigger.
    assert not _rule_condition_matches(
        {"trigger": "healing"},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source="route",
    )
    # List membership on trigger.
    assert _rule_condition_matches(
        {"trigger": ["healing", "retry"]},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source="retry",
    )
    # Trigger constraint present but no trigger context → fail closed.
    assert not _rule_condition_matches(
        {"trigger": "healing"},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source=None,
    )
    # AND with other dims.
    assert _rule_condition_matches(
        {"butler": "general", "trigger": "healing"},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source="healing",
    )


@pytest.mark.unit
def test_rule_condition_purpose_dim_aliases_trigger() -> None:
    """The ``purpose`` condition dim is an alias for ``trigger`` (bu-og0j2/bu-qvnce.12).

    Both evaluate against the same ``trigger_source`` value passed to
    ``_rule_condition_matches`` -- ``purpose`` just matches the vocabulary the
    ``/spend`` breakdown and ``token_usage_ledger.purpose`` use for this dimension.
    """
    # Exact purpose match (case-insensitive), same semantics as trigger.
    assert _rule_condition_matches(
        {"purpose": "discretion"},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source="discretion",
    )
    assert _rule_condition_matches(
        {"purpose": "CLASSIFICATION"},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source="classification",
    )
    # Non-matching purpose.
    assert not _rule_condition_matches(
        {"purpose": "discretion"},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source="route",
    )
    # List membership on purpose.
    assert _rule_condition_matches(
        {"purpose": ["healing", "discretion"]},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source="discretion",
    )
    # Purpose constraint present but no trigger context → fail closed.
    assert not _rule_condition_matches(
        {"purpose": "discretion"},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source=None,
    )
    # AND with other dims.
    assert _rule_condition_matches(
        {"butler": "general", "purpose": "discretion"},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source="discretion",
    )
    # trigger and purpose target the SAME context value, so a rule combining
    # both with conflicting values can never match (documented AND semantics).
    assert not _rule_condition_matches(
        {"trigger": "route", "purpose": "discretion"},
        butler_name="general",
        complexity_tier="workhorse",
        trigger_source="route",
    )


@pytest.mark.unit
def test_parse_max_cost_per_call() -> None:
    """action.max_cost_per_call parsing: positive float kept, malformed/non-positive dropped."""
    assert _parse_max_cost_per_call({"max_cost_per_call": 0.05}, "r1") == pytest.approx(0.05)
    assert _parse_max_cost_per_call({"max_cost_per_call": "0.1"}, "r1") == pytest.approx(0.1)
    # Absent cap → None.
    assert _parse_max_cost_per_call({"model": "m"}, "r1") is None
    # Non-positive → ignored (None).
    assert _parse_max_cost_per_call({"max_cost_per_call": 0}, "r1") is None
    assert _parse_max_cost_per_call({"max_cost_per_call": -1.0}, "r1") is None
    # Non-numeric → ignored (None).
    assert _parse_max_cost_per_call({"max_cost_per_call": "abc"}, "r1") is None
    assert _parse_max_cost_per_call({"max_cost_per_call": None}, "r1") is None


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


# ---------------------------------------------------------------------------
# Spend routing rules — apply_spend_routing_rules (model SELECTION override)
# ---------------------------------------------------------------------------


async def _insert_spend_rule(
    pool: asyncpg.Pool,
    *,
    position: int,
    condition: dict,
    action: dict,
) -> None:
    import json

    await pool.execute(
        """
        INSERT INTO public.spend_rules (position, condition, action)
        VALUES ($1, $2::jsonb, $3::jsonb)
        """,
        position,
        json.dumps(condition),
        json.dumps(action),
    )


async def _resolved_tuple(pool: asyncpg.Pool, butler: str, tier: Complexity):
    """Resolve a model and return the 5-tuple shape apply_spend_routing_rules expects."""
    r = await resolve_model(pool, butler, tier, allow_tier_fallthrough=False)
    assert r is not None
    return r


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_spend_rule_overrides_resolved_model(pool: asyncpg.Pool) -> None:
    """A matching routing rule re-routes the resolved model to the rule's target.

    Pre-fix this assertion fails: rules were never consulted at dispatch, so the
    tier-resolved model would be returned unchanged.
    """
    await pool.execute("TRUNCATE public.spend_rules")

    # Tier resolution would pick the expensive (higher-priority) workhorse model.
    expensive_id = await _insert_catalog_entry(
        pool,
        alias="expensive",
        model_id="claude-opus-expensive",
        complexity_tier="workhorse",
        priority=100,
    )
    cheap_id = await _insert_catalog_entry(
        pool,
        alias="cheap",
        model_id="claude-haiku-cheap",
        complexity_tier="workhorse",
        priority=1,
    )

    resolved = await _resolved_tuple(pool, "general", Complexity.WORKHORSE)
    assert resolved[1] == "claude-opus-expensive"
    assert str(resolved[3]) == expensive_id

    # Rule: route general/workhorse → the cheap model.
    await _insert_spend_rule(
        pool,
        position=0,
        condition={"butler": "general", "complexity": "workhorse"},
        action={"model": "claude-haiku-cheap"},
    )

    routed = (
        await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, resolved)
    ).resolved
    assert routed[1] == "claude-haiku-cheap"
    assert str(routed[3]) == cheap_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_spend_rule_first_match_wins(pool: asyncpg.Pool) -> None:
    """Rules evaluate top-to-bottom (position ASC); the first matching rule wins."""
    await pool.execute("TRUNCATE public.spend_rules")

    await _insert_catalog_entry(
        pool, alias="base", model_id="base-model", complexity_tier="workhorse", priority=100
    )
    first_id = await _insert_catalog_entry(
        pool, alias="first", model_id="first-target", complexity_tier="workhorse", priority=1
    )
    await _insert_catalog_entry(
        pool, alias="second", model_id="second-target", complexity_tier="workhorse", priority=1
    )

    resolved = await _resolved_tuple(pool, "general", Complexity.WORKHORSE)
    assert resolved[1] == "base-model"

    # Two catch-all rules both match general/workhorse; position 0 must win.
    await _insert_spend_rule(pool, position=0, condition={}, action={"model": "first-target"})
    await _insert_spend_rule(pool, position=1, condition={}, action={"model": "second-target"})

    routed = (
        await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, resolved)
    ).resolved
    assert routed[1] == "first-target"
    assert str(routed[3]) == first_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_spend_rule_no_match_leaves_model_unchanged(pool: asyncpg.Pool) -> None:
    """When no rule condition matches, the tier-resolved model is returned unchanged."""
    await pool.execute("TRUNCATE public.spend_rules")

    base_id = await _insert_catalog_entry(
        pool, alias="base", model_id="base-model", complexity_tier="workhorse", priority=100
    )
    await _insert_catalog_entry(
        pool, alias="other", model_id="other-model", complexity_tier="workhorse", priority=1
    )

    resolved = await _resolved_tuple(pool, "general", Complexity.WORKHORSE)
    assert resolved[1] == "base-model"

    # Rule targets a DIFFERENT butler — does not match this dispatch.
    await _insert_spend_rule(
        pool, position=0, condition={"butler": "health"}, action={"model": "other-model"}
    )

    routed = (
        await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, resolved)
    ).resolved
    assert routed[1] == "base-model"
    assert str(routed[3]) == base_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_spend_rule_unroutable_target_keeps_original(pool: asyncpg.Pool) -> None:
    """A matched rule routing to a non-dispatchable model keeps the original (first-match-wins)."""
    await pool.execute("TRUNCATE public.spend_rules")

    base_id = await _insert_catalog_entry(
        pool, alias="base", model_id="base-model", complexity_tier="workhorse", priority=100
    )
    fallthrough_id = await _insert_catalog_entry(
        pool,
        alias="fallthrough",
        model_id="fallthrough-model",
        complexity_tier="workhorse",
        priority=1,
    )

    resolved = await _resolved_tuple(pool, "general", Complexity.WORKHORSE)
    assert resolved[1] == "base-model"

    # First rule matches but routes to a model with no catalog row → unroutable.
    # Second rule also matches and IS routable; first-match-wins means it must NOT
    # be reached — the original model is kept.
    await _insert_spend_rule(pool, position=0, condition={}, action={"model": "does-not-exist"})
    await _insert_spend_rule(pool, position=1, condition={}, action={"model": "fallthrough-model"})

    routed = (
        await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, resolved)
    ).resolved
    assert routed[1] == "base-model"
    assert str(routed[3]) == base_id
    assert str(fallthrough_id) != str(base_id)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_spend_rule_surfaces_max_cost_per_call(pool: asyncpg.Pool) -> None:
    """A matching rule's action.max_cost_per_call is surfaced on the routing result."""
    await pool.execute("TRUNCATE public.spend_rules")

    await _insert_catalog_entry(
        pool, alias="base", model_id="base-model", complexity_tier="workhorse", priority=100
    )
    cheap_id = await _insert_catalog_entry(
        pool, alias="cheap", model_id="cheap-model", complexity_tier="workhorse", priority=1
    )

    resolved = await _resolved_tuple(pool, "general", Complexity.WORKHORSE)

    # Rule re-routes the model AND attaches a per-call cap.
    await _insert_spend_rule(
        pool,
        position=0,
        condition={"butler": "general"},
        action={"model": "cheap-model", "max_cost_per_call": 0.05},
    )

    result = await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, resolved)
    assert result.resolved[1] == "cheap-model"
    assert str(result.resolved[3]) == cheap_id
    assert result.max_cost_per_call == pytest.approx(0.05)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_spend_rule_cap_only_keeps_model(pool: asyncpg.Pool) -> None:
    """A cap-only rule (no action.model) keeps the resolved model but surfaces the cap."""
    await pool.execute("TRUNCATE public.spend_rules")

    base_id = await _insert_catalog_entry(
        pool, alias="base", model_id="base-model", complexity_tier="workhorse", priority=100
    )

    resolved = await _resolved_tuple(pool, "general", Complexity.WORKHORSE)

    await _insert_spend_rule(pool, position=0, condition={}, action={"max_cost_per_call": 0.10})

    result = await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, resolved)
    # Model unchanged, cap surfaced.
    assert result.resolved[1] == "base-model"
    assert str(result.resolved[3]) == base_id
    assert result.max_cost_per_call == pytest.approx(0.10)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_spend_rule_trigger_condition_at_dispatch(pool: asyncpg.Pool) -> None:
    """The trigger_source dim gates a rule at dispatch via apply_spend_routing_rules."""
    await pool.execute("TRUNCATE public.spend_rules")

    await _insert_catalog_entry(
        pool, alias="base", model_id="base-model", complexity_tier="workhorse", priority=100
    )
    cheap_id = await _insert_catalog_entry(
        pool, alias="cheap", model_id="cheap-model", complexity_tier="workhorse", priority=1
    )

    resolved = await _resolved_tuple(pool, "general", Complexity.WORKHORSE)

    # Rule matches only healing-triggered dispatches.
    await _insert_spend_rule(
        pool, position=0, condition={"trigger": "healing"}, action={"model": "cheap-model"}
    )

    # No trigger context → rule does not match.
    no_trigger = await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, resolved)
    assert no_trigger.resolved[1] == "base-model"

    # Non-matching trigger → rule does not match.
    routed_other = await apply_spend_routing_rules(
        pool, "general", Complexity.WORKHORSE, resolved, trigger_source="route"
    )
    assert routed_other.resolved[1] == "base-model"

    # Matching trigger → rule re-routes.
    routed_healing = await apply_spend_routing_rules(
        pool, "general", Complexity.WORKHORSE, resolved, trigger_source="healing"
    )
    assert routed_healing.resolved[1] == "cheap-model"
    assert str(routed_healing.resolved[3]) == cheap_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_spend_rule_purpose_condition_at_dispatch(pool: asyncpg.Pool) -> None:
    """The ``purpose`` dim gates a rule at dispatch identically to ``trigger`` (bu-og0j2).

    ``purpose`` is stamped onto ``token_usage_ledger`` using the same
    ``trigger_source`` value passed here, including ``"discretion"`` -- a value that
    has no equivalent named ``trigger_source`` constant but IS a real dispatch
    context (connectors.discretion_dispatcher). A rule authored against ``purpose``
    must gate on it exactly as a ``trigger`` rule would.
    """
    await pool.execute("TRUNCATE public.spend_rules")

    await _insert_catalog_entry(
        pool, alias="base", model_id="base-model", complexity_tier="workhorse", priority=100
    )
    cheap_id = await _insert_catalog_entry(
        pool, alias="cheap", model_id="cheap-model", complexity_tier="workhorse", priority=1
    )

    resolved = await _resolved_tuple(pool, "general", Complexity.WORKHORSE)

    # Rule matches only discretion-purposed dispatches.
    await _insert_spend_rule(
        pool, position=0, condition={"purpose": "discretion"}, action={"model": "cheap-model"}
    )

    # No trigger context → rule does not match.
    no_trigger = await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, resolved)
    assert no_trigger.resolved[1] == "base-model"

    # Non-matching purpose → rule does not match.
    routed_other = await apply_spend_routing_rules(
        pool, "general", Complexity.WORKHORSE, resolved, trigger_source="route"
    )
    assert routed_other.resolved[1] == "base-model"

    # Matching purpose → rule re-routes.
    routed_discretion = await apply_spend_routing_rules(
        pool, "general", Complexity.WORKHORSE, resolved, trigger_source="discretion"
    )
    assert routed_discretion.resolved[1] == "cheap-model"
    assert str(routed_discretion.resolved[3]) == cheap_id


# ---------------------------------------------------------------------------
# Dispatch-outcome circuit breaker (bu-hmdqz.2)
# ---------------------------------------------------------------------------


async def _insert_dispatch_attempt(
    pool: asyncpg.Pool,
    *,
    catalog_entry_id: str,
    outcome: str,
    butler: str = "general",
    duration_ms: int | None = None,
    ts: datetime | None = None,
) -> int:
    """Insert one public.model_dispatch_attempts row for breaker/evidence tests."""
    attempt_id = await pool.fetchval(
        """
        INSERT INTO public.model_dispatch_attempts
            (catalog_entry_id, butler, outcome, attempt_index, duration_ms, ts)
        VALUES ($1, $2, $3, 0, $4, COALESCE($5::timestamptz, now()))
        RETURNING id
        """,
        uuid.UUID(catalog_entry_id),
        butler,
        outcome,
        duration_ms,
        ts,
    )
    assert isinstance(attempt_id, int)
    return attempt_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_breaker_closed_with_no_dispatch_history(pool: asyncpg.Pool) -> None:
    """A brand-new catalog entry with no dispatch_attempts rows has a closed breaker."""
    entry_id = await _insert_catalog_entry(
        pool, alias="fresh", model_id="fresh-model", complexity_tier="workhorse"
    )
    state = await get_breaker_state(pool, uuid.UUID(entry_id))
    assert state.open is False
    assert state.consecutive_failures == 0


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_breaker_opens_after_threshold_consecutive_failures(pool: asyncpg.Pool) -> None:
    """N consecutive runtime_failure rows trip the breaker open."""
    entry_id = await _insert_catalog_entry(
        pool, alias="flaky", model_id="flaky-model", complexity_tier="workhorse"
    )
    for _ in range(_BREAKER_FAILURE_THRESHOLD):
        await _insert_dispatch_attempt(pool, catalog_entry_id=entry_id, outcome="runtime_failure")

    state = await get_breaker_state(pool, uuid.UUID(entry_id))
    assert state.open is True
    assert state.consecutive_failures == _BREAKER_FAILURE_THRESHOLD


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_breaker_and_resolver_use_id_for_equal_timestamp_ordering(pool: asyncpg.Pool) -> None:
    """The newest id wins when same-timestamp attempts race each other.

    A success is written first, followed by the five later-inserted failures at
    the exact same timestamp.  The trailing deterministic window is therefore
    five failures, which opens the high-priority entry and makes resolution
    select the healthy lower-priority candidate.
    """
    tripped_id = await _insert_catalog_entry(
        pool,
        alias="equal-ts-tripped",
        model_id="equal-ts-tripped-model",
        complexity_tier="workhorse",
        priority=100,
    )
    healthy_id = await _insert_catalog_entry(
        pool,
        alias="equal-ts-healthy",
        model_id="equal-ts-healthy-model",
        complexity_tier="workhorse",
        priority=10,
    )
    tied_ts = datetime.now(UTC)
    await _insert_dispatch_attempt(pool, catalog_entry_id=tripped_id, outcome="success", ts=tied_ts)
    for _ in range(_BREAKER_FAILURE_THRESHOLD):
        await _insert_dispatch_attempt(
            pool, catalog_entry_id=tripped_id, outcome="runtime_failure", ts=tied_ts
        )

    state = await get_breaker_state(pool, uuid.UUID(tripped_id))
    assert state.open is True
    resolved = await resolve_model(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    assert resolved is not None
    assert str(resolved[3]) == healthy_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_breaker_stays_closed_below_threshold(pool: asyncpg.Pool) -> None:
    """Fewer than the threshold consecutive failures leaves the breaker closed."""
    entry_id = await _insert_catalog_entry(
        pool, alias="mostly-ok", model_id="mostly-ok-model", complexity_tier="workhorse"
    )
    for _ in range(_BREAKER_FAILURE_THRESHOLD - 1):
        await _insert_dispatch_attempt(pool, catalog_entry_id=entry_id, outcome="runtime_failure")

    state = await get_breaker_state(pool, uuid.UUID(entry_id))
    assert state.open is False


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_breaker_closes_after_a_success_breaks_the_streak(pool: asyncpg.Pool) -> None:
    """A success row inside the trailing window closes the breaker (half-open probe succeeded)."""
    entry_id = await _insert_catalog_entry(
        pool, alias="recovered", model_id="recovered-model", complexity_tier="workhorse"
    )
    for _ in range(_BREAKER_FAILURE_THRESHOLD):
        await _insert_dispatch_attempt(pool, catalog_entry_id=entry_id, outcome="runtime_failure")
    state = await get_breaker_state(pool, uuid.UUID(entry_id))
    assert state.open is True

    await _insert_dispatch_attempt(pool, catalog_entry_id=entry_id, outcome="success")
    state = await get_breaker_state(pool, uuid.UUID(entry_id))
    assert state.open is False


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_breaker_ignores_suppressed_and_quota_skip_outcomes(pool: asyncpg.Pool) -> None:
    """'suppressed' and 'quota_skip' rows are not systemic-failure signals about
    the model itself and must not contribute to the consecutive-failure count."""
    entry_id = await _insert_catalog_entry(
        pool, alias="quota-limited", model_id="quota-limited-model", complexity_tier="workhorse"
    )
    for _ in range(10):
        await _insert_dispatch_attempt(pool, catalog_entry_id=entry_id, outcome="quota_skip")
    for _ in range(10):
        await _insert_dispatch_attempt(pool, catalog_entry_id=entry_id, outcome="suppressed")

    state = await get_breaker_state(pool, uuid.UUID(entry_id))
    assert state.open is False
    assert state.consecutive_failures == 0


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_breaker_open_excludes_entry_from_resolve_model(pool: asyncpg.Pool) -> None:
    """resolve_model must skip a breaker-open entry, falling through as if it
    did not exist — the same behavior as last_verified_ok = false."""
    tripped_id = await _insert_catalog_entry(
        pool, alias="tripped", model_id="tripped-model", complexity_tier="workhorse", priority=100
    )
    healthy_id = await _insert_catalog_entry(
        pool, alias="healthy", model_id="healthy-model", complexity_tier="workhorse", priority=10
    )
    for _ in range(_BREAKER_FAILURE_THRESHOLD):
        await _insert_dispatch_attempt(pool, catalog_entry_id=tripped_id, outcome="runtime_failure")

    result = await resolve_model(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    assert result is not None
    assert str(result[3]) == healthy_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_breaker_open_excludes_entry_from_next_same_tier_candidate(
    pool: asyncpg.Pool,
) -> None:
    """next_same_tier_candidate must also skip a breaker-open entry."""
    tripped_id = await _insert_catalog_entry(
        pool, alias="tripped2", model_id="tripped2-model", complexity_tier="workhorse", priority=100
    )
    healthy_id = await _insert_catalog_entry(
        pool, alias="healthy2", model_id="healthy2-model", complexity_tier="workhorse", priority=10
    )
    for _ in range(_BREAKER_FAILURE_THRESHOLD):
        await _insert_dispatch_attempt(pool, catalog_entry_id=tripped_id, outcome="runtime_failure")

    result = await next_same_tier_candidate(pool, "general", "workhorse", [])
    assert result is not None
    assert str(result[3]) == healthy_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_breaker_half_open_probe_after_cooldown(pool: asyncpg.Pool) -> None:
    """After the cooldown window elapses since the last failure, the breaker
    stops excluding the entry (the next resolution IS the half-open probe)."""
    entry_id = await _insert_catalog_entry(
        pool, alias="cooled-down", model_id="cooled-down-model", complexity_tier="workhorse"
    )
    for _ in range(_BREAKER_FAILURE_THRESHOLD):
        await _insert_dispatch_attempt(pool, catalog_entry_id=entry_id, outcome="runtime_failure")
    state = await get_breaker_state(pool, uuid.UUID(entry_id))
    assert state.open is True

    # Backdate every attempt row past the cooldown window.
    await pool.execute(
        f"""
        UPDATE public.model_dispatch_attempts
           SET ts = now() - interval '{_BREAKER_HALF_OPEN_COOLDOWN_MINUTES + 1} minutes'
         WHERE catalog_entry_id = $1
        """,
        uuid.UUID(entry_id),
    )

    state = await get_breaker_state(pool, uuid.UUID(entry_id))
    assert state.open is False


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_get_breaker_states_batches_across_catalog(pool: asyncpg.Pool) -> None:
    """get_breaker_states returns per-entry state for the whole catalog in one call."""
    tripped_id = await _insert_catalog_entry(
        pool, alias="batch-tripped", model_id="batch-tripped-model", complexity_tier="workhorse"
    )
    healthy_id = await _insert_catalog_entry(
        pool, alias="batch-healthy", model_id="batch-healthy-model", complexity_tier="workhorse"
    )
    for _ in range(_BREAKER_FAILURE_THRESHOLD):
        await _insert_dispatch_attempt(pool, catalog_entry_id=tripped_id, outcome="runtime_failure")
    await _insert_dispatch_attempt(pool, catalog_entry_id=healthy_id, outcome="success")

    states = await get_breaker_states(pool, [uuid.UUID(tripped_id), uuid.UUID(healthy_id)])
    assert states[uuid.UUID(tripped_id)].open is True
    assert states[uuid.UUID(healthy_id)].open is False


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_get_breaker_states_defaults_missing_ids_to_closed(pool: asyncpg.Pool) -> None:
    """An entry with zero dispatch history returns a default closed BreakerState
    rather than being omitted from the result dict."""
    entry_id = await _insert_catalog_entry(
        pool, alias="never-dispatched", model_id="never-dispatched-model", complexity_tier="cheap"
    )
    states = await get_breaker_states(pool, [uuid.UUID(entry_id)])
    assert states[uuid.UUID(entry_id)].open is False
    assert states[uuid.UUID(entry_id)].consecutive_failures == 0


# ---------------------------------------------------------------------------
# Spend routing rules × dispatch-outcome circuit breaker (bu-14j0m)
#
# Decision (b): an operator spend rule is explicit human intent, so a
# breaker-open target is HONORED, not silently excluded. apply_spend_routing_rules
# surfaces the breaker state (SpendRoutingResult.breaker_open) and logs a warning;
# the model tuple is still re-routed to the rule target.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_spend_rule_to_breaker_open_model_honored_and_flagged(
    pool: asyncpg.Pool, caplog
) -> None:
    """A rule routing to a breaker-open model keeps the override AND flags the breaker.

    The rule-selected model is still returned (honored, not excluded), and
    ``SpendRoutingResult.breaker_open`` carries the open BreakerState so the
    spawner can record it on the dispatch-attempt trail. A visible warning is
    logged naming the model + rule.
    """
    import logging

    await pool.execute("TRUNCATE public.spend_rules")

    # Tier resolution picks the healthy default; the rule reroutes to the tripped one.
    await _insert_catalog_entry(
        pool, alias="brk-base", model_id="brk-base-model", complexity_tier="workhorse", priority=100
    )
    tripped_id = await _insert_catalog_entry(
        pool,
        alias="brk-target",
        model_id="brk-target-model",
        complexity_tier="workhorse",
        priority=1,
    )
    # Trip the breaker: the most recent _BREAKER_FAILURE_THRESHOLD attempts are all
    # runtime_failure and recent, so the breaker derives as open.
    for _ in range(_BREAKER_FAILURE_THRESHOLD):
        await _insert_dispatch_attempt(pool, catalog_entry_id=tripped_id, outcome="runtime_failure")

    resolved = await _resolved_tuple(pool, "general", Complexity.WORKHORSE)
    assert resolved[1] == "brk-base-model"

    await _insert_spend_rule(
        pool,
        position=0,
        condition={"butler": "general", "complexity": "workhorse"},
        action={"model": "brk-target-model"},
    )

    with caplog.at_level(logging.WARNING, logger="butlers.core.model_routing"):
        result = await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, resolved)

    # Rule honored: model IS re-routed to the breaker-open target.
    assert result.resolved[1] == "brk-target-model"
    assert str(result.resolved[3]) == tripped_id
    # Breaker state surfaced for the spawner's dispatch-attempt trail.
    assert result.breaker_open is not None
    assert result.breaker_open.open is True
    assert result.breaker_open.consecutive_failures >= _BREAKER_FAILURE_THRESHOLD
    # Visible warning naming the model and that the rule is honored, not excluded.
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("breaker is OPEN" in m and "brk-target-model" in m for m in warnings)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_spend_rule_to_healthy_model_no_breaker_flag(pool: asyncpg.Pool, caplog) -> None:
    """A rule routing to a healthy (breaker-closed) model sets no breaker flag/warning."""
    import logging

    await pool.execute("TRUNCATE public.spend_rules")

    await _insert_catalog_entry(
        pool, alias="ok-base", model_id="ok-base-model", complexity_tier="workhorse", priority=100
    )
    healthy_id = await _insert_catalog_entry(
        pool, alias="ok-target", model_id="ok-target-model", complexity_tier="workhorse", priority=1
    )
    # A single success = breaker closed.
    await _insert_dispatch_attempt(pool, catalog_entry_id=healthy_id, outcome="success")

    resolved = await _resolved_tuple(pool, "general", Complexity.WORKHORSE)
    await _insert_spend_rule(
        pool,
        position=0,
        condition={},
        action={"model": "ok-target-model"},
    )

    with caplog.at_level(logging.WARNING, logger="butlers.core.model_routing"):
        result = await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, resolved)

    assert result.resolved[1] == "ok-target-model"
    assert result.breaker_open is None
    assert not any("breaker is OPEN" in r.message for r in caplog.records)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_spend_rule_breaker_flag_matches_get_breaker_state(pool: asyncpg.Pool) -> None:
    """The surfaced breaker_open state matches get_breaker_state (PR #3170 semantics)."""
    await pool.execute("TRUNCATE public.spend_rules")

    await _insert_catalog_entry(
        pool, alias="sem-base", model_id="sem-base-model", complexity_tier="workhorse", priority=100
    )
    tripped_id = await _insert_catalog_entry(
        pool,
        alias="sem-target",
        model_id="sem-target-model",
        complexity_tier="workhorse",
        priority=1,
    )
    for _ in range(_BREAKER_FAILURE_THRESHOLD):
        await _insert_dispatch_attempt(pool, catalog_entry_id=tripped_id, outcome="runtime_failure")

    resolved = await _resolved_tuple(pool, "general", Complexity.WORKHORSE)
    await _insert_spend_rule(pool, position=0, condition={}, action={"model": "sem-target-model"})

    result = await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, resolved)
    direct = await get_breaker_state(pool, uuid.UUID(tripped_id))

    assert result.breaker_open is not None
    assert result.breaker_open.open == direct.open is True
    assert result.breaker_open.consecutive_failures == direct.consecutive_failures


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_breaker_override_outcome_ignored_by_breaker_derivation(pool: asyncpg.Pool) -> None:
    """The informational override outcome must not trip/reset the breaker.

    The breaker CTE counts only runtime_failure/success rows, so a
    breaker_open_override attempt row is inert to breaker derivation.
    """
    entry_id = await _insert_catalog_entry(
        pool, alias="inert", model_id="inert-model", complexity_tier="cheap"
    )
    # Only override rows — no runtime_failure/success history.
    for _ in range(_BREAKER_FAILURE_THRESHOLD + 2):
        await _insert_dispatch_attempt(
            pool, catalog_entry_id=entry_id, outcome=BREAKER_OPEN_RULE_OVERRIDE_OUTCOME
        )

    state = await get_breaker_state(pool, uuid.UUID(entry_id))
    assert state.open is False
    assert state.consecutive_failures == 0
    # Prefix constant is greppable on the trail (documents the recorded fact).
    assert BREAKER_OPEN_RULE_OVERRIDE_REASON_PREFIX == "Spend rule routed to breaker-open model"


# ---------------------------------------------------------------------------
# Evidence-based routing score integration (bu-ep4ks.13)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_get_routing_evidence_aggregates_success_and_failure_counts(
    pool: asyncpg.Pool,
) -> None:
    """get_routing_evidence counts success/runtime_failure and computes duration percentiles."""
    entry_id = await _insert_catalog_entry(
        pool, alias="evidence-a", model_id="evidence-a-model", complexity_tier="workhorse"
    )
    for duration in (1000, 2000, 3000):
        await _insert_dispatch_attempt(
            pool, catalog_entry_id=entry_id, outcome="success", duration_ms=duration
        )
    await _insert_dispatch_attempt(pool, catalog_entry_id=entry_id, outcome="runtime_failure")
    # Ignored outcomes must not count toward sample_count.
    await _insert_dispatch_attempt(pool, catalog_entry_id=entry_id, outcome="quota_skip")
    await _insert_dispatch_attempt(pool, catalog_entry_id=entry_id, outcome="suppressed")

    evidence = await get_routing_evidence(pool, [uuid.UUID(entry_id)])
    e = evidence[uuid.UUID(entry_id)]
    assert e.success_count == 3
    assert e.failure_count == 1
    assert e.sample_count == 4
    assert e.p50_duration_ms == 2000.0


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_get_routing_evidence_defaults_missing_ids_to_zero(pool: asyncpg.Pool) -> None:
    """A requested id with no dispatch_attempts rows gets zero-sample evidence, not omission."""
    entry_id = await _insert_catalog_entry(
        pool, alias="fresh-evidence", model_id="fresh-evidence-model", complexity_tier="workhorse"
    )
    evidence = await get_routing_evidence(pool, [uuid.UUID(entry_id)])
    e = evidence[uuid.UUID(entry_id)]
    assert e.success_count == 0
    assert e.failure_count == 0
    assert e.sample_count == 0
    assert e.p50_duration_ms is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_get_routing_scores_marks_insufficient_data_below_threshold(
    pool: asyncpg.Pool,
) -> None:
    """get_routing_scores never fabricates a score for a sparse-history entry."""
    entry_id = await _insert_catalog_entry(
        pool, alias="sparse", model_id="sparse-model", complexity_tier="workhorse"
    )
    for _ in range(_EVIDENCE_MIN_SAMPLES - 1):
        await _insert_dispatch_attempt(
            pool, catalog_entry_id=entry_id, outcome="success", duration_ms=1000
        )

    scores = await get_routing_scores(pool, [(uuid.UUID(entry_id), "sparse-model")])
    score = scores[uuid.UUID(entry_id)]
    assert score.insufficient_data is True
    assert score.score is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_prefers_evidence_based_winner_over_round_robin(
    pool: asyncpg.Pool,
) -> None:
    """Once both same-priority candidates have sufficient evidence, the better one always wins.

    Without evidence-based scoring this would round-robin between the two
    (cf. test_resolve_round_robin) regardless of how the 436s-slow one has
    been performing -- the exact gap bu-ep4ks.13 closes.
    """
    fast_id = await _insert_catalog_entry(
        pool, alias="fast", model_id="fast-model", complexity_tier="workhorse", priority=10
    )
    slow_id = await _insert_catalog_entry(
        pool, alias="slow", model_id="slow-model", complexity_tier="workhorse", priority=10
    )
    for _ in range(_EVIDENCE_MIN_SAMPLES):
        await _insert_dispatch_attempt(
            pool, catalog_entry_id=fast_id, outcome="success", duration_ms=500
        )
        await _insert_dispatch_attempt(
            pool, catalog_entry_id=slow_id, outcome="success", duration_ms=436_000
        )

    for _ in range(5):
        result = await resolve_model(
            pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False
        )
        assert result is not None
        assert result[1] == "fast-model", (
            f"Expected the fast, reliable model to always win once evidence "
            f"exists, got {result[1]!r}"
        )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_falls_back_to_round_robin_when_evidence_insufficient(
    pool: asyncpg.Pool,
) -> None:
    """With no dispatch history at all, resolution still round-robins exactly as before."""
    await _insert_catalog_entry(
        pool,
        alias="no-history-a",
        model_id="no-history-a-model",
        complexity_tier="workhorse",
        priority=10,
    )
    await _insert_catalog_entry(
        pool,
        alias="no-history-b",
        model_id="no-history-b-model",
        complexity_tier="workhorse",
        priority=10,
    )

    r1 = await resolve_model(pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False)
    r2 = await resolve_model(pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False)
    r3 = await resolve_model(pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False)
    assert r1 is not None and r2 is not None and r3 is not None
    seen = {r1[1], r2[1]}
    assert seen == {"no-history-a-model", "no-history-b-model"}
    assert r3[1] == r1[1]  # wraps back to the first after both are rotated through


# ---------------------------------------------------------------------------
# Quota-aware resolve fold (bu-ep4ks.13 follow-up / bu-k9te9)
#
# "Fold the quota/ceiling pre-spawn gates into the resolve CTE": resolve_model_with_effective_tier
# (quota_aware=True) folds a per-catalog-entry token-quota check into the same round trip as
# tier/breaker/priority resolution. Quota-unaware callers (resolve_model, and
# resolve_model_with_effective_tier's own default) must be COMPLETELY unaffected -- the quota_ok
# SQL column is purely additive. See TierQuotaExhausted's docstring for the exact contract this
# section verifies.
# ---------------------------------------------------------------------------


async def _insert_limits(
    pool: asyncpg.Pool,
    *,
    catalog_entry_id: str,
    limit_24h: int | None = None,
    limit_30d: int | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO public.token_limits (catalog_entry_id, limit_24h, limit_30d)
        VALUES ($1, $2, $3)
        """,
        uuid.UUID(catalog_entry_id),
        limit_24h,
        limit_30d,
    )


async def _insert_ledger_row(
    pool: asyncpg.Pool, *, catalog_entry_id: str, input_tokens: int = 0, output_tokens: int = 0
) -> None:
    await pool.execute(
        """
        INSERT INTO public.token_usage_ledger
            (catalog_entry_id, butler_name, input_tokens, output_tokens)
        VALUES ($1, 'general', $2, $3)
        """,
        uuid.UUID(catalog_entry_id),
        input_tokens,
        output_tokens,
    )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_quota_aware_no_limits_row_is_pure_fast_path(pool: asyncpg.Pool) -> None:
    """No token_limits row -> quota_aware=True returns exactly what quota_aware=False would."""
    await _insert_catalog_entry(
        pool, alias="qa-no-limits", model_id="qa-no-limits-model", priority=10
    )
    unaware = await resolve_model_with_effective_tier(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    aware = await resolve_model_with_effective_tier(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False, quota_aware=True
    )
    assert unaware is not None and aware is not None
    assert unaware == aware


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_quota_aware_boundary_just_under_limit_allowed(pool: asyncpg.Pool) -> None:
    """usage == limit - 1 -> quota_ok, quota_aware=True returns normally (no exception)."""
    entry_id = await _insert_catalog_entry(
        pool, alias="qa-under", model_id="qa-under-model", priority=10
    )
    await _insert_limits(pool, catalog_entry_id=entry_id, limit_24h=100)
    await _insert_ledger_row(pool, catalog_entry_id=entry_id, input_tokens=60, output_tokens=39)

    result = await resolve_model_with_effective_tier(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False, quota_aware=True
    )
    assert result is not None
    assert str(result[3]) == entry_id


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_quota_aware_boundary_exactly_at_limit_raises_exhausted(
    pool: asyncpg.Pool,
) -> None:
    """usage == limit (>=) -> blocked, matching check_token_quota's exact boundary."""
    entry_id = await _insert_catalog_entry(
        pool, alias="qa-exact", model_id="qa-exact-model", priority=10
    )
    await _insert_limits(pool, catalog_entry_id=entry_id, limit_24h=100)
    await _insert_ledger_row(pool, catalog_entry_id=entry_id, input_tokens=60, output_tokens=40)

    with pytest.raises(TierQuotaExhausted) as exc_info:
        await resolve_model_with_effective_tier(
            pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False, quota_aware=True
        )
    assert exc_info.value.effective_tier == "workhorse"
    # The representative must be exactly what a quota-unaware resolve would have picked --
    # the caller falls back to the pre-existing sequential quota loop starting from it.
    unaware = await resolve_model_with_effective_tier(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    assert exc_info.value.representative == unaware


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_quota_aware_30d_boundary_exactly_at_limit_raises_exhausted(
    pool: asyncpg.Pool,
) -> None:
    """30d window boundary: usage == limit_30d (24h unlimited) also blocks."""
    entry_id = await _insert_catalog_entry(
        pool, alias="qa-exact-30d", model_id="qa-exact-30d-model", priority=10
    )
    await _insert_limits(pool, catalog_entry_id=entry_id, limit_30d=200)
    await _insert_ledger_row(pool, catalog_entry_id=entry_id, input_tokens=120, output_tokens=80)

    with pytest.raises(TierQuotaExhausted):
        await resolve_model_with_effective_tier(
            pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False, quota_aware=True
        )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_quota_aware_mixed_tied_peers_conservatively_raises(
    pool: asyncpg.Pool,
) -> None:
    """One of two tied top-priority peers is quota-blocked -> TierQuotaExhausted is still raised.

    Deliberately conservative (see TierQuotaExhausted's docstring): the fold does not attempt to
    pick among a mixed quota-ok/quota-blocked tie itself, because that could select a different
    candidate than the deterministic same-tier failover the caller falls back to for a real
    exhaustion. Verifies the fold never silently narrows a tie on the caller's behalf.
    """
    ok_id = await _insert_catalog_entry(
        pool, alias="qa-mixed-ok", model_id="qa-mixed-ok-model", priority=10
    )
    blocked_id = await _insert_catalog_entry(
        pool, alias="qa-mixed-blocked", model_id="qa-mixed-blocked-model", priority=10
    )
    await _insert_limits(pool, catalog_entry_id=blocked_id, limit_24h=100)
    await _insert_ledger_row(pool, catalog_entry_id=blocked_id, input_tokens=100, output_tokens=0)
    del ok_id  # unused beyond seeding a quota-ok tied peer

    with pytest.raises(TierQuotaExhausted):
        await resolve_model_with_effective_tier(
            pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False, quota_aware=True
        )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_quota_aware_does_not_change_tier_fallthrough(pool: asyncpg.Pool) -> None:
    """Tier-fallthrough stays governed by breaker/enabled state only, never by quota.

    A workhorse candidate that is quota-blocked must NOT cause resolution to fall through to
    the next canonical tier (cheap) -- that would silently swap a hard quota DENY for a
    different, un-vetted model. TierQuotaExhausted must be raised for the workhorse tier
    instead, exactly mirroring how the pre-fold sequential quota loop never fell through tiers
    either (it only ever searched within `_failover_effective_tier`).
    """
    workhorse_id = await _insert_catalog_entry(
        pool,
        alias="qa-fallthrough-workhorse",
        model_id="qa-fallthrough-workhorse-model",
        complexity_tier="workhorse",
        priority=10,
    )
    await _insert_catalog_entry(
        pool,
        alias="qa-fallthrough-cheap",
        model_id="qa-fallthrough-cheap-model",
        complexity_tier="cheap",
        priority=10,
    )
    await _insert_limits(pool, catalog_entry_id=workhorse_id, limit_24h=100)
    await _insert_ledger_row(pool, catalog_entry_id=workhorse_id, input_tokens=100, output_tokens=0)

    with pytest.raises(TierQuotaExhausted) as exc_info:
        await resolve_model_with_effective_tier(
            pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=True, quota_aware=True
        )
    assert exc_info.value.effective_tier == "workhorse"
    assert exc_info.value.representative[1] == "qa-fallthrough-workhorse-model"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_quota_unaware_default_ignores_quota_ok_column(pool: asyncpg.Pool) -> None:
    """Regression guard: quota_aware defaults to False and quota status never filters selection.

    Every existing caller of resolve_model / resolve_model_with_effective_tier (Models tab,
    calendar quick_add, QA dispatch, healing dispatch) must be provably unaffected by the
    quota_ok column ``_RESOLVE_SQL`` now carries -- a fully quota-exhausted entry is still
    resolved normally when quota_aware is left at its default.
    """
    entry_id = await _insert_catalog_entry(
        pool, alias="qa-default-ignored", model_id="qa-default-ignored-model", priority=10
    )
    await _insert_limits(pool, catalog_entry_id=entry_id, limit_24h=1)
    await _insert_ledger_row(pool, catalog_entry_id=entry_id, input_tokens=999, output_tokens=0)

    # resolve_model: never raises, never filters on quota.
    via_resolve_model = await resolve_model(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    assert via_resolve_model is not None
    assert str(via_resolve_model[3]) == entry_id

    # resolve_model_with_effective_tier default (quota_aware unset): same behavior.
    default_result = await resolve_model_with_effective_tier(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False
    )
    assert default_result is not None
    assert str(default_result[3]) == entry_id

    # Explicit quota_aware=False: identical.
    explicit_false = await resolve_model_with_effective_tier(
        pool, "general", Complexity.WORKHORSE, allow_tier_fallthrough=False, quota_aware=False
    )
    assert explicit_false == default_result


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_routing_cache_masks_a_new_candidate_until_cleared(pool: asyncpg.Pool) -> None:
    """A cache hit serves the stale-but-still-valid candidate set within the TTL window.

    Documents the accepted staleness bound this cache trades for fewer round trips:
    a second, higher-priority candidate added right after the first resolve is not seen
    until the cache is cleared (in production: until the short TTL elapses).
    """
    await _insert_catalog_entry(
        pool, alias="cache-first", model_id="cache-first-model", priority=10
    )
    first = await resolve_model(pool, "cache-butler", Complexity.WORKHORSE)
    assert first is not None and first[1] == "cache-first-model"

    await _insert_catalog_entry(
        pool, alias="cache-second", model_id="cache-second-model", priority=100
    )
    still_cached = await resolve_model(pool, "cache-butler", Complexity.WORKHORSE)
    assert still_cached is not None and still_cached[1] == "cache-first-model", (
        "Expected the cached (now stale) candidate set within the TTL window"
    )

    model_routing_module.clear_routing_decision_cache()
    fresh = await resolve_model(pool, "cache-butler", Complexity.WORKHORSE)
    assert fresh is not None and fresh[1] == "cache-second-model"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_routing_cache_round_robin_counter_still_increments_on_cache_hit(
    pool: asyncpg.Pool,
) -> None:
    """The round-robin counter increments every call even when candidate rows are cached.

    The counter increment was split out of the cached candidate-resolution query
    specifically so caching never masks this: several pre-existing tests
    (test_resolve_round_robin, test_empty_tier_fallthrough_does_not_increment_skipped_counters)
    depend on it incrementing exactly once per logical resolve_model call.
    """
    await _insert_catalog_entry(pool, alias="rr-cache-only", model_id="rr-cache-model", priority=10)

    for _ in range(3):
        r = await resolve_model(pool, "rr-cache-butler", Complexity.WORKHORSE)
        assert r is not None and r[1] == "rr-cache-model"

    row = await pool.fetchrow(
        "SELECT counter FROM public.model_round_robin_counters "
        "WHERE butler_name = $1 AND complexity_tier = 'workhorse'",
        "rr-cache-butler",
    )
    assert row is not None and row["counter"] == 2, (
        "Expected the counter to increment on all 3 calls (0, 1, 2) despite the "
        "candidate row set being served from cache after the first"
    )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_routing_cache_never_used_by_quota_aware_resolution(pool: asyncpg.Pool) -> None:
    """quota_aware=True always sees a newly-added candidate immediately -- never cached."""
    await _insert_catalog_entry(
        pool, alias="qa-cache-first", model_id="qa-cache-first-model", priority=10
    )
    first = await resolve_model_with_effective_tier(
        pool, "qa-cache-butler", Complexity.WORKHORSE, quota_aware=True
    )
    assert first is not None and first[1] == "qa-cache-first-model"

    await _insert_catalog_entry(
        pool, alias="qa-cache-second", model_id="qa-cache-second-model", priority=100
    )
    second = await resolve_model_with_effective_tier(
        pool, "qa-cache-butler", Complexity.WORKHORSE, quota_aware=True
    )
    assert second is not None and second[1] == "qa-cache-second-model", (
        "quota_aware=True must never serve a cached (potentially quota-stale) answer"
    )


# ---------------------------------------------------------------------------
# Routing evidence with NULL-duration rows (bu-ot2ug)
# ---------------------------------------------------------------------------
#
# The SQL evidence CTE excludes NULL-duration rows from the p95 percentile
# calculation but includes them in the success count. This test verifies
# both behaviors are correct in isolation and when combined through the
# full routing score computation.


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_routing_evidence_mixed_null_and_nonnull_duration_successes(
    pool: asyncpg.Pool,
) -> None:
    """Evidence correctly excludes NULL durations from percentiles but includes in success count.

    Pre-migration rows (duration_ms NULL) and post-migration rows (real duration_ms)
    must both count toward success_count, but only post-migration rows contribute
    to percentile calculations.
    """
    entry_id = await _insert_catalog_entry(
        pool, alias="mixed-duration", model_id="mixed-model", complexity_tier="workhorse"
    )

    # Insert success rows with various duration values:
    # - 3 pre-migration style (NULL duration)
    for _ in range(3):
        await _insert_dispatch_attempt(
            pool, catalog_entry_id=entry_id, outcome="success", duration_ms=None
        )
    # - 2 post-migration style with concrete durations: 1000ms, 2000ms
    await _insert_dispatch_attempt(
        pool, catalog_entry_id=entry_id, outcome="success", duration_ms=1000
    )
    await _insert_dispatch_attempt(
        pool, catalog_entry_id=entry_id, outcome="success", duration_ms=2000
    )
    # - 2 failures (to test failure_count is separate)
    for _ in range(2):
        await _insert_dispatch_attempt(pool, catalog_entry_id=entry_id, outcome="runtime_failure")

    # Fetch evidence for this entry
    evidence_dict = await get_routing_evidence(pool, [uuid.UUID(entry_id)])
    evidence = evidence_dict[uuid.UUID(entry_id)]

    # success_count must include ALL successes (3 NULL + 2 with durations = 5)
    assert evidence.success_count == 5, (
        f"Expected success_count=5 (3 NULL + 2 with durations); got {evidence.success_count}"
    )

    # failure_count counts only the runtime_failure outcomes
    assert evidence.failure_count == 2, f"Expected failure_count=2; got {evidence.failure_count}"

    # p50 should be the median of [1000, 2000] = 1500.0
    assert evidence.p50_duration_ms == pytest.approx(1500.0), (
        f"Expected p50=1500.0 (median of [1000, 2000]); got {evidence.p50_duration_ms}"
    )

    # p95 should be 1950.0 for [1000, 2000]
    # PERCENTILE_CONT uses linear interpolation: position = 0.95 * (n-1) = 0.95 * 1 = 0.95
    # Value = 1000 + 0.95 * (2000 - 1000) = 1000 + 950 = 1950.0
    assert evidence.p95_duration_ms == pytest.approx(1950.0), (
        f"Expected p95=1950.0 (95th percentile of [1000, 2000]); got {evidence.p95_duration_ms}"
    )

    # Compute routing score: should have sufficient data and a well-defined score
    score = compute_routing_score(evidence, cost_usd_per_call=0.0)
    assert score.insufficient_data is False
    assert score.sample_count == 7, f"Expected sample_count=7 (5+2); got {score.sample_count}"
    assert score.success_rate == pytest.approx(5 / 7), (
        f"Expected success_rate=5/7≈0.714; got {score.success_rate}"
    )
    assert score.score is not None and score.score > 0, (
        "Expected a positive score with 5 successes and 2 failures"
    )
    # Verify latency component is set to p95 (not p50)
    assert score.latency_p95_ms == pytest.approx(1950.0)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_routing_evidence_only_null_duration_successes_yields_defined_score(
    pool: asyncpg.Pool,
) -> None:
    """A window with only NULL-duration successes (no failures) yields a well-defined score.

    Pre-migration entries may have ONLY NULL-duration successes; this must
    not crash or produce NaN. Since both p50 and p95 will be NULL, the
    latency component of the score should handle that gracefully.
    """
    entry_id = await _insert_catalog_entry(
        pool,
        alias="null-only-success",
        model_id="null-only-model",
        complexity_tier="workhorse",
    )

    # Insert 5 successes, all with NULL duration (pre-migration style only)
    for _ in range(5):
        await _insert_dispatch_attempt(
            pool, catalog_entry_id=entry_id, outcome="success", duration_ms=None
        )

    # Fetch evidence
    evidence_dict = await get_routing_evidence(pool, [uuid.UUID(entry_id)])
    evidence = evidence_dict[uuid.UUID(entry_id)]

    # success_count = 5
    assert evidence.success_count == 5
    # failure_count = 0
    assert evidence.failure_count == 0
    # Both percentiles should be None (no non-NULL durations to measure)
    assert evidence.p50_duration_ms is None
    assert evidence.p95_duration_ms is None

    # Compute routing score: must not crash or return NaN
    score = compute_routing_score(evidence, cost_usd_per_call=0.01)
    assert score.insufficient_data is False
    assert score.sample_count == 5
    assert score.success_rate == pytest.approx(1.0)
    assert score.score is not None, "Expected a well-defined score even with NULL durations"
    assert not any(x is None for x in [score.score, score.success_rate]), (
        "Score components must not be None"
    )
    # With 100% success rate, latency_ms=0 (since both p95 and p50 are None),
    # and non-zero cost, the score should be positive
    assert score.score > 0


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_routing_evidence_null_durations_do_not_affect_percentile_calculation(
    pool: asyncpg.Pool,
) -> None:
    """NULL-duration rows are strictly excluded from p50/p95 calculation.

    This test inserts many NULL-duration successes alongside a small number of
    non-NULL successes, and verifies the percentile is computed only over the
    non-NULL subset, not over a 0-padded representation.
    """
    entry_id = await _insert_catalog_entry(
        pool,
        alias="null-not-padded",
        model_id="null-not-padded-model",
        complexity_tier="workhorse",
    )

    # Insert 10 NULL-duration successes
    for _ in range(10):
        await _insert_dispatch_attempt(
            pool, catalog_entry_id=entry_id, outcome="success", duration_ms=None
        )
    # Insert 4 non-NULL successes: 100ms, 200ms, 300ms, 400ms
    # Sorted: [100, 200, 300, 400]
    # p50 (median) = (200 + 300) / 2 = 250
    # p95 = linear interpolation at position 0.95*(4-1) = 2.85 → between 300 and 400
    #     = 300 + 0.85*(400-300) = 300 + 85 = 385
    for duration in [100, 200, 300, 400]:
        await _insert_dispatch_attempt(
            pool, catalog_entry_id=entry_id, outcome="success", duration_ms=duration
        )

    evidence_dict = await get_routing_evidence(pool, [uuid.UUID(entry_id)])
    evidence = evidence_dict[uuid.UUID(entry_id)]

    # Total success_count must include all 14 successes
    assert evidence.success_count == 14

    # p50 should be 250.0 (median of [100, 200, 300, 400])
    assert evidence.p50_duration_ms == pytest.approx(250.0), (
        f"Expected p50=250.0; got {evidence.p50_duration_ms}"
    )

    # p95 for [100, 200, 300, 400]:
    # Position = 0.95 * (4-1) = 2.85, between index 2 (300) and 3 (400)
    # Value = 300 + 0.85 * 100 = 385
    assert evidence.p95_duration_ms == pytest.approx(385.0), (
        f"Expected p95≈385.0; got {evidence.p95_duration_ms}"
    )

    # Verify the score still computes correctly
    score = compute_routing_score(evidence, cost_usd_per_call=0.0)
    assert score.insufficient_data is False
    assert score.sample_count == 14
    assert score.score is not None and score.score > 0

"""Tests for intent-aware model resolution (``resolve_dispatch``, bu-6jv4m.7).

The property under test is ordering: hard capability / context / deadline / budget
fit is applied to every candidate BEFORE the winning tier is chosen, before priority
narrows the field, and before the evidence/round-robin tie-break. The existing
resolver narrows first, which is why a top-priority entry that cannot do the job can
win today -- ``public.model_catalog``'s seeded ``api-haiku-cheap`` (priority 30, top
of the ``cheap`` tier, backed by an adapter that raises on any MCP tool wiring) is
exactly that shape.

The control test ``test_intent_requiring_nothing_matches_legacy_selection`` is the
migration-safety claim: an intent with no requirements selects exactly what the
pre-existing resolver selects, so nothing about ranking changed.
"""

from __future__ import annotations

import json
import shutil
import uuid

import asyncpg
import pytest

from butlers.core.dispatch_intent import (
    DISPATCH_POLICY_VERSION,
    Consequence,
    DispatchIntent,
    FitCode,
    derive_dispatch_intent,
)
from butlers.core.model_capabilities import ModelFeature
from butlers.core.model_routing import (
    CandidateOutcome,
    Complexity,
    TierQuotaExhausted,
    clear_routing_decision_cache,
    resolve_dispatch,
    resolve_model_with_effective_tier,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

BUTLER = "general"

# Every trigger source except ``healing``/``qa`` gets MCP tool wiring in the spawner,
# so this is the intent shape that matters most in production.
TOOL_INTENT = derive_dispatch_intent("external", Complexity.CHEAP)
NO_REQUIREMENTS_INTENT = derive_dispatch_intent("healing", Complexity.CHEAP)


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    """Pool with catalog, override, counter, and quota tables cleared between tests."""
    clear_routing_decision_cache()
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    await p.execute(
        "TRUNCATE public.model_round_robin_counters, public.butler_model_overrides, "
        "public.token_limits, public.token_usage_ledger, public.model_catalog CASCADE"
    )
    yield p
    await p.close()


async def _insert_entry(
    pool: asyncpg.Pool,
    *,
    alias: str,
    runtime_type: str = "claude",
    complexity_tier: str = "cheap",
    priority: int = 0,
    capabilities: str = "{}",
    max_context_tokens: int | None = None,
) -> uuid.UUID:
    """Insert one enabled catalog entry. Model ids are synthetic and unpriced."""
    row = await pool.fetchrow(
        """
        INSERT INTO public.model_catalog
            (alias, runtime_type, model_id, extra_args, complexity_tier, enabled,
             priority, session_timeout_s, capabilities, max_context_tokens)
        VALUES ($1, $2, $3, '[]'::jsonb, $4, true, $5, 1800, $6::jsonb, $7)
        RETURNING id
        """,
        alias,
        runtime_type,
        f"synthetic-{alias}",
        complexity_tier,
        priority,
        capabilities,
        max_context_tokens,
    )
    return row["id"]


def _outcome(resolution, entry_id: uuid.UUID) -> CandidateOutcome:
    for candidate in resolution.candidates:
        if candidate.catalog_entry_id == entry_id:
            return candidate.outcome
    raise AssertionError(f"entry {entry_id} missing from receipt")


def _codes(resolution, entry_id: uuid.UUID) -> set[FitCode]:
    for candidate in resolution.candidates:
        if candidate.catalog_entry_id == entry_id:
            return {f.code for f in candidate.exclusions}
    raise AssertionError(f"entry {entry_id} missing from receipt")


async def test_hard_fit_excludes_tool_incapable_top_priority_entry(pool: asyncpg.Pool) -> None:
    """The live defect: a top-priority entry that cannot accept tools must not win.

    The first assertion is the control -- it shows the pre-existing resolver really
    does pick the unusable entry, so the fix is answering a real failure rather than
    a hypothetical one.
    """
    api_id = await _insert_entry(pool, alias="fit-api", runtime_type="api", priority=30)
    claude_id = await _insert_entry(pool, alias="fit-claude", runtime_type="claude", priority=10)

    legacy = await resolve_model_with_effective_tier(
        pool, BUTLER, Complexity.CHEAP, allow_tier_fallthrough=False
    )
    assert legacy is not None
    assert legacy[3] == api_id, "control failed: legacy resolver no longer picks the api entry"

    resolution = await resolve_dispatch(pool, BUTLER, TOOL_INTENT, allow_tier_fallthrough=False)
    assert resolution.selection is not None
    assert resolution.selection[3] == claude_id
    assert resolution.winner_reason == "sole_candidate"
    assert _outcome(resolution, api_id) is CandidateOutcome.EXCLUDED_HARD_FIT
    assert _codes(resolution, api_id) == {FitCode.CAPABILITY_UNSUPPORTED}
    assert _outcome(resolution, claude_id) is CandidateOutcome.SELECTED


async def test_intent_requiring_nothing_matches_legacy_selection(pool: asyncpg.Pool) -> None:
    """Migration safety: no requirements means no behaviour change, including the winner."""
    api_id = await _insert_entry(pool, alias="ctl-api", runtime_type="api", priority=30)
    await _insert_entry(pool, alias="ctl-claude", runtime_type="claude", priority=10)

    legacy = await resolve_model_with_effective_tier(
        pool, BUTLER, Complexity.CHEAP, allow_tier_fallthrough=False
    )
    resolution = await resolve_dispatch(
        pool, BUTLER, NO_REQUIREMENTS_INTENT, allow_tier_fallthrough=False
    )
    via_kwarg = await resolve_model_with_effective_tier(
        pool,
        BUTLER,
        Complexity.CHEAP,
        allow_tier_fallthrough=False,
        intent=NO_REQUIREMENTS_INTENT,
    )
    assert legacy is not None
    assert legacy[3] == api_id
    assert resolution.selection == legacy
    assert via_kwarg == legacy


async def test_falls_through_when_the_whole_tier_misfits(pool: asyncpg.Pool) -> None:
    """A tier with no fitting candidate is not a winning tier, so fallthrough continues."""
    api_id = await _insert_entry(pool, alias="ft-api", runtime_type="api", priority=30)
    specialty_id = await _insert_entry(
        pool, alias="ft-claude", runtime_type="claude", complexity_tier="specialty", priority=5
    )

    resolution = await resolve_dispatch(pool, BUTLER, TOOL_INTENT, allow_tier_fallthrough=True)
    assert resolution.selection is not None
    assert resolution.selection[3] == specialty_id
    assert resolution.effective_tier == "specialty"
    assert resolution.requested_intent.complexity_tier == "cheap"
    assert resolution.effective_intent.complexity_tier == "specialty"
    # The intents differ ONLY in the tier -- the requirement envelope is not rewritten
    # on the way down.
    assert resolution.requested_intent.required_features == (
        resolution.effective_intent.required_features
    )
    assert _outcome(resolution, api_id) is CandidateOutcome.EXCLUDED_HARD_FIT


async def test_no_selection_when_nothing_fits_anywhere(pool: asyncpg.Pool) -> None:
    """Distinct from "no catalog entries": the receipt explains why the fleet is stuck."""
    api_id = await _insert_entry(pool, alias="ns-api", runtime_type="api", priority=30)
    api2_id = await _insert_entry(
        pool, alias="ns-api2", runtime_type="api", complexity_tier="local", priority=5
    )

    resolution = await resolve_dispatch(pool, BUTLER, TOOL_INTENT)
    assert resolution.selection is None
    assert resolution.winner_reason is None
    assert resolution.effective_tier is None
    assert _outcome(resolution, api_id) is CandidateOutcome.EXCLUDED_HARD_FIT
    assert _outcome(resolution, api2_id) is CandidateOutcome.EXCLUDED_HARD_FIT

    # The 6-tuple caller sees None and takes its existing static-fallback path.
    assert (
        await resolve_model_with_effective_tier(pool, BUTLER, Complexity.CHEAP, intent=TOOL_INTENT)
        is None
    )


async def test_context_floor_excludes_undeclared_window(pool: asyncpg.Pool) -> None:
    """An undeclared context window cannot satisfy a floor, even at top priority."""
    undeclared_id = await _insert_entry(pool, alias="cx-undeclared", priority=30)
    declared_id = await _insert_entry(
        pool, alias="cx-declared", priority=10, max_context_tokens=200_000
    )

    intent = DispatchIntent(
        trigger_class="external",
        complexity_tier="cheap",
        consequence=Consequence.EXTERNAL,
        required_features=frozenset({ModelFeature.TOOL_USE}),
        min_context_tokens=100_000,
    )
    resolution = await resolve_dispatch(pool, BUTLER, intent, allow_tier_fallthrough=False)
    assert resolution.selection is not None
    assert resolution.selection[3] == declared_id
    assert _codes(resolution, undeclared_id) == {FitCode.CONTEXT_WINDOW_UNKNOWN}


async def test_row_capabilities_override_the_adapter_baseline(pool: asyncpg.Pool) -> None:
    """An operator can declare per-entry truth the adapter class cannot know."""
    api_id = await _insert_entry(
        pool,
        alias="ov-api",
        runtime_type="api",
        priority=30,
        capabilities=json.dumps({"tool_use": True}),
    )
    await _insert_entry(pool, alias="ov-claude", priority=10)

    resolution = await resolve_dispatch(pool, BUTLER, TOOL_INTENT, allow_tier_fallthrough=False)
    assert resolution.selection is not None
    assert resolution.selection[3] == api_id


async def test_unusable_capability_envelope_excludes_the_entry(pool: asyncpg.Pool) -> None:
    """A stored envelope the descriptor layer cannot parse fails closed, not open.

    The column CHECK only pins the JSON shape (the feature vocabulary lives with the
    adapters), so an unknown key is storable and must be caught during resolution.
    """
    broken_id = await _insert_entry(
        pool, alias="bad-envelope", priority=30, capabilities=json.dumps({"no_such_feature": True})
    )
    good_id = await _insert_entry(pool, alias="good-envelope", priority=10)

    resolution = await resolve_dispatch(pool, BUTLER, TOOL_INTENT, allow_tier_fallthrough=False)
    assert resolution.selection is not None
    assert resolution.selection[3] == good_id
    assert _codes(resolution, broken_id) == {FitCode.CAPABILITY_DESCRIPTOR_INVALID}


async def test_lower_priority_fitting_peer_is_recorded_not_selected(pool: asyncpg.Pool) -> None:
    """Fit filters; priority still decides among what is left."""
    top_id = await _insert_entry(pool, alias="pr-top", priority=30)
    low_id = await _insert_entry(pool, alias="pr-low", priority=10)

    resolution = await resolve_dispatch(pool, BUTLER, TOOL_INTENT, allow_tier_fallthrough=False)
    assert resolution.selection is not None
    assert resolution.selection[3] == top_id
    assert _outcome(resolution, low_id) is CandidateOutcome.NOT_TOP_PRIORITY


async def test_tied_fitting_peers_still_round_robin(pool: asyncpg.Pool) -> None:
    """Below the evidence threshold the tie-break is the existing round-robin counter."""
    first_id = await _insert_entry(pool, alias="rr-a", priority=10)
    second_id = await _insert_entry(pool, alias="rr-b", priority=10)

    winners = []
    for _ in range(4):
        resolution = await resolve_dispatch(pool, BUTLER, TOOL_INTENT, allow_tier_fallthrough=False)
        assert resolution.selection is not None
        assert resolution.winner_reason == "round_robin"
        winners.append(resolution.selection[3])

    assert set(winners) == {first_id, second_id}
    assert winners[0] != winners[1]


async def test_quota_block_raises_with_the_receipt_attached(pool: asyncpg.Pool) -> None:
    """A quota-exhausted tier keeps the existing contract and gains an explanation."""
    entry_id = await _insert_entry(pool, alias="q-claude", priority=10)
    await pool.execute(
        "INSERT INTO public.token_limits (catalog_entry_id, limit_24h) VALUES ($1, 100)",
        entry_id,
    )
    await pool.execute(
        """
        INSERT INTO public.token_usage_ledger
            (catalog_entry_id, butler_name, input_tokens, output_tokens)
        VALUES ($1, $2, 60, 40)
        """,
        entry_id,
        BUTLER,
    )

    with pytest.raises(TierQuotaExhausted) as exc_info:
        await resolve_dispatch(
            pool, BUTLER, TOOL_INTENT, allow_tier_fallthrough=False, quota_aware=True
        )

    exc = exc_info.value
    assert exc.effective_tier == "cheap"
    assert exc.representative[3] == entry_id
    assert exc.resolution is not None
    assert _outcome(exc.resolution, entry_id) is CandidateOutcome.EXCLUDED_QUOTA


async def test_receipt_is_json_safe_and_prompt_free(pool: asyncpg.Pool) -> None:
    """The receipt has to be storable and showable without redaction."""
    api_id = await _insert_entry(pool, alias="rc-api", runtime_type="api", priority=30)
    claude_id = await _insert_entry(pool, alias="rc-claude", priority=10)

    resolution = await resolve_dispatch(pool, BUTLER, TOOL_INTENT, allow_tier_fallthrough=False)
    payload = resolution.describe()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["policy_version"] == DISPATCH_POLICY_VERSION
    assert payload["winner"]["catalog_entry_id"] == str(claude_id)
    assert payload["winner"]["reason"] == "sole_candidate"

    recorded = {c["catalog_entry_id"] for c in payload["candidates"]}
    assert recorded == {str(api_id), str(claude_id)}
    excluded = next(c for c in payload["candidates"] if c["catalog_entry_id"] == str(api_id))
    assert excluded["outcome"] == "excluded_hard_fit"
    assert excluded["exclusions"][0]["code"] == "capability_unsupported"
    # Evidence age is present as a field and null until an attempt exists.
    assert excluded["evidence_age_s"] is None
    assert excluded["evidence_samples"] == 0

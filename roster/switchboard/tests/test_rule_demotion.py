"""Unit tests for demotion via spot-check sampling (bu-x55k3, bead 5 of 7).

Covers the pure ``parse_rule_action``/``compute_agreement`` helpers directly,
plus ``maybe_create_demotion_suggestion`` orchestration against a small fake
pool — no DB required. See
``tests/integration/test_switchboard_spot_check_index_migration.py`` for the
end-to-end round-trip against real Postgres (migration sw_021's index +
real-insert coverage), and ``tests/modules/test_module_pipeline.py``'s
``TestMessagePipelineDemotionSpotCheck`` for the pipeline wiring that calls
into this module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from butlers.tools.switchboard.routing.rule_demotion import (
    DEFAULT_AGREEMENT_THRESHOLD,
    DEFAULT_AGREEMENT_WINDOW,
    DEFAULT_MIN_SAMPLES,
    compute_agreement,
    maybe_create_demotion_suggestion,
    parse_rule_action,
)

pytestmark = pytest.mark.unit


def _ts(*, minutes_ago: int) -> datetime:
    return datetime(2026, 7, 6, 12, 0, tzinfo=UTC) - timedelta(minutes=minutes_ago)


# ---------------------------------------------------------------------------
# parse_rule_action
# ---------------------------------------------------------------------------


class TestParseRuleAction:
    def test_route_to_action_splits_target(self):
        assert parse_rule_action("route_to:finance") == ("route_to", "finance")

    def test_skip_action_has_no_target(self):
        assert parse_rule_action("skip") == ("skip", None)

    def test_metadata_only_action_has_no_target(self):
        assert parse_rule_action("metadata_only") == ("metadata_only", None)

    def test_route_to_with_empty_target_normalizes_to_none(self):
        assert parse_rule_action("route_to:") == ("route_to", None)


# ---------------------------------------------------------------------------
# compute_agreement — pure scoring, no I/O
# ---------------------------------------------------------------------------


class TestComputeAgreement:
    def test_empty_rows_has_no_score(self):
        result = compute_agreement([], rule_action="route_to", rule_target="finance")
        assert result.sample_count == 0
        assert result.agreement_score is None

    def test_all_agree_scores_100_percent(self):
        rows = [
            {"verdict_action": "route_to", "verdict_target": "finance"},
            {"verdict_action": "route_to", "verdict_target": "finance"},
        ]
        result = compute_agreement(rows, rule_action="route_to", rule_target="finance")
        assert result.sample_count == 2
        assert result.agreement_count == 2
        assert result.agreement_score == 1.0

    def test_all_disagree_scores_zero(self):
        rows = [
            {"verdict_action": "route_to", "verdict_target": "general"},
            {"verdict_action": "route_to", "verdict_target": "general"},
        ]
        result = compute_agreement(rows, rule_action="route_to", rule_target="finance")
        assert result.agreement_score == 0.0

    def test_mixed_agreement_scores_fraction(self):
        rows = [
            {"verdict_action": "route_to", "verdict_target": "finance"},  # agree
            {"verdict_action": "route_to", "verdict_target": "finance"},  # agree
            {"verdict_action": "route_to", "verdict_target": "general"},  # disagree
            {"verdict_action": "route_to", "verdict_target": "finance"},  # agree
        ]
        result = compute_agreement(rows, rule_action="route_to", rule_target="finance")
        assert result.sample_count == 4
        assert result.agreement_count == 3
        assert result.agreement_score == 0.75

    def test_action_mismatch_counts_as_disagreement_even_with_matching_target(self):
        rows = [{"verdict_action": "skip", "verdict_target": None}]
        result = compute_agreement(rows, rule_action="route_to", rule_target=None)
        assert result.agreement_score == 0.0


# ---------------------------------------------------------------------------
# maybe_create_demotion_suggestion — orchestration against a fake pool
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal stand-in for asyncpg.Pool, mirroring
    ``test_rule_promotion_trigger.py``'s ``_FakePool`` convention: canned
    per-call results consumed in call order, matching the fixed query
    sequence ``maybe_create_demotion_suggestion`` issues (rule fetch, pending
    demotion check, recent spot-checks, optional insert)."""

    def __init__(self, *, fetchrow_results=None, fetch_results=None, fetchval_results=None):
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetch_results = list(fetch_results or [])
        self._fetchval_results = list(fetchval_results or [])

    async def fetchrow(self, *_args, **_kwargs):
        return self._fetchrow_results.pop(0) if self._fetchrow_results else None

    async def fetch(self, *_args, **_kwargs):
        return self._fetch_results.pop(0) if self._fetch_results else []

    async def fetchval(self, *_args, **_kwargs):
        return self._fetchval_results.pop(0) if self._fetchval_results else None


def _promoted_rule_row(*, action: str = "route_to:finance", enabled: bool = True):
    return {"id": "rule-1", "action": action, "created_by": "promotion", "enabled": enabled}


def _spot_check_row(*, minutes_ago: int, action: str = "route_to", target: str | None = "finance"):
    return {
        "decided_at": _ts(minutes_ago=minutes_ago),
        "verdict_action": action,
        "verdict_target": target,
    }


class TestMaybeCreateDemotionSuggestion:
    async def test_no_pool_is_a_neutral_noop(self):
        result = await maybe_create_demotion_suggestion(None, rule_id="rule-1")
        assert result.ran is False
        assert result.demoted_suggestion_created is False
        assert result.reason == "no_pool"

    async def test_rule_not_found_does_not_run(self):
        pool = _FakePool(fetchrow_results=[None])
        result = await maybe_create_demotion_suggestion(pool, rule_id="missing")
        assert result.ran is False
        assert result.reason == "rule_not_found"

    async def test_non_promoted_rule_is_never_evaluated(self):
        """A hand-edited-back-to-dashboard rule must not be demotion-scored."""
        rule = {
            "id": "rule-1",
            "action": "route_to:finance",
            "created_by": "dashboard",
            "enabled": True,
        }
        pool = _FakePool(fetchrow_results=[rule])
        result = await maybe_create_demotion_suggestion(pool, rule_id="rule-1")
        assert result.ran is False
        assert result.reason == "not_a_promoted_rule"

    async def test_disabled_rule_is_not_evaluated(self):
        rule = _promoted_rule_row(enabled=False)
        pool = _FakePool(fetchrow_results=[rule])
        result = await maybe_create_demotion_suggestion(pool, rule_id="rule-1")
        assert result.ran is False
        assert result.reason == "rule_disabled"

    async def test_existing_pending_demotion_short_circuits(self):
        """Scenario mirrors 'Table enforces one pending demotion suggestion
        per rule' -- the app layer must not even attempt a duplicate."""
        rule = _promoted_rule_row()
        pending = {"id": uuid4()}
        pool = _FakePool(fetchrow_results=[rule, pending])
        result = await maybe_create_demotion_suggestion(pool, rule_id="rule-1")
        assert result.ran is True
        assert result.demoted_suggestion_created is False
        assert result.reason == "pending_demotion_exists"

    async def test_below_min_samples_does_not_score(self):
        rule = _promoted_rule_row()
        spot_checks = [_spot_check_row(minutes_ago=1, target="general")]  # 1 disagreeing sample
        pool = _FakePool(fetchrow_results=[rule, None], fetch_results=[spot_checks])
        result = await maybe_create_demotion_suggestion(pool, rule_id="rule-1", min_samples=5)
        assert result.ran is True
        assert result.demoted_suggestion_created is False
        assert result.reason == "insufficient_samples"
        assert result.sample_count == 1

    async def test_agreement_above_threshold_does_not_demote(self):
        rule = _promoted_rule_row()
        spot_checks = [_spot_check_row(minutes_ago=i, target="finance") for i in range(5)]
        pool = _FakePool(fetchrow_results=[rule, None], fetch_results=[spot_checks])
        result = await maybe_create_demotion_suggestion(pool, rule_id="rule-1", min_samples=5)
        assert result.demoted_suggestion_created is False
        assert result.reason == "agreement_above_threshold"
        assert result.agreement_score == 1.0

    async def test_sustained_disagreement_creates_demotion_suggestion(self):
        """Scenario: 'Sustained disagreement creates a demotion suggestion'."""
        rule = _promoted_rule_row()
        # 10 samples, only 8 agree -> 80%, below the 90% default threshold.
        spot_checks = [_spot_check_row(minutes_ago=i, target="finance") for i in range(8)]
        spot_checks += [_spot_check_row(minutes_ago=i, target="general") for i in range(8, 10)]
        new_id = uuid4()
        pool = _FakePool(
            fetchrow_results=[rule, None],
            fetch_results=[spot_checks],
            fetchval_results=[new_id],
        )
        result = await maybe_create_demotion_suggestion(pool, rule_id="rule-1", min_samples=5)
        assert result.demoted_suggestion_created is True
        assert result.reason == "demotion_created"
        assert result.agreement_score == pytest.approx(0.8)
        assert result.sample_count == 10

    async def test_race_conflict_on_insert_is_handled_gracefully(self):
        """A concurrent insert already created the pending demotion between
        this call's pending-check and its insert -- ON CONFLICT DO NOTHING
        must not raise or double-count as created."""
        rule = _promoted_rule_row()
        spot_checks = [_spot_check_row(minutes_ago=i, target="general") for i in range(5)]
        pool = _FakePool(
            fetchrow_results=[rule, None],
            fetch_results=[spot_checks],
            fetchval_results=[None],  # ON CONFLICT DO NOTHING -> no id returned
        )
        result = await maybe_create_demotion_suggestion(pool, rule_id="rule-1", min_samples=5)
        assert result.demoted_suggestion_created is False
        assert result.reason == "race_conflict"

    async def test_never_raises_on_unexpected_db_error(self):
        """Degraded-honesty contract: an unexpected error must never
        propagate and block/fail the routing decision it followed."""

        class _ExplodingPool:
            async def fetchrow(self, *_args, **_kwargs):
                raise RuntimeError("connection refused")

        result = await maybe_create_demotion_suggestion(_ExplodingPool(), rule_id="rule-1")
        assert result.ran is False
        assert result.reason == "error"

    async def test_skip_action_rule_compares_against_skip_not_route_to(self):
        """A promoted skip rule's spot-checks are compared against
        (skip, None), not any route_to shape."""
        rule = _promoted_rule_row(action="skip")
        # All 5 spot-checks show the LLM actually routing somewhere -- total
        # disagreement with the rule's 'skip'.
        spot_checks = [_spot_check_row(minutes_ago=i, target="general") for i in range(5)]
        new_id = uuid4()
        pool = _FakePool(
            fetchrow_results=[rule, None],
            fetch_results=[spot_checks],
            fetchval_results=[new_id],
        )
        result = await maybe_create_demotion_suggestion(pool, rule_id="rule-1", min_samples=5)
        assert result.agreement_score == 0.0
        assert result.demoted_suggestion_created is True


def test_module_defaults_match_design_doc():
    """design.md section 4 / spec defaults: window=20, threshold=90%."""
    assert DEFAULT_AGREEMENT_WINDOW == 20
    assert DEFAULT_AGREEMENT_THRESHOLD == pytest.approx(0.90)
    assert DEFAULT_MIN_SAMPLES >= 1

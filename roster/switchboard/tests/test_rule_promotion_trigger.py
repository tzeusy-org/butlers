"""Unit tests for the rule-promotion trigger (bu-wuwy9, bead 3 of 7).

Covers the pure gate/classifier/parsing helpers directly, plus the
per-candidate orchestration (``_process_candidate`` / ``_bump_existing_
suggestion``) against small fake pools and a fake coverage evaluator — no DB
required. See ``tests/integration/test_switchboard_rule_promotion_trigger_job.py``
for the end-to-end scan-against-real-Postgres coverage, and
``tests/tools/test_scheduled_jobs_rule_promotion_trigger.py`` for job_args
validation + registry wiring.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.tools.switchboard.routing.rule_promotion import (
    DEFAULT_MIN_ELAPSED_FLOOR,
    PromotionTriggerResult,
    build_proposed_action,
    compute_is_clearly_automated,
    distinct_utc_calendar_days,
    parse_proposed_action,
    passes_evidence_quality_gate,
    proposed_rule_for_sender_key,
    run_rule_promotion_trigger,
    verdicts_agree,
)

pytestmark = pytest.mark.unit


def _ts(*, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


class TestProposedRuleIdentity:
    def test_email_sender_key_proposes_email_matcher(self) -> None:
        assert proposed_rule_for_sender_key("billing@example.com") == (
            "sender_address",
            {"address": "billing@example.com"},
        )

    def test_opaque_connector_identity_proposes_source_endpoint_matcher(self) -> None:
        assert proposed_rule_for_sender_key("spotify:tzeusii") == (
            "source_endpoint",
            {"endpoint_identity": "spotify:tzeusii"},
        )


# ---------------------------------------------------------------------------
# verdicts_agree — the "N-consecutive-same-verdict" scan
# ---------------------------------------------------------------------------


class TestVerdictsAgree:
    def test_empty_is_not_agreement(self):
        assert verdicts_agree([]) is None

    def test_single_row_agrees_with_itself(self):
        rows = [{"verdict_action": "route_to", "verdict_target": "finance"}]
        assert verdicts_agree(rows) == ("route_to", "finance")

    def test_all_matching_rows_agree(self):
        rows = [
            {"verdict_action": "route_to", "verdict_target": "finance"},
            {"verdict_action": "route_to", "verdict_target": "finance"},
            {"verdict_action": "route_to", "verdict_target": "finance"},
        ]
        assert verdicts_agree(rows) == ("route_to", "finance")

    def test_differing_target_breaks_agreement(self):
        rows = [
            {"verdict_action": "route_to", "verdict_target": "finance"},
            {"verdict_action": "route_to", "verdict_target": "general"},
            {"verdict_action": "route_to", "verdict_target": "finance"},
        ]
        assert verdicts_agree(rows) is None

    def test_differing_action_breaks_agreement(self):
        rows = [
            {"verdict_action": "skip", "verdict_target": None},
            {"verdict_action": "route_to", "verdict_target": "finance"},
        ]
        assert verdicts_agree(rows) is None


# ---------------------------------------------------------------------------
# passes_evidence_quality_gate — UTC anchoring + elapsed floor
# ---------------------------------------------------------------------------


class TestEvidenceQualityGate:
    def test_three_distinct_calendar_days_passes(self):
        timestamps = [_ts(day=1), _ts(day=2), _ts(day=3)]
        assert passes_evidence_quality_gate(timestamps) is True

    def test_single_burst_same_day_fails(self):
        """Scenario: 'Single-burst evidence does not trigger promotion'."""
        timestamps = [
            _ts(day=5, hour=10, minute=0),
            _ts(day=5, hour=10, minute=4),
            _ts(day=5, hour=10, minute=9),
        ]
        assert passes_evidence_quality_gate(timestamps) is False

    def test_midnight_boundary_burst_fails(self):
        """Scenario: 'Midnight-boundary burst does not trigger promotion'.

        Two distinct UTC calendar dates (day 5 and day 6), but only ~7
        minutes apart — a naive distinct-day count alone would pass this;
        the minimum-elapsed-time floor must reject it.
        """
        timestamps = [
            datetime(2026, 7, 5, 23, 55, tzinfo=UTC),
            datetime(2026, 7, 5, 23, 59, tzinfo=UTC),
            datetime(2026, 7, 6, 0, 2, tzinfo=UTC),
        ]
        assert distinct_utc_calendar_days(timestamps) == 2  # passes the naive check...
        assert passes_evidence_quality_gate(timestamps) is False  # ...but the gate rejects it

    def test_two_distinct_days_with_sufficient_elapsed_time_passes(self):
        timestamps = [_ts(day=1, hour=23), _ts(day=2, hour=23, minute=1)]
        assert passes_evidence_quality_gate(timestamps) is True

    def test_two_distinct_days_but_insufficient_elapsed_floor_fails(self):
        timestamps = [
            datetime(2026, 7, 1, 23, 0, tzinfo=UTC),
            datetime(2026, 7, 2, 1, 0, tzinfo=UTC),
        ]
        # 2 distinct days, but only 2 hours apart — below the default 20h floor.
        assert passes_evidence_quality_gate(timestamps) is False

    def test_single_timestamp_never_passes(self):
        assert passes_evidence_quality_gate([_ts(day=1)]) is False

    def test_custom_thresholds_respected(self):
        timestamps = [_ts(day=1, hour=0), _ts(day=1, hour=5)]
        assert (
            passes_evidence_quality_gate(
                timestamps, min_distinct_days=1, min_elapsed=timedelta(hours=4)
            )
            is True
        )

    # --- exact-boundary pins (bu-xcrz6): guard the ``>=`` in both checks -----
    # These isolate each threshold at its literal edge so a future off-by-one
    # (``>=`` -> ``>``) flips exactly one of them red instead of slipping by.

    def test_elapsed_exactly_at_floor_passes(self):
        """Elapsed == 20h (the default floor) must pass: the check is ``>=``.

        Held at 2 distinct UTC days so the elapsed floor is the deciding
        factor, not the distinct-day count.
        """
        timestamps = [
            datetime(2026, 7, 1, 4, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 2, 0, 0, 0, tzinfo=UTC),
        ]
        assert max(timestamps) - min(timestamps) == timedelta(hours=20)
        assert distinct_utc_calendar_days(timestamps) == 2
        assert passes_evidence_quality_gate(timestamps) is True

    def test_elapsed_one_second_under_floor_fails(self):
        """Elapsed == 20h - 1s must fail: pins the low side of the floor."""
        timestamps = [
            datetime(2026, 7, 1, 4, 0, 1, tzinfo=UTC),
            datetime(2026, 7, 2, 0, 0, 0, tzinfo=UTC),
        ]
        assert max(timestamps) - min(timestamps) == timedelta(hours=20) - timedelta(seconds=1)
        assert distinct_utc_calendar_days(timestamps) == 2  # distinct-day check satisfied...
        assert passes_evidence_quality_gate(timestamps) is False  # ...only the floor rejects it

    def test_exactly_two_distinct_days_passes(self):
        """distinct days == 2 (the default minimum) must pass: check is ``>=``.

        Elapsed is held well above the floor so the distinct-day count is the
        deciding factor.
        """
        timestamps = [
            datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 2, 23, 0, tzinfo=UTC),
        ]
        assert distinct_utc_calendar_days(timestamps) == 2
        assert max(timestamps) - min(timestamps) >= DEFAULT_MIN_ELAPSED_FLOOR
        assert passes_evidence_quality_gate(timestamps) is True

    def test_one_distinct_day_fails_even_with_sufficient_elapsed(self):
        """distinct days == 1 must fail even when the elapsed floor is met.

        20h elapsed within a single UTC calendar day clears the floor, so this
        pins the distinct-day check as an independent, mandatory gate.
        """
        timestamps = [
            datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 1, 20, 0, tzinfo=UTC),
        ]
        assert distinct_utc_calendar_days(timestamps) == 1
        assert max(timestamps) - min(timestamps) >= DEFAULT_MIN_ELAPSED_FLOOR  # floor satisfied...
        assert passes_evidence_quality_gate(timestamps) is False  # ...only distinct-day rejects it


# ---------------------------------------------------------------------------
# build_proposed_action / parse_proposed_action round trip
# ---------------------------------------------------------------------------


class TestProposedActionCodec:
    def test_route_to_encodes_with_target(self):
        assert build_proposed_action("route_to", "finance") == "route_to:finance"

    def test_route_to_without_target_is_none(self):
        assert build_proposed_action("route_to", None) is None

    def test_skip_and_metadata_only_pass_through(self):
        assert build_proposed_action("skip", None) == "skip"
        assert build_proposed_action("metadata_only", None) == "metadata_only"

    def test_pass_through_and_block_have_no_rule_equivalent(self):
        assert build_proposed_action("pass_through", None) is None
        assert build_proposed_action("block", None) is None

    def test_round_trip_route_to(self):
        encoded = build_proposed_action("route_to", "finance")
        assert parse_proposed_action(encoded) == ("route_to", "finance")

    def test_round_trip_skip(self):
        encoded = build_proposed_action("skip", None)
        assert parse_proposed_action(encoded) == ("skip", None)


# ---------------------------------------------------------------------------
# compute_is_clearly_automated — reused bulk-mail vocabulary from migration 003
# ---------------------------------------------------------------------------


class TestIsClearlyAutomated:
    def test_noreply_prefix_flags_regardless_of_headers(self):
        assert compute_is_clearly_automated("noreply@example.com", []) is True

    def test_no_reply_hyphenated_prefix_flags(self):
        assert compute_is_clearly_automated("no-reply@example.com", []) is True

    def test_notifications_and_alerts_prefixes_flag(self):
        assert compute_is_clearly_automated("notifications@github.com", []) is True
        assert compute_is_clearly_automated("alerts@chase.com", []) is True

    def test_list_unsubscribe_header_on_all_evidence_flags(self):
        """Scenario: 'Automated sender flagged'."""
        headers = [{"List-Unsubscribe": "<mailto:x@y.com>"}, {"List-Unsubscribe": "<...>"}]
        assert compute_is_clearly_automated("billing@vendor.com", headers) is True

    def test_precedence_bulk_flags(self):
        assert compute_is_clearly_automated("billing@vendor.com", [{"Precedence": "bulk"}]) is True

    def test_auto_submitted_auto_generated_flags(self):
        headers = [{"Auto-Submitted": "auto-generated"}]
        assert compute_is_clearly_automated("billing@vendor.com", headers) is True

    def test_no_signals_and_non_automated_local_part_not_flagged(self):
        """Scenario: 'Non-automated sender not flagged'."""
        headers = [{"Subject": "hi"}, {"Subject": "hello"}]
        assert compute_is_clearly_automated("jane@example.com", headers) is False

    def test_empty_evidence_headers_not_flagged_for_human_looking_address(self):
        assert compute_is_clearly_automated("jane@example.com", []) is False

    def test_mixed_evidence_requires_all_to_show_a_signal(self):
        """One automated-looking outlier must not flip an otherwise-human
        sender's history — see module docstring's 'all not any' rationale."""
        headers = [{"List-Unsubscribe": "<...>"}, {"Subject": "hi"}]
        assert compute_is_clearly_automated("jane@example.com", headers) is False


# ---------------------------------------------------------------------------
# Orchestration: run_rule_promotion_trigger against a fake pool + evaluator
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal stand-in for asyncpg.Pool driven by canned per-call results.

    ``fetch_results`` / ``fetchrow_results`` / ``fetchval_results`` are
    consumed in call order — this mirrors the fixed sequence of queries
    ``_process_candidate`` issues for a given code path (see
    rule_promotion.py: pending-check, evidence fetch, headers fetch, insert).
    """

    def __init__(
        self,
        *,
        fetch_results=None,
        fetchrow_results=None,
        fetchval_results=None,
        execute_result: str = "UPDATE 1",
    ):
        self._fetch_results = list(fetch_results or [])
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetchval_results = list(fetchval_results or [])
        self.execute = AsyncMock(return_value=execute_result)

    async def fetch(self, *_args, **_kwargs):
        return self._fetch_results.pop(0) if self._fetch_results else []

    async def fetchrow(self, *_args, **_kwargs):
        return self._fetchrow_results.pop(0) if self._fetchrow_results else None

    async def fetchval(self, *_args, **_kwargs):
        return self._fetchval_results.pop(0) if self._fetchval_results else None


class _FakeEvaluator:
    def __init__(self, *, action: str = "pass_through"):
        self._decision = SimpleNamespace(action=action)
        self.ensure_loaded = AsyncMock(return_value=None)

    def evaluate(self, _envelope):
        return self._decision


def _verdict_row(*, day: int, action: str = "route_to", target: str | None = "finance"):
    return {
        "decided_at": _ts(day=day),
        "verdict_action": action,
        "verdict_target": target,
        "ingestion_event_id": None,
    }


class TestRunRulePromotionTrigger:
    async def test_creates_suggestion_for_eligible_candidate(self):
        candidates = [{"sender_key": "billing@chase.com", "source_channel": "email"}]
        evidence = [_verdict_row(day=3), _verdict_row(day=2), _verdict_row(day=1)]
        new_id = uuid4()
        pool = _FakePool(
            fetch_results=[candidates, evidence, []],  # candidates, evidence, headers
            fetchrow_results=[None],  # no pending suggestion
            fetchval_results=[new_id],  # insert succeeds
        )
        evaluator = _FakeEvaluator(action="pass_through")

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["candidates_scanned"] == 1
        assert result["suggestions_created"] == 1
        assert result["errors"] == 0

    async def test_skips_candidate_below_threshold(self):
        candidates = [{"sender_key": "new@sender.com", "source_channel": "email"}]
        evidence = [_verdict_row(day=1), _verdict_row(day=2)]  # only 2, threshold is 3
        pool = _FakePool(fetch_results=[candidates, evidence], fetchrow_results=[None])
        evaluator = _FakeEvaluator()

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["skipped_insufficient_evidence"] == 1
        assert result["suggestions_created"] == 0

    async def test_skips_candidate_already_covered_by_existing_rule(self):
        """Scenario: 'Existing rule suppresses re-proposal'."""
        candidates = [{"sender_key": "vip@chase.com", "source_channel": "email"}]
        evidence = [_verdict_row(day=1), _verdict_row(day=2), _verdict_row(day=3)]
        pool = _FakePool(
            fetch_results=[candidates, evidence, []],
            fetchrow_results=[None],
        )
        evaluator = _FakeEvaluator(action="route_to")  # a rule already matches

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["skipped_existing_rule"] == 1
        assert result["suggestions_created"] == 0

    async def test_skips_candidate_under_active_dismissal_cooldown(self):
        """A prior owner Dismiss (status='dismissed' + future cooldown_until)
        must suppress a fresh proposal for the same sender/channel — the bug
        that made Dismiss cosmetic and the card reappear every scan."""
        candidates = [{"sender_key": "declined@sender.com", "source_channel": "google_drive"}]
        active_cooldown = {"id": uuid4(), "cooldown_until": _ts(day=28)}
        pool = _FakePool(
            fetch_results=[candidates],  # returns before any evidence fetch
            # 1st fetchrow: no pending suggestion; 2nd: an active dismissal cooldown.
            fetchrow_results=[None, active_cooldown],
        )
        evaluator = _FakeEvaluator()

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["skipped_dismissal_cooldown"] == 1
        assert result["suggestions_created"] == 0

    async def test_creates_again_once_dismissal_cooldown_expired(self):
        """An expired/absent dismissal cooldown (query returns no row) does not
        suppress a re-proposal — the cooldown is a window, not a permanent mute."""
        candidates = [{"sender_key": "lapsed@sender.com", "source_channel": "email"}]
        evidence = [_verdict_row(day=3), _verdict_row(day=2), _verdict_row(day=1)]
        new_id = uuid4()
        pool = _FakePool(
            fetch_results=[candidates, evidence, []],
            fetchrow_results=[None, None],  # no pending, no active cooldown
            fetchval_results=[new_id],
        )
        evaluator = _FakeEvaluator(action="pass_through")

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["skipped_dismissal_cooldown"] == 0
        assert result["suggestions_created"] == 1

    async def test_skips_when_most_recent_verdicts_disagree(self):
        candidates = [{"sender_key": "flip@sender.com", "source_channel": "email"}]
        evidence = [
            _verdict_row(day=3, target="general"),
            _verdict_row(day=2, target="finance"),
            _verdict_row(day=1, target="finance"),
        ]
        pool = _FakePool(fetch_results=[candidates, evidence], fetchrow_results=[None])
        evaluator = _FakeEvaluator()

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["skipped_no_agreement"] == 1

    async def test_conflict_on_insert_is_handled_gracefully(self):
        """A race against a concurrent insert (ON CONFLICT DO NOTHING ->
        RETURNING nothing) must not raise or double-count as created."""
        candidates = [{"sender_key": "race@sender.com", "source_channel": "email"}]
        evidence = [_verdict_row(day=1), _verdict_row(day=2), _verdict_row(day=3)]
        pool = _FakePool(
            fetch_results=[candidates, evidence, []],
            fetchrow_results=[None],
            fetchval_results=[None],  # ON CONFLICT DO NOTHING -> no id returned
        )
        evaluator = _FakeEvaluator(action="pass_through")

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["skipped_race_conflict"] == 1
        assert result["suggestions_created"] == 0

    async def test_bumps_existing_pending_suggestion_with_new_evidence(self):
        """Scenario: 'Repeated evidence bumps an existing pending suggestion'."""
        candidates = [{"sender_key": "existing@sender.com", "source_channel": "email"}]
        pending = {
            "id": uuid4(),
            "evidence_count": 3,
            "last_evidence_at": _ts(day=1),
            "proposed_action": "route_to:finance",
        }
        new_agreeing = [{"decided_at": _ts(day=5)}]
        pool = _FakePool(
            # Candidate scan, latest evidence for the coverage recheck, then
            # the post-suggestion evidence to bump. The synthetic evidence has
            # no event id, so the header helper makes no DB fetch.
            fetch_results=[candidates, [_verdict_row(day=3)], new_agreeing],
            fetchrow_results=[pending],
        )
        evaluator = _FakeEvaluator()

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["suggestions_bumped"] == 1
        pool.execute.assert_awaited_once()

    async def test_supersedes_pending_suggestion_when_an_enabled_rule_now_covers_it(self):
        """A manually added rule retires, rather than keeps bumping, its pending proposal."""
        candidates = [{"sender_key": "covered@sender.com", "source_channel": "email"}]
        pending = {
            "id": uuid4(),
            "evidence_count": 3,
            "last_evidence_at": _ts(day=1),
            "proposed_action": "route_to:finance",
        }
        pool = _FakePool(
            # Candidate scan, then the latest evidence used for policy coverage.
            fetch_results=[candidates, [_verdict_row(day=3)]],
            fetchrow_results=[pending],
        )
        evaluator = _FakeEvaluator(action="skip")

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["suggestions_superseded"] == 1
        assert result["suggestions_bumped"] == 0
        update_sql, decided_by, suggestion_id = pool.execute.await_args.args
        assert "status = 'superseded'" in update_sql
        assert "AND status = 'pending_review'" in update_sql
        assert decided_by == "system:rule_promotion_trigger"
        assert suggestion_id == pending["id"]

    async def test_supersede_race_does_not_overwrite_another_terminal_decision(self):
        """The conditional update must report, not overwrite, a concurrent decision."""
        candidates = [{"sender_key": "raced@sender.com", "source_channel": "email"}]
        pending = {
            "id": uuid4(),
            "evidence_count": 3,
            "last_evidence_at": _ts(day=1),
            "proposed_action": "route_to:finance",
        }
        pool = _FakePool(
            fetch_results=[candidates, [_verdict_row(day=3)]],
            fetchrow_results=[pending],
            execute_result="UPDATE 0",
        )
        evaluator = _FakeEvaluator(action="skip")

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["suggestions_superseded"] == 0
        assert result["skipped_race_conflict"] == 1
        update_sql = pool.execute.await_args.args[0]
        assert "AND status = 'pending_review'" in update_sql

    async def test_no_new_evidence_leaves_pending_suggestion_untouched(self):
        candidates = [{"sender_key": "stale@sender.com", "source_channel": "email"}]
        pending = {
            "id": uuid4(),
            "evidence_count": 3,
            "last_evidence_at": _ts(day=1),
            "proposed_action": "skip",
        }
        pool = _FakePool(
            fetch_results=[candidates, [_verdict_row(day=3)], []],
            fetchrow_results=[pending],
        )
        evaluator = _FakeEvaluator()

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["skipped_no_new_evidence"] == 1
        pool.execute.assert_not_awaited()

    async def test_candidate_processing_error_is_isolated(self):
        """One candidate raising must not abort the whole scan."""
        candidates = [
            {"sender_key": "bad@sender.com", "source_channel": "email"},
        ]

        class _BoomPool(_FakePool):
            async def fetchrow(self, *_args, **_kwargs):
                raise RuntimeError("boom")

        pool = _BoomPool(fetch_results=[candidates])
        evaluator = _FakeEvaluator()

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["errors"] == 1
        assert result["candidates_scanned"] == 1

    async def test_skips_candidates_missing_sender_key_or_channel(self):
        candidates = [{"sender_key": "", "source_channel": "email"}]
        pool = _FakePool(fetch_results=[candidates])
        evaluator = _FakeEvaluator()

        result = await run_rule_promotion_trigger(pool, evaluator=evaluator)

        assert result["candidates_scanned"] == 1
        assert sum(result.values()) == 1  # only candidates_scanned incremented


def test_promotion_trigger_result_as_dict_has_all_fields():
    result = PromotionTriggerResult()
    d = result.as_dict()
    assert set(d) == {
        "candidates_scanned",
        "suggestions_created",
        "suggestions_bumped",
        "suggestions_superseded",
        "skipped_existing_rule",
        "skipped_dismissal_cooldown",
        "skipped_insufficient_evidence",
        "skipped_no_agreement",
        "skipped_no_rule_equivalent",
        "skipped_no_new_evidence",
        "skipped_race_conflict",
        "errors",
    }
    assert all(v == 0 for v in d.values())

"""Unit tests for tests.e2e.scoring — scoring engine.

Tests are grouped by:
1. load_pricing() — TOML loading with fallback
2. BenchmarkCostTracker — per-(model, scenario_id) keyed cost tracking
3. compute_routing_scorecard() — accuracy, tag breakdown, confusion matrix
4. compute_tool_call_scorecard() — accuracy, butler breakdown, fail details
5. compute_all_scorecards() — integration, sorting by routing accuracy

These tests are pure Python and do NOT require a running database or LLM.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.benchmark import BenchmarkEntry, BenchmarkResult
from tests.e2e.scoring import (
    BenchmarkCostTracker,
    RoutingScorecard,
    ToolCallScorecard,
    compute_all_scorecards,
    compute_routing_scorecard,
    compute_tool_call_scorecard,
    load_pricing,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    model: str,
    scenario_id: str,
    *,
    routing_passed: bool = True,
    routing_expected: str | None = "health",
    routing_actual: str | None = "health",
    tool_calls_passed: bool = True,
    tool_calls_expected: list[str] | None = None,
    tool_calls_actual: list[str] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    duration_ms: int = 1000,
    timed_out: bool = False,
    error: str | None = None,
) -> BenchmarkEntry:
    return BenchmarkEntry(
        model=model,
        scenario_id=scenario_id,
        routing_passed=routing_passed,
        routing_expected=routing_expected,
        routing_actual=routing_actual,
        tool_calls_passed=tool_calls_passed,
        tool_calls_expected=tool_calls_expected or [],
        tool_calls_actual=tool_calls_actual or [],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
        timed_out=timed_out,
        error=error,
    )


def _make_results(*entries: BenchmarkEntry) -> BenchmarkResult:
    results = BenchmarkResult()
    for entry in entries:
        results.record(entry)
    return results


# ---------------------------------------------------------------------------
# load_pricing tests
# ---------------------------------------------------------------------------


class TestLoadPricing:
    def test_loads_valid_toml(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "pricing.toml"
        toml_file.write_text(
            '[models."test-model"]\n'
            "input_price_per_token = 0.000003\n"
            "output_price_per_token = 0.000015\n"
        )
        pricing = load_pricing(toml_file)
        assert "test-model" in pricing
        assert pricing["test-model"]["input_price_per_token"] == 0.000003
        assert pricing["test-model"]["output_price_per_token"] == 0.000015

    def test_returns_empty_dict_if_file_not_found(self, tmp_path: Path) -> None:
        pricing = load_pricing(tmp_path / "nonexistent.toml")
        assert pricing == {}

    def test_returns_empty_dict_on_invalid_toml(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text("this is not valid [ toml\n")
        pricing = load_pricing(toml_file)
        assert pricing == {}

    def test_loads_multiple_models(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "pricing.toml"
        toml_file.write_text(
            "[models]\n"
            '[models."model-a"]\n'
            "input_price_per_token = 0.001\n"
            "output_price_per_token = 0.002\n"
            '[models."model-b"]\n'
            "input_price_per_token = 0.003\n"
            "output_price_per_token = 0.006\n"
        )
        pricing = load_pricing(toml_file)
        assert len(pricing) == 2
        assert pricing["model-a"]["input_price_per_token"] == 0.001
        assert pricing["model-b"]["output_price_per_token"] == 0.006


# ---------------------------------------------------------------------------
# BenchmarkCostTracker tests
# ---------------------------------------------------------------------------


class TestBenchmarkCostTracker:
    def test_record_and_retrieve_single_entry(self) -> None:
        pricing = {"my-model": {"input_price_per_token": 0.001, "output_price_per_token": 0.002}}
        tracker = BenchmarkCostTracker(pricing=pricing)
        tracker.record("my-model", "scenario-1", 1000, 500)

        entries = tracker.for_model("my-model")
        assert len(entries) == 1
        e = entries[0]
        assert e.model == "my-model"
        assert e.scenario_id == "scenario-1"
        assert e.input_tokens == 1000
        assert e.output_tokens == 500
        assert e.input_cost_usd == pytest.approx(1.0)  # 1000 * 0.001
        assert e.output_cost_usd == pytest.approx(1.0)  # 500 * 0.002

    def test_record_replaces_existing_entry(self) -> None:
        tracker = BenchmarkCostTracker()
        tracker.record("model-a", "s1", 100, 50)
        tracker.record("model-a", "s1", 200, 100)  # Replace

        entries = tracker.for_model("model-a")
        assert len(entries) == 1
        assert entries[0].input_tokens == 200

    def test_zero_cost_when_model_not_in_pricing(self) -> None:
        tracker = BenchmarkCostTracker(pricing={})
        tracker.record("unknown-model", "s1", 5000, 3000)

        entries = tracker.for_model("unknown-model")
        assert len(entries) == 1
        assert entries[0].input_cost_usd == 0.0
        assert entries[0].output_cost_usd == 0.0

    def test_total_for_model(self) -> None:
        pricing = {"m": {"input_price_per_token": 0.001, "output_price_per_token": 0.002}}
        tracker = BenchmarkCostTracker(pricing=pricing)
        tracker.record("m", "s1", 1000, 500)
        tracker.record("m", "s2", 2000, 1000)

        totals = tracker.total_for_model("m")
        assert totals["input_tokens"] == 3000
        assert totals["output_tokens"] == 1500
        assert totals["total_tokens"] == 4500
        assert totals["input_cost_usd"] == pytest.approx(3.0)
        assert totals["output_cost_usd"] == pytest.approx(3.0)
        assert totals["total_cost_usd"] == pytest.approx(6.0)

    def test_total_for_model_empty(self) -> None:
        tracker = BenchmarkCostTracker()
        totals = tracker.total_for_model("nonexistent")
        assert totals["input_tokens"] == 0
        assert totals["total_cost_usd"] == 0.0

    def test_for_model_sorted_by_scenario_id(self) -> None:
        tracker = BenchmarkCostTracker()
        tracker.record("m", "c-scenario", 1, 1)
        tracker.record("m", "a-scenario", 1, 1)
        tracker.record("m", "b-scenario", 1, 1)

        entries = tracker.for_model("m")
        assert [e.scenario_id for e in entries] == ["a-scenario", "b-scenario", "c-scenario"]

    def test_populate_from_results(self) -> None:
        results = _make_results(
            _make_entry("model-x", "s1", input_tokens=500, output_tokens=200),
            _make_entry("model-x", "s2", input_tokens=300, output_tokens=100),
        )
        tracker = BenchmarkCostTracker()
        tracker.populate_from_results(results)

        entries = tracker.for_model("model-x")
        assert len(entries) == 2
        total = tracker.total_for_model("model-x")
        assert total["input_tokens"] == 800
        assert total["output_tokens"] == 300

    def test_cost_entry_total_tokens(self) -> None:
        tracker = BenchmarkCostTracker()
        tracker.record("m", "s1", 1000, 500)
        e = tracker.for_model("m")[0]
        assert e.total_tokens == 1500

    def test_cost_entry_total_cost(self) -> None:
        pricing = {"m": {"input_price_per_token": 0.001, "output_price_per_token": 0.002}}
        tracker = BenchmarkCostTracker(pricing=pricing)
        tracker.record("m", "s1", 1000, 500)
        e = tracker.for_model("m")[0]
        assert e.total_cost_usd == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# compute_routing_scorecard tests
# ---------------------------------------------------------------------------


class TestComputeRoutingScorecard:
    def test_perfect_accuracy(self) -> None:
        results = _make_results(
            _make_entry("m", "s1", routing_passed=True, routing_expected="health"),
            _make_entry("m", "s2", routing_passed=True, routing_expected="calendar"),
        )
        sc = compute_routing_scorecard(results, "m")
        assert sc.model == "m"
        assert sc.total_scenarios == 2
        assert sc.passed == 2
        assert sc.accuracy_pct == pytest.approx(100.0)

    def test_partial_accuracy(self) -> None:
        results = _make_results(
            _make_entry("m", "s1", routing_passed=True, routing_expected="health"),
            _make_entry(
                "m",
                "s2",
                routing_passed=False,
                routing_expected="health",
                routing_actual="general",
            ),
        )
        sc = compute_routing_scorecard(results, "m")
        assert sc.total_scenarios == 2
        assert sc.passed == 1
        assert sc.accuracy_pct == pytest.approx(50.0)

    def test_zero_accuracy(self) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                routing_passed=False,
                routing_expected="health",
                routing_actual="general",
            ),
        )
        sc = compute_routing_scorecard(results, "m")
        assert sc.accuracy_pct == pytest.approx(0.0)

    def test_no_routing_scenarios(self) -> None:
        results = _make_results(
            _make_entry("m", "s1", routing_expected=None),
        )
        sc = compute_routing_scorecard(results, "m")
        assert sc.total_scenarios == 0
        assert sc.accuracy_pct == 0.0

    def test_confusion_matrix_populated_for_misroutes(self) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                routing_passed=False,
                routing_expected="health",
                routing_actual="general",
            ),
            _make_entry(
                "m",
                "s2",
                routing_passed=False,
                routing_expected="health",
                routing_actual="general",
            ),
            _make_entry(
                "m",
                "s3",
                routing_passed=False,
                routing_expected="calendar",
                routing_actual="health",
            ),
        )
        sc = compute_routing_scorecard(results, "m")
        assert sc.confusion_matrix[("health", "general")] == 2
        assert sc.confusion_matrix[("calendar", "health")] == 1

    def test_confusion_matrix_empty_when_all_pass(self) -> None:
        results = _make_results(
            _make_entry("m", "s1", routing_passed=True, routing_expected="health"),
        )
        sc = compute_routing_scorecard(results, "m")
        assert sc.confusion_matrix == {}

    def test_tag_breakdown_computed(self) -> None:
        results = _make_results(
            _make_entry("m", "s1", routing_passed=True, routing_expected="health"),
            _make_entry(
                "m", "s2", routing_passed=False, routing_expected="health", routing_actual="general"
            ),
            _make_entry("m", "s3", routing_passed=True, routing_expected="calendar"),
        )
        tags = {
            "s1": ["telegram", "health"],
            "s2": ["telegram", "health"],
            "s3": ["email", "calendar"],
        }
        sc = compute_routing_scorecard(results, "m", scenario_tags=tags)

        tag_map = {b.tag: b for b in sc.tag_breakdown}
        assert "telegram" in tag_map
        assert tag_map["telegram"].total == 2
        assert tag_map["telegram"].passed == 1
        assert tag_map["telegram"].accuracy_pct == pytest.approx(50.0)
        assert "email" in tag_map
        assert tag_map["email"].total == 1
        assert tag_map["email"].passed == 1
        assert tag_map["email"].accuracy_pct == pytest.approx(100.0)

    def test_tag_breakdown_empty_without_scenario_tags(self) -> None:
        results = _make_results(
            _make_entry("m", "s1", routing_passed=True),
        )
        sc = compute_routing_scorecard(results, "m")
        assert sc.tag_breakdown == []

    def test_spec_accuracy_90_percent(self) -> None:
        """Spec scenario: model A correctly routes 18 of 20 → 90.0%."""
        entries = [_make_entry("m", f"s{i}", routing_passed=(i < 18)) for i in range(20)]
        results = _make_results(*entries)
        sc = compute_routing_scorecard(results, "m")
        assert sc.total_scenarios == 20
        assert sc.passed == 18
        assert sc.accuracy_pct == pytest.approx(90.0)

    def test_spec_confusion_matrix_health_to_general(self) -> None:
        """Spec scenario: 3 scenarios expected health but routed to general."""
        entries = [
            _make_entry(
                "m",
                f"s{i}",
                routing_passed=False,
                routing_expected="health",
                routing_actual="general",
            )
            for i in range(3)
        ]
        results = _make_results(*entries)
        sc = compute_routing_scorecard(results, "m")
        assert sc.confusion_matrix[("health", "general")] == 3

    def test_spec_category_breakdown(self) -> None:
        """Spec scenario: 10 email (9 correct) + 10 telegram (8 correct) → overall 85%."""
        entries = []
        for i in range(10):
            entries.append(
                _make_entry("m", f"email-{i}", routing_passed=(i < 9), routing_expected="calendar")
            )
        for i in range(10):
            entries.append(
                _make_entry("m", f"tg-{i}", routing_passed=(i < 8), routing_expected="health")
            )
        results = _make_results(*entries)

        scenario_tags = {}
        for i in range(10):
            scenario_tags[f"email-{i}"] = ["email"]
        for i in range(10):
            scenario_tags[f"tg-{i}"] = ["telegram"]

        sc = compute_routing_scorecard(results, "m", scenario_tags=scenario_tags)
        tag_map = {b.tag: b for b in sc.tag_breakdown}
        assert tag_map["email"].accuracy_pct == pytest.approx(90.0)
        assert tag_map["telegram"].accuracy_pct == pytest.approx(80.0)
        assert sc.accuracy_pct == pytest.approx(85.0)

    def test_misroute_with_none_actual_uses_none_label(self) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                routing_passed=False,
                routing_expected="health",
                routing_actual=None,
            ),
        )
        sc = compute_routing_scorecard(results, "m")
        assert ("health", "none") in sc.confusion_matrix


# ---------------------------------------------------------------------------
# compute_tool_call_scorecard tests
# ---------------------------------------------------------------------------


class TestComputeToolCallScorecard:
    def test_perfect_accuracy(self) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                tool_calls_passed=True,
                tool_calls_expected=["log_meal"],
                tool_calls_actual=["state_get", "log_meal", "state_set"],
            ),
        )
        sc = compute_tool_call_scorecard(results, "m")
        assert sc.total_scenarios == 1
        assert sc.passed == 1
        assert sc.accuracy_pct == pytest.approx(100.0)

    def test_partial_accuracy(self) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                tool_calls_passed=True,
                tool_calls_expected=["calendar_create"],
                tool_calls_actual=["calendar_create"],
            ),
            _make_entry(
                "m",
                "s2",
                tool_calls_passed=False,
                tool_calls_expected=["notify"],
                tool_calls_actual=[],
            ),
        )
        sc = compute_tool_call_scorecard(results, "m")
        assert sc.total_scenarios == 2
        assert sc.passed == 1
        assert sc.accuracy_pct == pytest.approx(50.0)

    def test_scenarios_without_expected_tools_excluded(self) -> None:
        results = _make_results(
            _make_entry("m", "s1", tool_calls_expected=None, tool_calls_actual=[]),
            _make_entry(
                "m",
                "s2",
                tool_calls_passed=True,
                tool_calls_expected=["notify"],
                tool_calls_actual=["notify"],
            ),
        )
        sc = compute_tool_call_scorecard(results, "m")
        assert sc.total_scenarios == 1  # Only s2 counts

    def test_fail_details_populated(self) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                tool_calls_passed=False,
                tool_calls_expected=["calendar_create", "notify"],
                tool_calls_actual=["calendar_create"],
            ),
        )
        sc = compute_tool_call_scorecard(results, "m")
        assert len(sc.fail_details) == 1
        detail = sc.fail_details[0]
        assert detail.scenario_id == "s1"
        assert detail.missing == ["notify"]
        assert "calendar_create" in detail.actual

    def test_fail_details_empty_when_all_pass(self) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                tool_calls_passed=True,
                tool_calls_expected=["notify"],
                tool_calls_actual=["notify"],
            ),
        )
        sc = compute_tool_call_scorecard(results, "m")
        assert sc.fail_details == []

    def test_butler_breakdown_by_routing_expected(self) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                tool_calls_passed=True,
                tool_calls_expected=["log_meal"],
                routing_expected="health",
            ),
            _make_entry(
                "m",
                "s2",
                tool_calls_passed=False,
                tool_calls_expected=["log_exercise"],
                routing_expected="health",
            ),
            _make_entry(
                "m",
                "s3",
                tool_calls_passed=True,
                tool_calls_expected=["calendar_create"],
                routing_expected="calendar",
            ),
        )
        sc = compute_tool_call_scorecard(results, "m")
        butler_map = {b.butler: b for b in sc.butler_breakdown}
        assert "health" in butler_map
        assert butler_map["health"].total == 2
        assert butler_map["health"].passed == 1
        assert butler_map["health"].accuracy_pct == pytest.approx(50.0)
        assert "calendar" in butler_map
        assert butler_map["calendar"].total == 1
        assert butler_map["calendar"].passed == 1

    def test_butler_breakdown_uses_scenario_routing_override(self) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                tool_calls_passed=True,
                tool_calls_expected=["notify"],
                routing_expected=None,
            ),  # No routing_expected in entry
        )
        sc = compute_tool_call_scorecard(
            results,
            "m",
            scenario_routing={"s1": "general"},
        )
        butler_map = {b.butler: b for b in sc.butler_breakdown}
        assert "general" in butler_map

    def test_spec_accuracy_75_percent(self) -> None:
        """Spec scenario: 15 of 20 → 75.0%."""
        entries = [
            _make_entry(
                "m",
                f"s{i}",
                tool_calls_passed=(i < 15),
                tool_calls_expected=["some_tool"],
            )
            for i in range(20)
        ]
        results = _make_results(*entries)
        sc = compute_tool_call_scorecard(results, "m")
        assert sc.total_scenarios == 20
        assert sc.passed == 15
        assert sc.accuracy_pct == pytest.approx(75.0)

    def test_spec_butler_breakdown_health_and_relationship(self) -> None:
        """Spec scenario: 8 health (7 correct) = 87.5%, 6 relationship (5 correct) = 83.3%."""
        entries = []
        for i in range(8):
            entries.append(
                _make_entry(
                    "m",
                    f"h{i}",
                    tool_calls_passed=(i < 7),
                    tool_calls_expected=["log_meal"],
                    routing_expected="health",
                )
            )
        for i in range(6):
            entries.append(
                _make_entry(
                    "m",
                    f"r{i}",
                    tool_calls_passed=(i < 5),
                    tool_calls_expected=["notify"],
                    routing_expected="relationship",
                )
            )
        results = _make_results(*entries)
        sc = compute_tool_call_scorecard(results, "m")
        butler_map = {b.butler: b for b in sc.butler_breakdown}
        assert butler_map["health"].accuracy_pct == pytest.approx(87.5)
        assert butler_map["relationship"].accuracy_pct == pytest.approx(83.3, rel=0.01)


# ---------------------------------------------------------------------------
# compute_all_scorecards tests
# ---------------------------------------------------------------------------


class TestComputeAllScorecards:
    def test_returns_one_scorecard_per_model(self) -> None:
        results = _make_results(
            _make_entry("model-a", "s1"),
            _make_entry("model-b", "s1"),
        )
        scorecards = compute_all_scorecards(results)
        assert len(scorecards) == 2
        models = {sc.model for sc in scorecards}
        assert models == {"model-a", "model-b"}

    def test_sorted_by_routing_accuracy_descending(self) -> None:
        results = _make_results(
            # model-a: 1/2 = 50%
            _make_entry("model-a", "s1", routing_passed=True, routing_expected="health"),
            _make_entry(
                "model-a",
                "s2",
                routing_passed=False,
                routing_expected="health",
                routing_actual="general",
            ),
            # model-b: 2/2 = 100%
            _make_entry("model-b", "s1", routing_passed=True, routing_expected="health"),
            _make_entry("model-b", "s2", routing_passed=True, routing_expected="calendar"),
        )
        scorecards = compute_all_scorecards(results)
        assert scorecards[0].model == "model-b"  # 100% first
        assert scorecards[1].model == "model-a"  # 50% second

    def test_includes_routing_and_tool_call_scorecards(self) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                routing_passed=True,
                routing_expected="health",
                tool_calls_passed=True,
                tool_calls_expected=["notify"],
            ),
        )
        scorecards = compute_all_scorecards(results)
        sc = scorecards[0]
        assert isinstance(sc.routing, RoutingScorecard)
        assert isinstance(sc.tool_calls, ToolCallScorecard)

    def test_cost_populated_from_results(self) -> None:
        pricing = {"m": {"input_price_per_token": 0.001, "output_price_per_token": 0.002}}
        results = _make_results(
            _make_entry("m", "s1", input_tokens=1000, output_tokens=500),
        )
        scorecards = compute_all_scorecards(results, pricing=pricing)
        cost = scorecards[0].cost
        assert cost["input_tokens"] == 1000
        assert cost["output_tokens"] == 500
        assert cost["total_cost_usd"] == pytest.approx(2.0)

    def test_empty_results_returns_empty_list(self) -> None:
        results = BenchmarkResult()
        scorecards = compute_all_scorecards(results)
        assert scorecards == []

    def test_scenario_tags_forwarded_to_routing_scorecard(self) -> None:
        results = _make_results(
            _make_entry("m", "s1", routing_passed=True, routing_expected="health"),
        )
        tags = {"s1": ["telegram", "health"]}
        scorecards = compute_all_scorecards(results, scenario_tags=tags)
        routing_sc = scorecards[0].routing
        tag_names = [b.tag for b in routing_sc.tag_breakdown]
        assert "telegram" in tag_names

    def test_scenario_routing_forwarded_to_tool_call_scorecard(self) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                tool_calls_passed=True,
                tool_calls_expected=["notify"],
                routing_expected=None,
            ),
        )
        scenario_routing = {"s1": "general"}
        scorecards = compute_all_scorecards(results, scenario_routing=scenario_routing)
        tc_sc = scorecards[0].tool_calls
        butler_names = [b.butler for b in tc_sc.butler_breakdown]
        assert "general" in butler_names

"""Unit tests for tests.e2e.reporting — scorecard file generation.

Tests cover:
1. Directory structure creation (per-model dirs + summary.md)
2. raw-results.json schema: schema_version, per-scenario entries, all required fields
3. routing-scorecard.md: accuracy, tag breakdown, confusion matrix
4. tool-call-scorecard.md: accuracy, butler breakdown, fail details
5. cost-summary.md: totals and per-scenario breakdown
6. summary.md: cross-model comparison table sorted by routing accuracy descending
7. Output path printed to terminal
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.benchmark import BenchmarkEntry, BenchmarkResult
from tests.e2e.reporting import generate_scorecards
from tests.e2e.scoring import compute_all_scorecards

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
        timed_out=False,
        error=None,
    )


def _make_results(*entries: BenchmarkEntry) -> BenchmarkResult:
    results = BenchmarkResult()
    for entry in entries:
        results.record(entry)
    return results


def _build_and_generate(
    results: BenchmarkResult,
    tmp_path: Path,
    *,
    scenario_tags: dict[str, list[str]] | None = None,
    scenario_routing: dict[str, str | None] | None = None,
    pricing: dict | None = None,
) -> tuple[Path, list]:
    """Helper: compute scorecards and generate files, return (output_dir, scorecards)."""
    scorecards = compute_all_scorecards(
        results,
        scenario_tags=scenario_tags,
        scenario_routing=scenario_routing,
        pricing=pricing or {},
    )
    out_dir = generate_scorecards(
        results,
        scorecards,
        base_dir=tmp_path / "scorecards",
        timestamp="20260312-143000",
    )
    return out_dir, scorecards


# ---------------------------------------------------------------------------
# Directory structure tests
# ---------------------------------------------------------------------------


class TestDirectoryStructure:
    def test_creates_timestamped_directory(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("sonnet", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        assert out_dir.exists()
        assert out_dir.name == "20260312-143000"

    def test_creates_per_model_subdirectory(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("sonnet", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        assert (out_dir / "sonnet").is_dir()

    def test_creates_all_four_per_model_files(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("haiku", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        model_dir = out_dir / "haiku"
        assert (model_dir / "routing-scorecard.md").exists()
        assert (model_dir / "tool-call-scorecard.md").exists()
        assert (model_dir / "cost-summary.md").exists()
        assert (model_dir / "raw-results.json").exists()

    def test_creates_summary_md(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("sonnet", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        assert (out_dir / "summary.md").exists()

    def test_creates_directories_for_multiple_models(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry("sonnet", "s1"),
            _make_entry("haiku", "s1"),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        assert (out_dir / "sonnet").is_dir()
        assert (out_dir / "haiku").is_dir()

    def test_returns_output_dir_path(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("m", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        assert isinstance(out_dir, Path)
        assert out_dir.exists()


# ---------------------------------------------------------------------------
# raw-results.json tests
# ---------------------------------------------------------------------------


class TestRawResultsJson:
    def _load_raw(self, out_dir: Path, model: str) -> dict:
        path = out_dir / model / "raw-results.json"
        return json.loads(path.read_text())

    def test_schema_version_field_present(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("m", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        data = self._load_raw(out_dir, "m")
        assert "schema_version" in data
        assert data["schema_version"] == "1.0"

    def test_model_field_present(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("my-model", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        data = self._load_raw(out_dir, "my-model")
        assert data["model"] == "my-model"

    def test_results_array_has_correct_count(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry("m", "s1"),
            _make_entry("m", "s2"),
            _make_entry("m", "s3"),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        data = self._load_raw(out_dir, "m")
        assert len(data["results"]) == 3

    def test_per_scenario_entry_has_all_required_fields(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                routing_passed=True,
                routing_expected="health",
                routing_actual="health",
                tool_calls_passed=True,
                tool_calls_expected=["log_meal"],
                tool_calls_actual=["log_meal"],
                input_tokens=500,
                output_tokens=200,
                duration_ms=1234,
            ),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        data = self._load_raw(out_dir, "m")
        entry = data["results"][0]

        required_fields = [
            "scenario_id",
            "routing_expected",
            "routing_actual",
            "routing_pass",
            "tool_calls_expected",
            "tool_calls_actual",
            "tool_calls_pass",
            "input_tokens",
            "output_tokens",
            "duration_ms",
        ]
        for field in required_fields:
            assert field in entry, f"Missing field: {field}"

    def test_per_scenario_entry_values_correct(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "my-scenario",
                routing_passed=True,
                routing_expected="health",
                routing_actual="health",
                tool_calls_passed=True,
                tool_calls_expected=["log_meal"],
                tool_calls_actual=["state_get", "log_meal"],
                input_tokens=300,
                output_tokens=150,
                duration_ms=999,
            ),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        data = self._load_raw(out_dir, "m")
        entry = data["results"][0]

        assert entry["scenario_id"] == "my-scenario"
        assert entry["routing_expected"] == "health"
        assert entry["routing_actual"] == "health"
        assert entry["routing_pass"] is True
        assert entry["tool_calls_expected"] == ["log_meal"]
        assert "state_get" in entry["tool_calls_actual"]
        assert "log_meal" in entry["tool_calls_actual"]
        assert entry["tool_calls_pass"] is True
        assert entry["input_tokens"] == 300
        assert entry["output_tokens"] == 150
        assert entry["duration_ms"] == 999

    def test_results_sorted_by_scenario_id(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry("m", "c-scenario"),
            _make_entry("m", "a-scenario"),
            _make_entry("m", "b-scenario"),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        data = self._load_raw(out_dir, "m")
        ids = [r["scenario_id"] for r in data["results"]]
        assert ids == sorted(ids)

    def test_routing_expected_can_be_null(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry("m", "s1", routing_expected=None, routing_actual=None),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        data = self._load_raw(out_dir, "m")
        assert data["results"][0]["routing_expected"] is None


# ---------------------------------------------------------------------------
# routing-scorecard.md tests
# ---------------------------------------------------------------------------


class TestRoutingScorecardMd:
    def test_contains_model_name(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("test-model", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "test-model" / "routing-scorecard.md").read_text()
        assert "test-model" in content

    def test_contains_accuracy_percentage(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry("m", "s1", routing_passed=True),
            _make_entry("m", "s2", routing_passed=True),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "m" / "routing-scorecard.md").read_text()
        assert "100.0%" in content

    def test_contains_correct_count(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry("m", "s1", routing_passed=True),
            _make_entry("m", "s2", routing_passed=False, routing_actual="general"),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "m" / "routing-scorecard.md").read_text()
        assert "1 / 2" in content

    def test_contains_confusion_matrix_when_misroutes(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry(
                "m", "s1", routing_passed=False, routing_expected="health", routing_actual="general"
            ),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "m" / "routing-scorecard.md").read_text()
        assert "health" in content
        assert "general" in content

    def test_contains_no_misroutes_message_when_all_pass(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("m", "s1", routing_passed=True))
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "m" / "routing-scorecard.md").read_text()
        assert "No misroutes" in content

    def test_contains_tag_breakdown_when_tags_provided(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry("m", "s1", routing_passed=True, routing_expected="health"),
        )
        out_dir, _ = _build_and_generate(
            results,
            tmp_path,
            scenario_tags={"s1": ["telegram", "health"]},
        )
        content = (out_dir / "m" / "routing-scorecard.md").read_text()
        assert "telegram" in content


# ---------------------------------------------------------------------------
# tool-call-scorecard.md tests
# ---------------------------------------------------------------------------


class TestToolCallScorecardMd:
    def test_contains_model_name(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry(
                "sonnet", "s1", tool_calls_expected=["notify"], tool_calls_actual=["notify"]
            ),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "sonnet" / "tool-call-scorecard.md").read_text()
        assert "sonnet" in content

    def test_contains_accuracy(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry("m", "s1", tool_calls_passed=True, tool_calls_expected=["log_meal"]),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "m" / "tool-call-scorecard.md").read_text()
        assert "100.0%" in content

    def test_contains_missing_tools_in_failing_section(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                tool_calls_passed=False,
                tool_calls_expected=["calendar_create", "notify"],
                tool_calls_actual=["calendar_create"],
                routing_expected="calendar",
            ),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "m" / "tool-call-scorecard.md").read_text()
        assert "notify" in content
        assert "s1" in content

    def test_contains_all_passed_message_when_no_failures(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                tool_calls_passed=True,
                tool_calls_expected=["notify"],
                tool_calls_actual=["notify"],
            ),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "m" / "tool-call-scorecard.md").read_text()
        assert "All tool-call scenarios passed" in content

    def test_contains_butler_breakdown(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry(
                "m",
                "s1",
                tool_calls_passed=True,
                tool_calls_expected=["log_meal"],
                routing_expected="health",
            ),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "m" / "tool-call-scorecard.md").read_text()
        assert "health" in content


# ---------------------------------------------------------------------------
# cost-summary.md tests
# ---------------------------------------------------------------------------


class TestCostSummaryMd:
    def test_contains_model_name(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("haiku", "s1", input_tokens=1000))
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "haiku" / "cost-summary.md").read_text()
        assert "haiku" in content

    def test_contains_token_totals(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry("m", "s1", input_tokens=5000, output_tokens=2000),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "m" / "cost-summary.md").read_text()
        assert "5,000" in content
        assert "2,000" in content

    def test_contains_estimated_cost(self, tmp_path: Path) -> None:
        pricing = {"m": {"input_price_per_token": 0.001, "output_price_per_token": 0.002}}
        results = _make_results(
            _make_entry("m", "s1", input_tokens=1000, output_tokens=500),
        )
        out_dir, _ = _build_and_generate(results, tmp_path, pricing=pricing)
        content = (out_dir / "m" / "cost-summary.md").read_text()
        # $1.00 input + $1.00 output = $2.00
        assert "$2.0000" in content

    def test_contains_per_scenario_breakdown(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry("m", "s1", duration_ms=999),
            _make_entry("m", "s2", duration_ms=1234),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "m" / "cost-summary.md").read_text()
        assert "s1" in content
        assert "s2" in content
        assert "999" in content
        assert "1234" in content


# ---------------------------------------------------------------------------
# summary.md tests
# ---------------------------------------------------------------------------


class TestSummaryMd:
    def test_contains_all_model_names(self, tmp_path: Path) -> None:
        results = _make_results(
            _make_entry("sonnet", "s1"),
            _make_entry("haiku", "s1"),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "summary.md").read_text()
        assert "sonnet" in content
        assert "haiku" in content

    def test_contains_routing_accuracy_column(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("m", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "summary.md").read_text()
        assert "Routing Accuracy" in content

    def test_contains_tool_call_accuracy_column(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("m", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "summary.md").read_text()
        assert "Tool-Call Accuracy" in content

    def test_contains_token_columns(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("m", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "summary.md").read_text()
        assert "Input Tokens" in content
        assert "Output Tokens" in content

    def test_contains_cost_column(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("m", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "summary.md").read_text()
        assert "Est. Cost" in content

    def test_sorted_by_routing_accuracy_descending(self, tmp_path: Path) -> None:
        """Higher accuracy model must appear before lower accuracy model in summary."""
        results = _make_results(
            # model-high: 100% routing
            _make_entry("model-high", "s1", routing_passed=True, routing_expected="health"),
            # model-low: 0% routing
            _make_entry(
                "model-low",
                "s1",
                routing_passed=False,
                routing_expected="health",
                routing_actual="general",
            ),
        )
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "summary.md").read_text()
        pos_high = content.index("model-high")
        pos_low = content.index("model-low")
        assert pos_high < pos_low, "model-high (100%) should appear before model-low (0%)"

    def test_is_markdown_table(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("m", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "summary.md").read_text()
        # Markdown table uses pipe separators
        assert "|" in content

    def test_contains_per_model_detail_section(self, tmp_path: Path) -> None:
        results = _make_results(_make_entry("my-model", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        content = (out_dir / "summary.md").read_text()
        assert "Per-Model Details" in content


# ---------------------------------------------------------------------------
# Output path printed to terminal
# ---------------------------------------------------------------------------


class TestOutputPathPrinted:
    def test_output_path_printed(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """Acceptance criterion 8: output path is printed to terminal."""
        results = _make_results(_make_entry("m", "s1"))
        out_dir, _ = _build_and_generate(results, tmp_path)
        captured = capsys.readouterr()
        assert str(out_dir.resolve()) in captured.out

    def test_output_path_contains_scorecard_dir_label(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        results = _make_results(_make_entry("m", "s1"))
        _build_and_generate(results, tmp_path)
        captured = capsys.readouterr()
        assert "Scorecard output directory" in captured.out

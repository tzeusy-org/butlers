"""Unit tests for the E2E benchmark harness.

These tests run without the E2E ecosystem — no Docker, no LLM calls, no real DB.
They verify:
- BenchmarkEntry and BenchmarkResult dataclass structures
- BenchmarkResult.record() and summary() semantics
- resolve_benchmark_models() CLI/env var resolution logic
- Benchmark runner loop orchestration (with mocked DB and scenario runner)

Acceptance criteria verified:
1. BenchmarkResult accumulates entries keyed by (model, scenario_id)
2. summary() computes routing and tool-call accuracy correctly
3. resolve_benchmark_models prefers CLI over env var, strips whitespace
4. resolve_benchmark_models returns None when neither source is set
5. Benchmark runner calls pin_model before and unpin_model after each model
6. try/finally ensures unpin_model is called even when scenario runner raises
7. No interleaving: all scenarios for model A complete before model B starts
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.e2e.benchmark import (
    _BENCHMARK_PRIORITY,
    _BENCHMARK_SOURCE,
    BenchmarkEntry,
    BenchmarkResult,
    resolve_benchmark_models,
    run_benchmark,
)

# ---------------------------------------------------------------------------
# BenchmarkEntry tests
# ---------------------------------------------------------------------------


class TestBenchmarkEntry:
    """Tests for the BenchmarkEntry dataclass."""

    def test_benchmark_entry_has_required_fields(self) -> None:
        entry = BenchmarkEntry(
            model="claude-sonnet-4-5",
            scenario_id="email-meeting-invite",
            routing_passed=True,
            routing_expected="calendar",
            routing_actual="calendar",
            tool_calls_passed=True,
            tool_calls_expected=["calendar_create"],
            tool_calls_actual=["calendar_create", "state_get"],
            input_tokens=100,
            output_tokens=50,
            duration_ms=1234,
            timed_out=False,
        )
        assert entry.model == "claude-sonnet-4-5"
        assert entry.scenario_id == "email-meeting-invite"
        assert entry.routing_passed is True
        assert entry.routing_expected == "calendar"
        assert entry.routing_actual == "calendar"
        assert entry.tool_calls_passed is True
        assert entry.tool_calls_expected == ["calendar_create"]
        assert entry.tool_calls_actual == ["calendar_create", "state_get"]
        assert entry.input_tokens == 100
        assert entry.output_tokens == 50
        assert entry.duration_ms == 1234
        assert entry.timed_out is False
        assert entry.error is None

    def test_benchmark_entry_error_defaults_to_none(self) -> None:
        entry = _make_entry(model="m", scenario_id="s1")
        assert entry.error is None

    def test_benchmark_entry_error_can_be_set(self) -> None:
        entry = _make_entry(model="m", scenario_id="s1", error="connection refused")
        assert entry.error == "connection refused"

    def test_benchmark_entry_is_dataclass(self) -> None:
        """BenchmarkEntry supports dataclass operations like asdict."""
        entry = _make_entry(model="m", scenario_id="s1")
        d = asdict(entry)
        assert d["model"] == "m"
        assert d["scenario_id"] == "s1"


# ---------------------------------------------------------------------------
# BenchmarkResult tests
# ---------------------------------------------------------------------------


class TestBenchmarkResult:
    """Tests for the BenchmarkResult accumulator."""

    def test_empty_result_has_no_models(self) -> None:
        results = BenchmarkResult()
        assert results.all_models() == []
        assert results.all_entries() == []

    def test_record_single_entry(self) -> None:
        results = BenchmarkResult()
        entry = _make_entry(model="model-a", scenario_id="scenario-1")
        results.record(entry)
        assert results.all_models() == ["model-a"]
        assert len(results.all_entries()) == 1

    def test_record_multiple_models(self) -> None:
        results = BenchmarkResult()
        results.record(_make_entry(model="model-a", scenario_id="s1"))
        results.record(_make_entry(model="model-b", scenario_id="s1"))
        results.record(_make_entry(model="model-c", scenario_id="s1"))
        assert results.all_models() == ["model-a", "model-b", "model-c"]

    def test_record_overwrites_same_key(self) -> None:
        """Recording (model, scenario_id) twice replaces the first entry."""
        results = BenchmarkResult()
        e1 = _make_entry(model="m", scenario_id="s", routing_passed=False)
        e2 = _make_entry(model="m", scenario_id="s", routing_passed=True)
        results.record(e1)
        results.record(e2)
        assert len(results.all_entries()) == 1
        assert results.all_entries()[0].routing_passed is True

    def test_for_model_returns_only_that_model(self) -> None:
        results = BenchmarkResult()
        results.record(_make_entry(model="model-a", scenario_id="s1"))
        results.record(_make_entry(model="model-a", scenario_id="s2"))
        results.record(_make_entry(model="model-b", scenario_id="s1"))

        model_a = results.for_model("model-a")
        assert len(model_a) == 2
        assert all(e.model == "model-a" for e in model_a)

    def test_for_model_sorted_by_scenario_id(self) -> None:
        results = BenchmarkResult()
        results.record(_make_entry(model="m", scenario_id="c-scenario"))
        results.record(_make_entry(model="m", scenario_id="a-scenario"))
        results.record(_make_entry(model="m", scenario_id="b-scenario"))

        entries = results.for_model("m")
        assert [e.scenario_id for e in entries] == ["a-scenario", "b-scenario", "c-scenario"]

    def test_for_model_missing_returns_empty(self) -> None:
        results = BenchmarkResult()
        assert results.for_model("nonexistent-model") == []

    def test_all_entries_sorted_by_model_then_scenario(self) -> None:
        results = BenchmarkResult()
        results.record(_make_entry(model="b-model", scenario_id="s2"))
        results.record(_make_entry(model="a-model", scenario_id="s1"))
        results.record(_make_entry(model="a-model", scenario_id="s2"))
        results.record(_make_entry(model="b-model", scenario_id="s1"))

        entries = results.all_entries()
        assert [(e.model, e.scenario_id) for e in entries] == [
            ("a-model", "s1"),
            ("a-model", "s2"),
            ("b-model", "s1"),
            ("b-model", "s2"),
        ]


# ---------------------------------------------------------------------------
# BenchmarkResult.summary() tests
# ---------------------------------------------------------------------------


class TestBenchmarkResultSummary:
    """Tests for the BenchmarkResult.summary() computation."""

    def test_summary_empty_results(self) -> None:
        results = BenchmarkResult()
        assert results.summary() == {}

    def test_summary_perfect_accuracy(self) -> None:
        results = BenchmarkResult()
        results.record(
            _make_entry(
                model="m",
                scenario_id="s1",
                routing_passed=True,
                routing_expected="health",
                tool_calls_passed=True,
                tool_calls_expected=["log_measurement"],
            )
        )
        results.record(
            _make_entry(
                model="m",
                scenario_id="s2",
                routing_passed=True,
                routing_expected="calendar",
                tool_calls_passed=True,
                tool_calls_expected=["calendar_create"],
            )
        )

        summary = results.summary()
        assert summary["m"]["routing_accuracy"] == 1.0
        assert summary["m"]["tool_call_accuracy"] == 1.0
        assert summary["m"]["routing_passed"] == 2
        assert summary["m"]["routing_total"] == 2
        assert summary["m"]["tool_calls_passed"] == 2
        assert summary["m"]["tool_calls_total"] == 2

    def test_summary_partial_accuracy(self) -> None:
        results = BenchmarkResult()
        results.record(
            _make_entry(
                model="m",
                scenario_id="s1",
                routing_passed=True,
                routing_expected="health",
            )
        )
        results.record(
            _make_entry(
                model="m",
                scenario_id="s2",
                routing_passed=False,
                routing_expected="calendar",
            )
        )

        summary = results.summary()
        assert summary["m"]["routing_accuracy"] == 0.5
        assert summary["m"]["routing_passed"] == 1
        assert summary["m"]["routing_total"] == 2

    def test_summary_skips_multi_target_routing(self) -> None:
        """Scenarios with expected_routing=None don't count in routing totals."""
        results = BenchmarkResult()
        results.record(
            _make_entry(
                model="m",
                scenario_id="s1",
                routing_passed=True,
                routing_expected=None,  # multi-target, skipped
            )
        )
        results.record(
            _make_entry(
                model="m",
                scenario_id="s2",
                routing_passed=True,
                routing_expected="health",
            )
        )

        summary = results.summary()
        assert summary["m"]["routing_total"] == 1
        assert summary["m"]["routing_passed"] == 1
        assert summary["m"]["routing_accuracy"] == 1.0

    def test_summary_tool_call_accuracy_no_tool_expectations(self) -> None:
        """Scenarios with no expected_tool_calls don't count in tool-call totals."""
        results = BenchmarkResult()
        results.record(
            _make_entry(
                model="m",
                scenario_id="s1",
                tool_calls_expected=[],  # no expectations
            )
        )

        summary = results.summary()
        assert summary["m"]["tool_calls_total"] == 0
        assert summary["m"]["tool_call_accuracy"] == 0.0

    def test_summary_token_aggregation(self) -> None:
        results = BenchmarkResult()
        results.record(_make_entry(model="m", scenario_id="s1", input_tokens=100, output_tokens=50))
        results.record(_make_entry(model="m", scenario_id="s2", input_tokens=200, output_tokens=75))

        summary = results.summary()
        assert summary["m"]["input_tokens"] == 300
        assert summary["m"]["output_tokens"] == 125

    def test_summary_timeout_count(self) -> None:
        results = BenchmarkResult()
        results.record(_make_entry(model="m", scenario_id="s1", timed_out=False))
        results.record(_make_entry(model="m", scenario_id="s2", timed_out=True))
        results.record(_make_entry(model="m", scenario_id="s3", timed_out=True))

        summary = results.summary()
        assert summary["m"]["timed_out"] == 2

    def test_summary_multiple_models(self) -> None:
        results = BenchmarkResult()
        results.record(
            _make_entry(
                model="model-a", scenario_id="s1", routing_passed=True, routing_expected="h"
            )
        )
        results.record(
            _make_entry(
                model="model-b", scenario_id="s1", routing_passed=False, routing_expected="h"
            )
        )

        summary = results.summary()
        assert "model-a" in summary
        assert "model-b" in summary
        assert summary["model-a"]["routing_accuracy"] == 1.0
        assert summary["model-b"]["routing_accuracy"] == 0.0

    def test_summary_total_scenarios(self) -> None:
        results = BenchmarkResult()
        for i in range(5):
            results.record(_make_entry(model="m", scenario_id=f"s{i}"))

        summary = results.summary()
        assert summary["m"]["total_scenarios"] == 5


# ---------------------------------------------------------------------------
# resolve_benchmark_models() tests
# ---------------------------------------------------------------------------


class TestResolveBenchmarkModels:
    """Tests for the resolve_benchmark_models() helper."""

    def test_returns_none_when_no_input(self) -> None:
        with _no_env("E2E_BENCHMARK_MODELS"):
            result = resolve_benchmark_models(None)
        assert result is None

    def test_cli_value_parsed_correctly(self) -> None:
        with _no_env("E2E_BENCHMARK_MODELS"):
            result = resolve_benchmark_models("claude-sonnet-4-5,gpt-4o")
        assert result == ["claude-sonnet-4-5", "gpt-4o"]

    def test_cli_value_strips_whitespace(self) -> None:
        with _no_env("E2E_BENCHMARK_MODELS"):
            result = resolve_benchmark_models("  claude-sonnet-4-5 , gpt-4o  ")
        assert result == ["claude-sonnet-4-5", "gpt-4o"]

    def test_cli_value_ignores_empty_segments(self) -> None:
        with _no_env("E2E_BENCHMARK_MODELS"):
            result = resolve_benchmark_models("claude-sonnet-4-5,,gpt-4o,")
        assert result == ["claude-sonnet-4-5", "gpt-4o"]

    def test_env_var_fallback(self) -> None:
        with _set_env("E2E_BENCHMARK_MODELS", "gemini-2.0-flash"):
            result = resolve_benchmark_models(None)
        assert result == ["gemini-2.0-flash"]

    def test_cli_overrides_env_var(self) -> None:
        with _set_env("E2E_BENCHMARK_MODELS", "env-model"):
            result = resolve_benchmark_models("cli-model")
        assert result == ["cli-model"]

    def test_single_model(self) -> None:
        with _no_env("E2E_BENCHMARK_MODELS"):
            result = resolve_benchmark_models("claude-opus-4-6")
        assert result == ["claude-opus-4-6"]

    def test_env_var_multiple_models(self) -> None:
        with _set_env("E2E_BENCHMARK_MODELS", "a,b,c"):
            result = resolve_benchmark_models(None)
        assert result == ["a", "b", "c"]

    def test_empty_cli_falls_back_to_env(self) -> None:
        with _set_env("E2E_BENCHMARK_MODELS", "env-model"):
            # Empty string is treated as falsy — should fall back to env
            result = resolve_benchmark_models("")
        assert result == ["env-model"]

    def test_custom_env_var_name(self) -> None:
        with _set_env("CUSTOM_MODELS_VAR", "custom-model"):
            result = resolve_benchmark_models(None, env_var="CUSTOM_MODELS_VAR")
        assert result == ["custom-model"]


# ---------------------------------------------------------------------------
# run_benchmark() tests (mocked DB and scenario runner)
# ---------------------------------------------------------------------------


class TestRunBenchmark:
    """Tests for the benchmark runner loop orchestration."""

    @pytest.mark.asyncio
    async def test_pin_then_unpin_for_each_model(self) -> None:
        """For each model: pin_model is called before scenarios, unpin_model after."""
        models = ["model-a", "model-b"]
        scenarios = [_make_mock_scenario("s1"), _make_mock_scenario("s2")]
        pool = MagicMock()
        butler_names = ["general", "health"]

        with (
            patch("tests.e2e.benchmark.pin_model", new_callable=AsyncMock) as mock_pin,
            patch("tests.e2e.benchmark.unpin_model", new_callable=AsyncMock) as mock_unpin,
        ):
            mock_unpin.return_value = 2
            run_order: list[str] = []

            async def track_pin(p: Any, model: str, butlers: Any, **kwargs: Any) -> str:
                run_order.append(f"pin:{model}")
                return "catalog-id"

            async def track_unpin(p: Any) -> int:
                run_order.append("unpin")
                return 2

            mock_pin.side_effect = track_pin
            mock_unpin.side_effect = track_unpin

            async def fake_run_scenario(scenario: Any) -> Any:
                return _make_scenario_result(scenario.id)

            await run_benchmark(
                models=models,
                pool=pool,
                butler_names=butler_names,
                scenarios=scenarios,
                run_scenario_fn=fake_run_scenario,
            )

        # Order: pin:model-a → scenarios → unpin → pin:model-b → scenarios → unpin
        assert run_order == ["pin:model-a", "unpin", "pin:model-b", "unpin"]

    @pytest.mark.asyncio
    async def test_scenarios_not_interleaved_across_models(self) -> None:
        """All scenarios for model A run before any scenario for model B."""
        models = ["model-a", "model-b"]
        scenarios = [
            _make_mock_scenario("s1"),
            _make_mock_scenario("s2"),
            _make_mock_scenario("s3"),
        ]
        pool = MagicMock()
        butler_names = ["general"]
        execution_log: list[tuple[str, str]] = []

        with (
            patch("tests.e2e.benchmark.pin_model", new_callable=AsyncMock) as mock_pin,
            patch("tests.e2e.benchmark.unpin_model", new_callable=AsyncMock) as mock_unpin,
        ):
            current_model: list[str] = []

            async def track_pin(p: Any, model: str, butlers: Any, **kwargs: Any) -> str:
                current_model.clear()
                current_model.append(model)
                return "id"

            mock_pin.side_effect = track_pin
            mock_unpin.return_value = 2

            async def run_scenario_fn(scenario: Any) -> Any:
                execution_log.append((current_model[0], scenario.id))
                return _make_scenario_result(scenario.id)

            await run_benchmark(
                models=models,
                pool=pool,
                butler_names=butler_names,
                scenarios=scenarios,
                run_scenario_fn=run_scenario_fn,
            )

        assert execution_log == [
            ("model-a", "s1"),
            ("model-a", "s2"),
            ("model-a", "s3"),
            ("model-b", "s1"),
            ("model-b", "s2"),
            ("model-b", "s3"),
        ]

    @pytest.mark.asyncio
    async def test_unpin_called_even_if_scenario_raises(self) -> None:
        """try/finally: unpin_model is called even when a scenario raises an exception."""
        pool = MagicMock()
        models = ["model-a"]
        scenarios = [_make_mock_scenario("s1")]
        butler_names = ["general"]

        with (
            patch("tests.e2e.benchmark.pin_model", new_callable=AsyncMock),
            patch("tests.e2e.benchmark.unpin_model", new_callable=AsyncMock) as mock_unpin,
        ):
            mock_unpin.return_value = 1

            async def crashing_scenario(scenario: Any) -> Any:
                raise RuntimeError("Simulated LLM failure")

            results = await run_benchmark(
                models=models,
                pool=pool,
                butler_names=butler_names,
                scenarios=scenarios,
                run_scenario_fn=crashing_scenario,
            )

        mock_unpin.assert_called_once()
        # Error should be captured in the entry, not raised
        entry = results.for_model("model-a")[0]
        assert entry.error is not None
        assert "Simulated LLM failure" in entry.error

    @pytest.mark.asyncio
    async def test_results_accumulate_across_models(self) -> None:
        """BenchmarkResult accumulates entries for all models."""
        models = ["model-a", "model-b"]
        scenarios = [_make_mock_scenario("s1"), _make_mock_scenario("s2")]
        pool = MagicMock()
        butler_names = ["general"]

        with (
            patch("tests.e2e.benchmark.pin_model", new_callable=AsyncMock),
            patch("tests.e2e.benchmark.unpin_model", new_callable=AsyncMock) as mock_unpin,
        ):
            mock_unpin.return_value = 0

            async def run_scenario_fn(scenario: Any) -> Any:
                return _make_scenario_result(scenario.id, routing_passed=True)

            results = await run_benchmark(
                models=models,
                pool=pool,
                butler_names=butler_names,
                scenarios=scenarios,
                run_scenario_fn=run_scenario_fn,
            )

        assert len(results.all_entries()) == 4  # 2 models × 2 scenarios
        assert "model-a" in results.all_models()
        assert "model-b" in results.all_models()
        assert len(results.for_model("model-a")) == 2
        assert len(results.for_model("model-b")) == 2

    @pytest.mark.asyncio
    async def test_pin_model_called_with_correct_butler_names(self) -> None:
        """pin_model is called with the exact butler_names list provided."""
        pool = MagicMock()
        models = ["model-a"]
        butler_names = ["switchboard", "general", "health", "calendar"]
        scenarios = [_make_mock_scenario("s1")]

        with (
            patch("tests.e2e.benchmark.pin_model", new_callable=AsyncMock) as mock_pin,
            patch("tests.e2e.benchmark.unpin_model", new_callable=AsyncMock) as mock_unpin,
        ):
            mock_pin.return_value = "catalog-id"
            mock_unpin.return_value = 0

            async def run_scenario_fn(scenario: Any) -> Any:
                return _make_scenario_result(scenario.id)

            await run_benchmark(
                models=models,
                pool=pool,
                butler_names=butler_names,
                scenarios=scenarios,
                run_scenario_fn=run_scenario_fn,
            )

        mock_pin.assert_called_once_with(pool, "model-a", butler_names, runtime_type="claude")

    @pytest.mark.asyncio
    async def test_empty_model_list_returns_empty_result(self) -> None:
        """Empty model list produces empty BenchmarkResult."""
        with (
            patch("tests.e2e.benchmark.pin_model", new_callable=AsyncMock) as mock_pin,
            patch("tests.e2e.benchmark.unpin_model", new_callable=AsyncMock) as mock_unpin,
        ):
            results = await run_benchmark(
                models=[],
                pool=MagicMock(),
                butler_names=["general"],
                scenarios=[_make_mock_scenario("s1")],
                run_scenario_fn=AsyncMock(return_value=_make_scenario_result("s1")),
            )

        mock_pin.assert_not_called()
        mock_unpin.assert_not_called()
        assert results.all_entries() == []


# ---------------------------------------------------------------------------
# Constants validation
# ---------------------------------------------------------------------------


class TestBenchmarkConstants:
    """Validate module-level constants used for DB cleanup identification."""

    def test_benchmark_source_tag(self) -> None:
        """_BENCHMARK_SOURCE is the expected crash-cleanup tag."""
        assert _BENCHMARK_SOURCE == "e2e-benchmark"

    def test_benchmark_priority_value(self) -> None:
        """_BENCHMARK_PRIORITY exceeds all realistic production priorities."""
        assert _BENCHMARK_PRIORITY == 999


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_entry(
    *,
    model: str = "test-model",
    scenario_id: str = "s1",
    routing_passed: bool = True,
    routing_expected: str | None = "health",
    routing_actual: str | None = "health",
    tool_calls_passed: bool = True,
    tool_calls_expected: list[str] | None = None,
    tool_calls_actual: list[str] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int = 100,
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


def _make_mock_scenario(scenario_id: str) -> Any:
    """Build a minimal mock Scenario object for runner tests."""
    s = MagicMock()
    s.id = scenario_id
    s.expected_routing = "health"
    s.expected_tool_calls = []
    return s


def _make_scenario_result(scenario_id: str, *, routing_passed: bool = True) -> Any:
    """Build a minimal mock ScenarioResult for runner tests."""
    result = MagicMock()
    result.routing = MagicMock()
    result.routing.passed = routing_passed
    result.routing.actual = "health"
    result.tool_calls = MagicMock()
    result.tool_calls.passed = True
    result.tool_calls.actual_names = []
    result.duration_ms = 100
    result.timed_out = False
    result.error = None
    return result


class _no_env:
    """Context manager that temporarily removes an env var."""

    def __init__(self, key: str) -> None:
        self._key = key
        self._original: str | None = None

    def __enter__(self) -> None:
        self._original = os.environ.pop(self._key, None)

    def __exit__(self, *args: Any) -> None:
        if self._original is not None:
            os.environ[self._key] = self._original
        else:
            os.environ.pop(self._key, None)


class _set_env:
    """Context manager that temporarily sets an env var."""

    def __init__(self, key: str, value: str) -> None:
        self._key = key
        self._value = value
        self._original: str | None = None

    def __enter__(self) -> None:
        self._original = os.environ.get(self._key)
        os.environ[self._key] = self._value

    def __exit__(self, *args: Any) -> None:
        if self._original is not None:
            os.environ[self._key] = self._original
        else:
            os.environ.pop(self._key, None)

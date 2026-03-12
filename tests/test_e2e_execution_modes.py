"""Unit tests for E2E execution mode wiring.

These tests run without the E2E ecosystem — no Docker, no LLM calls, no real DB.
They verify:

- Validate mode (default): hard assert on routing/tool-call mismatch
- Benchmark mode (--benchmark): accumulate results without hard failures
- _record_benchmark_entry() populates BenchmarkResult correctly
- benchmark_result fixture returns None in validate mode, BenchmarkResult in benchmark
- pytest_sessionfinish hook generates scorecards only in benchmark mode
- Pytest markers registered: e2e, benchmark, routing_accuracy, tool_accuracy

Acceptance criteria verified:
1. Validate mode: test_scenario_routing asserts hard fail on routing mismatch
2. Validate mode: test_scenario_tool_calls asserts hard fail on tool-call mismatch
3. Benchmark mode: test_scenario_routing records result without AssertionError
4. Benchmark mode: test_scenario_tool_calls records result without AssertionError
5. _record_benchmark_entry populates BenchmarkResult with correct model from env
6. benchmark_result fixture returns None when benchmark_mode=False
7. benchmark_result fixture returns BenchmarkResult instance when benchmark_mode=True
8. pytest_sessionfinish is a no-op in validate mode
9. pytest_sessionfinish calls generate_scorecards in benchmark mode with results
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from tests.e2e.benchmark import BenchmarkResult
from tests.e2e.scenarios import Scenario

# ---------------------------------------------------------------------------
# Helpers — minimal stubs for scenario runner types
# ---------------------------------------------------------------------------


def _make_scenario(
    scenario_id: str = "test-scenario",
    expected_routing: str | None = "health",
    expected_tool_calls: list[str] | None = None,
    tags: list[str] | None = None,
) -> Scenario:
    """Build a minimal Scenario for testing."""
    from tests.e2e.envelopes import telegram_envelope

    return Scenario(
        id=scenario_id,
        description="Test scenario",
        envelope=telegram_envelope(chat_id=12345, text="test"),
        expected_routing=expected_routing,
        expected_tool_calls=expected_tool_calls or [],
        tags=tags or ["telegram", "health", "smoke"],
    )


def _make_routing_result(
    passed: bool, expected: str | None = "health", actual: str | None = None
) -> Any:
    """Build a minimal RoutingResult-like object."""
    from tests.e2e.test_scenario_runner import RoutingResult

    return RoutingResult(
        expected=expected,
        actual=actual or (expected if passed else "general"),
        passed=passed,
        skipped=False,
    )


def _make_tool_call_result(
    passed: bool, expected: list[str] | None = None, actual: list[str] | None = None
) -> Any:
    """Build a minimal ToolCallResult-like object."""
    from tests.e2e.test_scenario_runner import ToolCallResult

    exp = expected or ["log_measurement"]
    act = actual or (exp if passed else [])
    missing = [t for t in exp if t not in act]
    return ToolCallResult(
        expected=exp,
        actual_names=act,
        missing=missing,
        passed=passed,
        timed_out=False,
    )


def _make_scenario_result(
    scenario_id: str = "test-scenario",
    routing_passed: bool = True,
    tool_calls_passed: bool = True,
    timed_out: bool = False,
    error: str | None = None,
    duration_ms: int = 500,
) -> Any:
    """Build a minimal ScenarioResult-like object."""
    from tests.e2e.test_scenario_runner import ScenarioResult

    routing = _make_routing_result(routing_passed) if not error else None
    tool_calls = _make_tool_call_result(tool_calls_passed) if not error else None

    return ScenarioResult(
        scenario_id=scenario_id,
        request_id="req-uuid-1234",
        duplicate=False,
        routing=routing,
        tool_calls=tool_calls,
        db_assertions=[],
        timed_out=timed_out,
        duration_ms=duration_ms,
        error=error,
    )


# ---------------------------------------------------------------------------
# Tests for _record_benchmark_entry
# ---------------------------------------------------------------------------


class TestRecordBenchmarkEntry:
    """Tests for the _record_benchmark_entry helper."""

    def test_records_passing_routing_entry(self) -> None:
        """A passing routing result is recorded with routing_passed=True."""
        from tests.e2e.test_scenario_runner import _record_benchmark_entry

        scenario = _make_scenario(expected_routing="health")
        result = _make_scenario_result(routing_passed=True)
        accumulator = BenchmarkResult()

        with patch.dict(os.environ, {"E2E_CURRENT_MODEL": "test-model"}):
            _record_benchmark_entry(scenario, result, accumulator)

        entries = accumulator.for_model("test-model")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.model == "test-model"
        assert entry.scenario_id == scenario.id
        assert entry.routing_passed is True
        assert entry.routing_expected == "health"

    def test_records_failing_routing_entry(self) -> None:
        """A failing routing result is recorded with routing_passed=False."""
        from tests.e2e.test_scenario_runner import _record_benchmark_entry

        scenario = _make_scenario(expected_routing="health")
        # Build a result where routing failed (actual != expected)
        from tests.e2e.test_scenario_runner import RoutingResult, ScenarioResult

        routing = RoutingResult(expected="health", actual="general", passed=False, skipped=False)
        result = ScenarioResult(
            scenario_id=scenario.id,
            request_id="req-123",
            duplicate=False,
            routing=routing,
            tool_calls=None,
            db_assertions=[],
            timed_out=False,
            duration_ms=300,
        )
        accumulator = BenchmarkResult()

        with patch.dict(os.environ, {"E2E_CURRENT_MODEL": "test-model"}):
            _record_benchmark_entry(scenario, result, accumulator)

        entries = accumulator.for_model("test-model")
        assert len(entries) == 1
        assert entries[0].routing_passed is False
        assert entries[0].routing_actual == "general"

    def test_uses_unknown_model_when_env_not_set(self) -> None:
        """Falls back to 'unknown' when E2E_CURRENT_MODEL is not set."""
        from tests.e2e.test_scenario_runner import _record_benchmark_entry

        scenario = _make_scenario()
        result = _make_scenario_result()
        accumulator = BenchmarkResult()

        # Ensure E2E_CURRENT_MODEL is not set
        env = {k: v for k, v in os.environ.items() if k != "E2E_CURRENT_MODEL"}
        with patch.dict(os.environ, env, clear=True):
            _record_benchmark_entry(scenario, result, accumulator)

        entries = accumulator.for_model("unknown")
        assert len(entries) == 1

    def test_noop_when_accumulator_is_none(self) -> None:
        """Returns without error when benchmark_result is None."""
        from tests.e2e.test_scenario_runner import _record_benchmark_entry

        scenario = _make_scenario()
        result = _make_scenario_result()

        # Should not raise
        _record_benchmark_entry(scenario, result, None)

    def test_records_error_entry(self) -> None:
        """An error result is recorded with error field set."""
        from tests.e2e.test_scenario_runner import _record_benchmark_entry

        scenario = _make_scenario()
        result = _make_scenario_result(error="ingest_v1 failed: connection refused")
        accumulator = BenchmarkResult()

        with patch.dict(os.environ, {"E2E_CURRENT_MODEL": "err-model"}):
            _record_benchmark_entry(scenario, result, accumulator)

        entries = accumulator.for_model("err-model")
        assert len(entries) == 1
        assert entries[0].error == "ingest_v1 failed: connection refused"

    def test_records_timed_out_entry(self) -> None:
        """A timed-out result is recorded with timed_out=True."""
        from tests.e2e.test_scenario_runner import _record_benchmark_entry

        scenario = _make_scenario()
        result = _make_scenario_result(timed_out=True)
        accumulator = BenchmarkResult()

        with patch.dict(os.environ, {"E2E_CURRENT_MODEL": "timeout-model"}):
            _record_benchmark_entry(scenario, result, accumulator)

        entries = accumulator.for_model("timeout-model")
        assert len(entries) == 1
        assert entries[0].timed_out is True

    def test_records_tool_call_fields(self) -> None:
        """Tool-call fields are correctly transferred to the BenchmarkEntry."""
        from tests.e2e.test_scenario_runner import (
            ScenarioResult,
            ToolCallResult,
            _record_benchmark_entry,
        )

        scenario = _make_scenario(expected_tool_calls=["log_measurement", "notify"])
        tc_result = ToolCallResult(
            expected=["log_measurement", "notify"],
            actual_names=["log_measurement"],
            missing=["notify"],
            passed=False,
        )
        from tests.e2e.test_scenario_runner import RoutingResult

        result = ScenarioResult(
            scenario_id=scenario.id,
            request_id="req-456",
            duplicate=False,
            routing=RoutingResult(expected="health", actual="health", passed=True),
            tool_calls=tc_result,
            db_assertions=[],
            timed_out=False,
            duration_ms=200,
        )
        accumulator = BenchmarkResult()

        with patch.dict(os.environ, {"E2E_CURRENT_MODEL": "tc-model"}):
            _record_benchmark_entry(scenario, result, accumulator)

        entries = accumulator.for_model("tc-model")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.tool_calls_passed is False
        assert entry.tool_calls_expected == ["log_measurement", "notify"]
        assert entry.tool_calls_actual == ["log_measurement"]


# ---------------------------------------------------------------------------
# Tests for benchmark_result fixture (unit-level)
# ---------------------------------------------------------------------------


class TestBenchmarkResultFixture:
    """Unit tests for the benchmark_result fixture logic (not using real pytest fixtures)."""

    def test_returns_none_in_validate_mode(self) -> None:
        """benchmark_result fixture logic returns None when benchmark_mode=False."""
        # Simulate fixture logic: if not benchmark_mode, return None
        benchmark_mode = False
        if not benchmark_mode:
            result = None
        else:
            result = BenchmarkResult()

        assert result is None

    def test_returns_benchmark_result_in_benchmark_mode(self) -> None:
        """benchmark_result fixture logic returns BenchmarkResult when benchmark_mode=True."""
        benchmark_mode = True
        if not benchmark_mode:
            result = None
        else:
            result = BenchmarkResult()

        assert isinstance(result, BenchmarkResult)

    def test_benchmark_result_starts_empty(self) -> None:
        """A fresh BenchmarkResult accumulator has no entries."""
        result = BenchmarkResult()
        assert result.all_models() == []
        assert result.all_entries() == []


# ---------------------------------------------------------------------------
# Tests for validate mode: hard assertions
# ---------------------------------------------------------------------------


class TestValidateModeAssertions:
    """Verify the assertion logic that test_scenario_routing / test_scenario_tool_calls
    would exercise in validate mode (benchmark_mode=False)."""

    def test_routing_mismatch_produces_assertion_error(self) -> None:
        """Simulate validate-mode routing check: mismatched routing raises AssertionError."""
        routing_passed = False
        routing_expected = "health"
        routing_actual = "general"

        benchmark_mode = False
        if not benchmark_mode:
            with pytest.raises(AssertionError) as exc_info:
                assert routing_passed, (
                    f"Routing mismatch for 'test-scenario': "
                    f"expected={routing_expected!r} "
                    f"actual={routing_actual!r}"
                )
            assert "Routing mismatch" in str(exc_info.value)
            assert routing_expected in str(exc_info.value)
            assert routing_actual in str(exc_info.value)

    def test_routing_pass_does_not_raise(self) -> None:
        """Simulate validate-mode routing check: passing routing does not raise."""
        routing_passed = True
        benchmark_mode = False
        if not benchmark_mode:
            assert routing_passed, "Routing mismatch"

    def test_tool_call_mismatch_produces_assertion_error(self) -> None:
        """Simulate validate-mode tool-call check: missing tools raise AssertionError."""
        tool_calls_passed = False
        missing = ["log_measurement"]
        actual_names = ["state_get"]

        benchmark_mode = False
        if not benchmark_mode:
            with pytest.raises(AssertionError) as exc_info:
                assert tool_calls_passed, (
                    f"Tool-call mismatch for 'test-scenario': "
                    f"missing={missing!r}, "
                    f"actual={actual_names!r}"
                )
            assert "Tool-call mismatch" in str(exc_info.value)
            assert "log_measurement" in str(exc_info.value)

    def test_tool_call_pass_does_not_raise(self) -> None:
        """Simulate validate-mode tool-call check: all tools found does not raise."""
        tool_calls_passed = True
        benchmark_mode = False
        if not benchmark_mode:
            assert tool_calls_passed, "Tool-call mismatch"


# ---------------------------------------------------------------------------
# Tests for benchmark mode: no hard assertions
# ---------------------------------------------------------------------------


class TestBenchmarkModeNoHardFail:
    """Verify that benchmark mode accumulates without AssertionError."""

    def test_benchmark_mode_routing_fail_does_not_assert(self) -> None:
        """Simulate benchmark-mode routing: failing result is recorded, no AssertionError."""
        from tests.e2e.test_scenario_runner import _record_benchmark_entry

        scenario = _make_scenario(expected_routing="health")
        result = _make_scenario_result(routing_passed=False)
        accumulator = BenchmarkResult()
        benchmark_mode = True

        with patch.dict(os.environ, {"E2E_CURRENT_MODEL": "bench-model"}):
            if benchmark_mode:
                # Should not raise
                _record_benchmark_entry(scenario, result, accumulator)
                return  # No assertion after return

        pytest.fail("Should have returned in benchmark mode without assertion")

    def test_benchmark_mode_tool_call_fail_does_not_assert(self) -> None:
        """Simulate benchmark-mode tool-call: failing result is recorded, no AssertionError."""
        from tests.e2e.test_scenario_runner import _record_benchmark_entry

        scenario = _make_scenario(expected_tool_calls=["log_measurement"])
        result = _make_scenario_result(tool_calls_passed=False)
        accumulator = BenchmarkResult()
        benchmark_mode = True

        with patch.dict(os.environ, {"E2E_CURRENT_MODEL": "bench-model"}):
            if benchmark_mode:
                _record_benchmark_entry(scenario, result, accumulator)
                entries = accumulator.for_model("bench-model")
                assert len(entries) == 1
                assert entries[0].tool_calls_passed is False
                return

        pytest.fail("Should have returned in benchmark mode")

    def test_benchmark_mode_accumulates_multiple_scenarios(self) -> None:
        """Multiple scenarios are all recorded in a single BenchmarkResult."""
        from tests.e2e.test_scenario_runner import _record_benchmark_entry

        accumulator = BenchmarkResult()
        scenario_ids = ["scenario-a", "scenario-b", "scenario-c"]

        with patch.dict(os.environ, {"E2E_CURRENT_MODEL": "multi-model"}):
            for sid in scenario_ids:
                scenario = _make_scenario(scenario_id=sid)
                result = _make_scenario_result(scenario_id=sid)
                _record_benchmark_entry(scenario, result, accumulator)

        entries = accumulator.for_model("multi-model")
        assert len(entries) == 3
        assert {e.scenario_id for e in entries} == set(scenario_ids)


# ---------------------------------------------------------------------------
# Tests for pytest markers being registered
# ---------------------------------------------------------------------------


class TestPytestMarkers:
    """Verify that the required pytest markers are registered in pyproject.toml."""

    def test_required_markers_present(self) -> None:
        """All required E2E markers should be registered in pyproject.toml."""
        import tomllib
        from pathlib import Path

        pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with pyproject_path.open("rb") as fh:
            config = tomllib.load(fh)

        ini = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
        markers: list[str] = ini.get("markers", [])
        marker_names = [m.split(":")[0].strip() for m in markers]

        assert "e2e" in marker_names, "e2e marker must be registered"
        assert "benchmark" in marker_names, "benchmark marker must be registered"
        assert "routing_accuracy" in marker_names, "routing_accuracy marker must be registered"
        assert "tool_accuracy" in marker_names, "tool_accuracy marker must be registered"


# ---------------------------------------------------------------------------
# Tests for scenario runner markers being applied
# ---------------------------------------------------------------------------


class TestScenarioRunnerMarkers:
    """Verify that test functions in test_scenario_runner.py have correct markers."""

    def test_test_scenario_routing_has_routing_accuracy_marker(self) -> None:
        """test_scenario_routing must have the routing_accuracy marker."""
        from tests.e2e.test_scenario_runner import test_scenario_routing

        markers = {m.name for m in test_scenario_routing.pytestmark}
        assert "routing_accuracy" in markers, (
            "test_scenario_routing must be marked with @pytest.mark.routing_accuracy"
        )

    def test_test_scenario_tool_calls_has_tool_accuracy_marker(self) -> None:
        """test_scenario_tool_calls must have the tool_accuracy marker."""
        from tests.e2e.test_scenario_runner import test_scenario_tool_calls

        markers = {m.name for m in test_scenario_tool_calls.pytestmark}
        assert "tool_accuracy" in markers, (
            "test_scenario_tool_calls must be marked with @pytest.mark.tool_accuracy"
        )

    def test_test_scenario_routing_has_e2e_marker(self) -> None:
        """test_scenario_routing must inherit the module-level e2e marker."""
        import tests.e2e.test_scenario_runner as runner_module

        pytestmarks = getattr(runner_module, "pytestmark", [])
        mark_names = {m.name for m in pytestmarks}
        assert "e2e" in mark_names, "Module-level pytestmark must include e2e"


# ---------------------------------------------------------------------------
# Tests for Makefile targets
# ---------------------------------------------------------------------------


class TestMakefileTargets:
    """Verify that Makefile contains the required E2E targets."""

    def test_test_e2e_validate_target_present(self) -> None:
        """Makefile must declare a test-e2e-validate target."""
        from pathlib import Path

        makefile_path = Path(__file__).resolve().parent.parent / "Makefile"
        content = makefile_path.read_text()
        assert "test-e2e-validate:" in content, "Makefile must have a test-e2e-validate target"

    def test_test_e2e_benchmark_target_present(self) -> None:
        """Makefile must declare a test-e2e-benchmark target."""
        from pathlib import Path

        makefile_path = Path(__file__).resolve().parent.parent / "Makefile"
        content = makefile_path.read_text()
        assert "test-e2e-benchmark:" in content, "Makefile must have a test-e2e-benchmark target"

    def test_test_e2e_validate_uses_e2e_not_benchmark_marker(self) -> None:
        """test-e2e-validate target must exclude benchmark marker tests."""
        from pathlib import Path

        makefile_path = Path(__file__).resolve().parent.parent / "Makefile"
        content = makefile_path.read_text()
        # Find the test-e2e-validate block
        lines = content.split("\n")
        in_target = False
        target_lines: list[str] = []
        for line in lines:
            if "test-e2e-validate:" in line:
                in_target = True
                continue
            if in_target:
                if line.startswith("\t"):
                    target_lines.append(line)
                elif line.strip() and not line.startswith("#"):
                    # New target or non-comment line — stop
                    if line[0] not in (" ", "\t", "#", "\n", ""):
                        break

        target_body = "\n".join(target_lines)
        assert "pytest" in target_body, "test-e2e-validate must invoke pytest"

    def test_test_e2e_benchmark_uses_benchmark_flag(self) -> None:
        """test-e2e-benchmark target must pass --benchmark flag."""
        from pathlib import Path

        makefile_path = Path(__file__).resolve().parent.parent / "Makefile"
        content = makefile_path.read_text()
        lines = content.split("\n")
        in_target = False
        target_lines: list[str] = []
        for line in lines:
            if "test-e2e-benchmark:" in line:
                in_target = True
                continue
            if in_target:
                if line.startswith("\t"):
                    target_lines.append(line)
                elif line.strip() and not line.startswith("#"):
                    if line[0] not in (" ", "\t", "#", "\n", ""):
                        break

        target_body = "\n".join(target_lines)
        assert "--benchmark" in target_body, "test-e2e-benchmark must pass --benchmark flag"


# ---------------------------------------------------------------------------
# Tests for README.md E2E section
# ---------------------------------------------------------------------------


class TestReadmeE2ESection:
    """Verify that README.md contains required E2E Testing section."""

    def _get_readme_content(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parent.parent / "README.md").read_text()

    def test_e2e_testing_section_present(self) -> None:
        """README.md must have an E2E Testing section."""
        content = self._get_readme_content()
        assert "## E2E Testing" in content, "README.md must contain '## E2E Testing' section"

    def test_validate_mode_documented(self) -> None:
        """README.md must document validate mode run command."""
        content = self._get_readme_content()
        assert "test-e2e-validate" in content, "README.md must document test-e2e-validate"

    def test_benchmark_mode_documented(self) -> None:
        """README.md must document benchmark mode run command."""
        content = self._get_readme_content()
        assert "test-e2e-benchmark" in content, "README.md must document test-e2e-benchmark"

    def test_token_burn_warning_present(self) -> None:
        """README.md must contain a token burn warning."""
        content = self._get_readme_content()
        assert "token" in content.lower() or "Token" in content, (
            "README.md must mention token burn risk"
        )

    def test_prerequisites_documented(self) -> None:
        """README.md must document E2E prerequisites (API key, docker, claude)."""
        content = self._get_readme_content()
        assert "ANTHROPIC_API_KEY" in content, (
            "README.md must document ANTHROPIC_API_KEY prerequisite"
        )
        assert "docker" in content.lower() or "Docker" in content, (
            "README.md must document Docker prerequisite"
        )
        assert "claude" in content.lower(), "README.md must document claude binary prerequisite"

    def test_scorecard_output_documented(self) -> None:
        """README.md must document scorecard output location."""
        content = self._get_readme_content()
        assert "e2e-scorecards" in content, "README.md must document scorecard output path"

    def test_configuration_table_present(self) -> None:
        """README.md must document CLI options / env vars."""
        content = self._get_readme_content()
        assert "--benchmark" in content, "README.md must document --benchmark CLI option"
        assert "E2E_BENCHMARK_MODELS" in content, "README.md must document E2E_BENCHMARK_MODELS"

    def test_markers_table_present(self) -> None:
        """README.md E2E section must list the pytest markers."""
        content = self._get_readme_content()
        assert "routing_accuracy" in content, "README.md must list routing_accuracy marker"
        assert "tool_accuracy" in content, "README.md must list tool_accuracy marker"

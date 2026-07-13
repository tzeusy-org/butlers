"""Unit tests for the model-benchmark multi-model orchestration loop (bu-mxex1).

These verify the harness *mechanics* with stubs and a fake pool — never a live
ecosystem or real LLM calls:

- ``run_benchmark`` iterates models sequentially (no interleaving), pins before
  and unpins after each model's corpus, propagates ``E2E_CURRENT_MODEL`` to the
  scenario runner, restores it afterward, and aggregates per-``(model, scenario)``
  results (including error entries when a scenario raises).
- ``orchestrate_benchmark`` is a no-op in validate mode and merges into a shared
  accumulator in benchmark mode.
- ``partition_benchmark_items`` gates collection: the corpus validate tests run
  in validate mode, the driver runs in benchmark mode.

The module lives under ``tests/`` (not ``tests/e2e/``) so it is collected by the
default unit lane and never triggers the e2e ecosystem/API-key skip guards.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.e2e.benchmark import (
    E2E_CURRENT_MODEL_ENV,
    BenchmarkResult,
    orchestrate_benchmark,
    partition_benchmark_items,
    run_benchmark,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Lightweight stand-ins for Scenario / ScenarioResult (no e2e imports needed)
# ---------------------------------------------------------------------------


@dataclass
class _FakeScenario:
    id: str
    expected_routing: str | None = "general"
    expected_tool_calls: list[str] = field(default_factory=list)


@dataclass
class _FakeRouting:
    actual: str | None
    passed: bool


@dataclass
class _FakeToolCalls:
    passed: bool
    actual_names: list[str]


@dataclass
class _FakeScenarioResult:
    routing: _FakeRouting | None = None
    tool_calls: _FakeToolCalls | None = None
    duration_ms: int = 5
    timed_out: bool = False
    error: str | None = None


class _FakePool:
    """Records pin/unpin events by inspecting the SQL run_benchmark issues.

    ``pin_model`` fetchval's the model_catalog INSERT (model name is the 3rd
    positional arg); ``unpin_model`` fetchval's the override DELETE count query.
    Everything else (per-butler override upserts, catalog cleanup) goes through
    ``execute`` and is a no-op here.
    """

    def __init__(self, events: list) -> None:
        self.events = events

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "INSERT INTO public.model_catalog" in sql:
            self.events.append(("pin", args[2]))
            return 1  # catalog id
        if "DELETE FROM public.butler_model_overrides" in sql:
            self.events.append(("unpin",))
            return 1  # deleted-row count
        return 1

    async def execute(self, sql: str, *args: Any) -> str:
        return ""


def _stub_runner(events: list, *, raise_on: set[str] | None = None):
    """Build a run_scenario_fn that records (scenario, active-model, id) events.

    The active model is read from ``E2E_CURRENT_MODEL`` at call time, proving the
    loop propagates it before running the corpus.
    """
    raise_on = raise_on or set()

    async def _run(scenario: _FakeScenario) -> _FakeScenarioResult:
        events.append(("scenario", os.environ.get(E2E_CURRENT_MODEL_ENV), scenario.id))
        if scenario.id in raise_on:
            raise RuntimeError(f"boom:{scenario.id}")
        return _FakeScenarioResult(
            routing=_FakeRouting(actual=scenario.expected_routing, passed=True),
            tool_calls=_FakeToolCalls(passed=True, actual_names=list(scenario.expected_tool_calls)),
        )

    return _run


# ---------------------------------------------------------------------------
# run_benchmark: iteration order, pin/unpin bracketing, env propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_benchmark_iterates_models_sequentially_without_interleaving() -> None:
    events: list = []
    pool = _FakePool(events)
    scenarios = [_FakeScenario("s1"), _FakeScenario("s2")]

    await run_benchmark(
        ["A", "B"],
        pool,
        ["general", "switchboard"],
        scenarios,
        run_scenario_fn=_stub_runner(events),
    )

    assert events == [
        ("pin", "A"),
        ("scenario", "A", "s1"),
        ("scenario", "A", "s2"),
        ("unpin",),
        ("pin", "B"),
        ("scenario", "B", "s1"),
        ("scenario", "B", "s2"),
        ("unpin",),
    ]


@pytest.mark.asyncio
async def test_run_benchmark_sets_and_restores_e2e_current_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pre-existing ambient value must be restored after the run.
    monkeypatch.setenv(E2E_CURRENT_MODEL_ENV, "preexisting")
    events: list = []
    pool = _FakePool(events)

    await run_benchmark(
        ["A", "B"],
        pool,
        ["general"],
        [_FakeScenario("s1")],
        run_scenario_fn=_stub_runner(events),
    )

    # During each model's corpus the env named the active model...
    per_scenario_models = [e[1] for e in events if e[0] == "scenario"]
    assert per_scenario_models == ["A", "B"]
    # ...and the ambient value is restored afterward.
    assert os.environ[E2E_CURRENT_MODEL_ENV] == "preexisting"


@pytest.mark.asyncio
async def test_run_benchmark_removes_env_when_unset_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(E2E_CURRENT_MODEL_ENV, raising=False)
    events: list = []
    pool = _FakePool(events)

    await run_benchmark(
        ["only"],
        pool,
        ["general"],
        [_FakeScenario("s1")],
        run_scenario_fn=_stub_runner(events),
    )

    # No pre-existing value → the loop must leave the variable unset, not "only".
    assert E2E_CURRENT_MODEL_ENV not in os.environ


@pytest.mark.asyncio
async def test_run_benchmark_aggregates_results_per_model_scenario() -> None:
    events: list = []
    pool = _FakePool(events)
    scenarios = [
        _FakeScenario("s1", expected_routing="general", expected_tool_calls=["t"]),
        _FakeScenario("s2", expected_routing="health", expected_tool_calls=[]),
    ]

    result = await run_benchmark(
        ["A", "B"], pool, ["general"], scenarios, run_scenario_fn=_stub_runner(events)
    )

    assert result.all_models() == ["A", "B"]
    assert [e.scenario_id for e in result.for_model("A")] == ["s1", "s2"]
    summary = result.summary()
    # Every scenario passed routing in the stub.
    assert summary["A"]["routing_accuracy"] == 1.0
    assert summary["A"]["total_scenarios"] == 2
    # Only s1 has expected tool calls, and it passed.
    assert summary["A"]["tool_calls_total"] == 1
    assert summary["A"]["tool_calls_passed"] == 1


@pytest.mark.asyncio
async def test_run_benchmark_records_error_entry_and_continues() -> None:
    events: list = []
    pool = _FakePool(events)
    scenarios = [_FakeScenario("boom"), _FakeScenario("ok")]

    result = await run_benchmark(
        ["A"],
        pool,
        ["general"],
        scenarios,
        run_scenario_fn=_stub_runner(events, raise_on={"boom"}),
    )

    entries = {e.scenario_id: e for e in result.for_model("A")}
    assert entries["boom"].error == "boom:boom"
    assert entries["boom"].routing_passed is False
    # The loop continued to the next scenario after the failure...
    assert "ok" in entries
    # ...and still unpinned the model despite the raise.
    assert ("unpin",) in events


@pytest.mark.asyncio
async def test_run_benchmark_unpins_even_when_pin_or_scenario_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scenario raising must not skip the finally-block unpin."""
    monkeypatch.delenv(E2E_CURRENT_MODEL_ENV, raising=False)
    events: list = []
    pool = _FakePool(events)

    await run_benchmark(
        ["A"],
        pool,
        ["general"],
        [_FakeScenario("boom")],
        run_scenario_fn=_stub_runner(events, raise_on={"boom"}),
    )

    assert ("pin", "A") in events
    assert ("unpin",) in events
    # env cleaned up even though the scenario raised
    assert E2E_CURRENT_MODEL_ENV not in os.environ


# ---------------------------------------------------------------------------
# orchestrate_benchmark: mode gating + accumulator merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrate_benchmark_validate_mode_is_noop() -> None:
    events: list = []
    pool = _FakePool(events)

    async def _should_not_run(_scenario: _FakeScenario) -> _FakeScenarioResult:
        raise AssertionError("run_scenario_fn must not be called in validate mode")

    out = await orchestrate_benchmark(
        benchmark_mode=False,
        models=["A"],
        pool=pool,
        butler_names=["general"],
        scenarios=[_FakeScenario("s1")],
        run_scenario_fn=_should_not_run,
    )

    assert out is None
    assert events == []  # no pinning, no scenarios


@pytest.mark.asyncio
async def test_orchestrate_benchmark_merges_into_accumulator() -> None:
    events: list = []
    pool = _FakePool(events)
    accumulator = BenchmarkResult()

    returned = await orchestrate_benchmark(
        benchmark_mode=True,
        models=["A", "B"],
        pool=pool,
        butler_names=["general"],
        scenarios=[_FakeScenario("s1")],
        run_scenario_fn=_stub_runner(events),
        accumulator=accumulator,
    )

    # The provided accumulator is populated and returned.
    assert returned is accumulator
    assert accumulator.all_models() == ["A", "B"]
    assert len(accumulator.all_entries()) == 2  # 2 models × 1 scenario


@pytest.mark.asyncio
async def test_orchestrate_benchmark_returns_fresh_result_without_accumulator() -> None:
    events: list = []
    pool = _FakePool(events)

    returned = await orchestrate_benchmark(
        benchmark_mode=True,
        models=["A"],
        pool=pool,
        butler_names=["general"],
        scenarios=[_FakeScenario("s1")],
        run_scenario_fn=_stub_runner(events),
    )

    assert isinstance(returned, BenchmarkResult)
    assert returned.all_models() == ["A"]


@pytest.mark.asyncio
async def test_orchestrate_benchmark_empty_models_raises() -> None:
    pool = _FakePool([])
    with pytest.raises(ValueError, match="no models"):
        await orchestrate_benchmark(
            benchmark_mode=True,
            models=[],
            pool=pool,
            butler_names=["general"],
            scenarios=[_FakeScenario("s1")],
            run_scenario_fn=_stub_runner([]),
        )


# ---------------------------------------------------------------------------
# partition_benchmark_items: collection gating decision
# ---------------------------------------------------------------------------


class _FakeItem:
    def __init__(self, *markers: str) -> None:
        self._markers = set(markers)

    def get_closest_marker(self, name: str) -> object | None:
        return object() if name in self._markers else None


def test_partition_benchmark_mode_deselects_corpus_keeps_driver() -> None:
    routing = _FakeItem("routing_accuracy")
    tool = _FakeItem("tool_accuracy")
    driver = _FakeItem("benchmark")
    other = _FakeItem("e2e")

    selected, deselected = partition_benchmark_items(
        [routing, tool, driver, other], is_benchmark=True
    )

    assert set(deselected) == {routing, tool}
    assert driver in selected
    assert other in selected


def test_partition_validate_mode_deselects_driver_keeps_corpus() -> None:
    routing = _FakeItem("routing_accuracy")
    driver = _FakeItem("benchmark")

    selected, deselected = partition_benchmark_items([routing, driver], is_benchmark=False)

    assert deselected == [driver]
    assert selected == [routing]

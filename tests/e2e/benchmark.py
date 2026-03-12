"""Model benchmark harness for E2E evaluation.

Provides model pinning/unpinning via the catalog override system and a
benchmark runner loop that iterates over a list of models, running the full
scenario corpus for each.

Usage in benchmark mode:
    pytest tests/e2e/ --benchmark --benchmark-models=claude-sonnet-4-5,gpt-4o

Or via environment variable:
    E2E_BENCHMARK_MODELS=claude-sonnet-4-5,gpt-4o pytest tests/e2e/ --benchmark

Key design decisions:
- Model pinning inserts a catalog entry + per-butler overrides at priority=999
  (highest) so resolve_model() always returns the pinned model.
- Override rows are tagged source='e2e-benchmark' for crash-safe identification.
- unpin_model() deletes all priority=999 rows tagged source='e2e-benchmark'.
- Benchmark runner uses try/finally to guarantee cleanup on crash.
- Results are accumulated in BenchmarkResult keyed by (model, scenario_id).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# Tag used to identify benchmark-inserted override rows for crash-safe cleanup.
_BENCHMARK_SOURCE = "e2e-benchmark"

# Priority value used for benchmark overrides — must exceed all production values.
_BENCHMARK_PRIORITY = 999


# ---------------------------------------------------------------------------
# Model pinning / unpinning
# ---------------------------------------------------------------------------


async def pin_model(
    pool: asyncpg.Pool,
    model_name: str,
    butler_names: list[str],
    *,
    runtime_type: str = "claude",
    complexity_tier: str = "medium",
) -> str:
    """Pin a model for all specified butlers by inserting catalog overrides.

    Inserts a ``shared.model_catalog`` entry for ``model_name`` (idempotent via
    ON CONFLICT) and upserts a ``shared.butler_model_overrides`` row for every
    butler in ``butler_names`` with ``priority=999`` and
    ``source='e2e-benchmark'``.

    After this call, ``resolve_model(pool, butler_name, complexity_tier)`` will
    return ``model_name`` for all listed butlers because priority=999 wins over
    any existing catalog entries.

    Parameters
    ----------
    pool:
        asyncpg pool connected to the butlers database.
    model_name:
        The model ID string to pin (e.g. ``"claude-sonnet-4-5-20250514"``).
    butler_names:
        All butler names to pin the model for (typically all roster butlers
        including switchboard).
    runtime_type:
        Runtime adapter to use for the catalog entry (e.g. ``"claude"``,
        ``"codex"``).  Defaults to ``"claude"``.
    complexity_tier:
        Complexity tier for the catalog entry.  Defaults to ``"medium"``.
        The override row does not specify a tier (NULL) so it matches whatever
        tier the butler requests.

    Returns
    -------
    str
        The UUID of the inserted/existing catalog entry.

    Notes
    -----
    The ``source='e2e-benchmark'`` tag on override rows allows manual cleanup
    after a crash:

        DELETE FROM shared.butler_model_overrides
        WHERE source = 'e2e-benchmark';
    """
    alias = f"e2e-benchmark-{model_name}"

    # Insert (or return existing) catalog entry for this model.
    catalog_id = await pool.fetchval(
        """
        INSERT INTO shared.model_catalog
            (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
        VALUES ($1, $2, $3, '[]'::jsonb, $4, true, $5)
        ON CONFLICT (alias) DO UPDATE
            SET runtime_type = EXCLUDED.runtime_type,
                model_id     = EXCLUDED.model_id,
                updated_at   = now()
        RETURNING id
        """,
        alias,
        runtime_type,
        model_name,
        complexity_tier,
        _BENCHMARK_PRIORITY,
    )

    logger.info(
        "[benchmark] Pinned model %r (catalog_id=%s, tier=%s) for %d butlers",
        model_name,
        catalog_id,
        complexity_tier,
        len(butler_names),
    )

    # Upsert one override row per butler at priority=999.
    for butler_name in butler_names:
        await pool.execute(
            """
            INSERT INTO shared.butler_model_overrides
                (butler_name, catalog_entry_id, enabled, priority, source)
            VALUES ($1, $2, true, $3, $4)
            ON CONFLICT (butler_name, catalog_entry_id) DO UPDATE
                SET enabled  = EXCLUDED.enabled,
                    priority = EXCLUDED.priority,
                    source   = EXCLUDED.source
            """,
            butler_name,
            catalog_id,
            _BENCHMARK_PRIORITY,
            _BENCHMARK_SOURCE,
        )
        logger.debug(
            "[benchmark] Override row upserted for butler=%r model=%r priority=%d",
            butler_name,
            model_name,
            _BENCHMARK_PRIORITY,
        )

    return str(catalog_id)


async def unpin_model(pool: asyncpg.Pool) -> int:
    """Remove all benchmark override rows and the associated catalog entry.

    Deletes every row in ``shared.butler_model_overrides`` where
    ``source='e2e-benchmark'`` and ``priority=999``.  Also removes the
    corresponding ``shared.model_catalog`` entries whose alias starts with
    ``"e2e-benchmark-"``.

    Returns
    -------
    int
        Number of override rows deleted.

    Notes
    -----
    This function is idempotent — calling it when no benchmark overrides exist
    is a no-op.  Safe to call in ``finally`` blocks after a crash.
    """
    # Delete override rows first (FK references catalog entry).
    deleted = await pool.fetchval(
        """
        WITH deleted AS (
            DELETE FROM shared.butler_model_overrides
            WHERE source = $1
              AND priority = $2
            RETURNING id
        )
        SELECT count(*) FROM deleted
        """,
        _BENCHMARK_SOURCE,
        _BENCHMARK_PRIORITY,
    )

    # Now remove orphaned benchmark catalog entries.
    await pool.execute(
        """
        DELETE FROM shared.model_catalog
        WHERE alias LIKE 'e2e-benchmark-%'
          AND priority = $1
        """,
        _BENCHMARK_PRIORITY,
    )

    count = int(deleted or 0)
    logger.info("[benchmark] unpin_model: deleted %d override rows", count)
    return count


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkEntry:
    """Per-scenario result for a single model run.

    Attributes:
        model: Model ID that was pinned for this run.
        scenario_id: Scenario identifier.
        routing_passed: Whether routing matched expected_routing.
        routing_expected: Expected butler name (or None for multi-target).
        routing_actual: Actual triage_target returned by ingest_v1.
        tool_calls_passed: Whether expected_tool_calls were all called.
        tool_calls_expected: Expected tool names.
        tool_calls_actual: Actual tool names called.
        input_tokens: Input token count from the session row.
        output_tokens: Output token count from the session row.
        duration_ms: Wall-clock duration for the scenario in milliseconds.
        timed_out: Whether the scenario timed out.
        error: Error string if the scenario raised an exception.
    """

    model: str
    scenario_id: str
    routing_passed: bool
    routing_expected: str | None
    routing_actual: str | None
    tool_calls_passed: bool
    tool_calls_expected: list[str]
    tool_calls_actual: list[str]
    input_tokens: int
    output_tokens: int
    duration_ms: int
    timed_out: bool
    error: str | None = None


@dataclass
class BenchmarkResult:
    """Accumulator for all benchmark scenario results across all models.

    Results are keyed by ``(model, scenario_id)`` for O(1) lookup.

    Usage:
        results = BenchmarkResult()
        results.record(entry)

        entries = results.for_model("claude-sonnet-4-5-20250514")
        summary = results.summary()
    """

    _entries: dict[tuple[str, str], BenchmarkEntry] = field(default_factory=dict, repr=False)

    def record(self, entry: BenchmarkEntry) -> None:
        """Add a BenchmarkEntry keyed by (model, scenario_id)."""
        self._entries[(entry.model, entry.scenario_id)] = entry

    def for_model(self, model: str) -> list[BenchmarkEntry]:
        """Return all entries for a given model, sorted by scenario_id."""
        return sorted(
            (e for e in self._entries.values() if e.model == model),
            key=lambda e: e.scenario_id,
        )

    def all_models(self) -> list[str]:
        """Return sorted list of unique model names seen so far."""
        return sorted({e.model for e in self._entries.values()})

    def all_entries(self) -> list[BenchmarkEntry]:
        """Return all entries sorted by (model, scenario_id)."""
        return sorted(self._entries.values(), key=lambda e: (e.model, e.scenario_id))

    def summary(self) -> dict[str, Any]:
        """Return a summary dict keyed by model with accuracy and token totals.

        Returns
        -------
        dict
            Mapping of model → dict with keys:
                - routing_accuracy: float (0.0–1.0)
                - tool_call_accuracy: float (0.0–1.0)
                - total_scenarios: int
                - routing_passed: int
                - routing_total: int
                - tool_calls_passed: int
                - tool_calls_total: int
                - input_tokens: int
                - output_tokens: int
                - timed_out: int
        """
        result: dict[str, Any] = {}
        for model in self.all_models():
            entries = self.for_model(model)
            routing_total = sum(1 for e in entries if e.routing_expected is not None)
            routing_passed = sum(
                1 for e in entries if e.routing_expected is not None and e.routing_passed
            )
            tc_total = sum(1 for e in entries if e.tool_calls_expected)
            tc_passed = sum(1 for e in entries if e.tool_calls_expected and e.tool_calls_passed)
            result[model] = {
                "routing_accuracy": routing_passed / routing_total if routing_total else 0.0,
                "tool_call_accuracy": tc_passed / tc_total if tc_total else 0.0,
                "total_scenarios": len(entries),
                "routing_passed": routing_passed,
                "routing_total": routing_total,
                "tool_calls_passed": tc_passed,
                "tool_calls_total": tc_total,
                "input_tokens": sum(e.input_tokens for e in entries),
                "output_tokens": sum(e.output_tokens for e in entries),
                "timed_out": sum(1 for e in entries if e.timed_out),
            }
        return result


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


async def run_benchmark(
    models: list[str],
    pool: asyncpg.Pool,
    butler_names: list[str],
    scenarios: list[Any],
    *,
    run_scenario_fn: Any,
    runtime_type: str = "claude",
) -> BenchmarkResult:
    """Run the full scenario corpus for each model sequentially.

    For each model in ``models``:
    1. Pin the model (insert catalog entry + overrides at priority=999).
    2. Run all scenarios in ``scenarios`` sequentially (no interleaving).
    3. Accumulate results in a ``BenchmarkResult``.
    4. Unpin the model in a ``try/finally`` block.

    Parameters
    ----------
    models:
        List of model ID strings to benchmark.
    pool:
        asyncpg pool for inserting/deleting catalog overrides.
    butler_names:
        All butler names to pin the model for (should match roster).
    scenarios:
        List of ``Scenario`` objects from ``tests.e2e.scenarios``.
    run_scenario_fn:
        Async callable with signature
        ``(scenario, butler_ecosystem, cost_tracker, *, envelope_override)``
        returning a ``ScenarioResult``.  Typically ``_run_scenario`` from
        ``test_scenario_runner``.
    runtime_type:
        Runtime adapter for the catalog entry.  Defaults to ``"claude"``.

    Returns
    -------
    BenchmarkResult
        Accumulated per-scenario results for all models.

    Notes
    -----
    Scenarios are run **sequentially** within each model run and models are
    iterated sequentially — there is no interleaving across models.

    If a crash occurs mid-run, the ``try/finally`` block ensures the override
    rows are removed.  Any remaining rows can be found by:

        SELECT * FROM shared.butler_model_overrides WHERE source = 'e2e-benchmark';
    """
    results = BenchmarkResult()

    for model in models:
        logger.info("[benchmark] Starting model run: %r (%d scenarios)", model, len(scenarios))
        t_model_start = time.monotonic()

        try:
            # Pin model for all butlers before running any scenarios.
            await pin_model(pool, model, butler_names, runtime_type=runtime_type)

            # Run all scenarios sequentially for this model.
            for scenario in scenarios:
                logger.debug(
                    "[benchmark] Running scenario %r with model %r",
                    scenario.id,
                    model,
                )
                t0 = time.monotonic()

                try:
                    scenario_result = await run_scenario_fn(scenario)
                except Exception as exc:
                    logger.error(
                        "[benchmark] Scenario %r raised with model %r: %s",
                        scenario.id,
                        model,
                        exc,
                    )
                    entry = BenchmarkEntry(
                        model=model,
                        scenario_id=scenario.id,
                        routing_passed=False,
                        routing_expected=scenario.expected_routing,
                        routing_actual=None,
                        tool_calls_passed=False,
                        tool_calls_expected=scenario.expected_tool_calls,
                        tool_calls_actual=[],
                        input_tokens=0,
                        output_tokens=0,
                        duration_ms=int((time.monotonic() - t0) * 1000),
                        timed_out=False,
                        error=str(exc),
                    )
                    results.record(entry)
                    continue

                # Extract routing result.
                routing_passed = False
                routing_expected = scenario.expected_routing
                routing_actual: str | None = None
                if scenario_result.routing is not None:
                    routing_actual = scenario_result.routing.actual
                    routing_passed = scenario_result.routing.passed

                # Extract tool-call result.
                tc_passed = True
                tc_expected: list[str] = scenario.expected_tool_calls
                tc_actual: list[str] = []
                if scenario_result.tool_calls is not None:
                    tc_passed = scenario_result.tool_calls.passed
                    tc_actual = scenario_result.tool_calls.actual_names

                entry = BenchmarkEntry(
                    model=model,
                    scenario_id=scenario.id,
                    routing_passed=routing_passed,
                    routing_expected=routing_expected,
                    routing_actual=routing_actual,
                    tool_calls_passed=tc_passed,
                    tool_calls_expected=tc_expected,
                    tool_calls_actual=tc_actual,
                    input_tokens=0,
                    output_tokens=0,
                    duration_ms=scenario_result.duration_ms,
                    timed_out=scenario_result.timed_out,
                    error=scenario_result.error,
                )
                results.record(entry)

        finally:
            # Always unpin, even if a scenario raised an exception.
            removed = await unpin_model(pool)
            t_model_elapsed = int((time.monotonic() - t_model_start) * 1000)
            logger.info(
                "[benchmark] Finished model run: %r (elapsed=%dms, overrides_removed=%d)",
                model,
                t_model_elapsed,
                removed,
            )

    return results


# ---------------------------------------------------------------------------
# CLI option helpers (called from conftest.py)
# ---------------------------------------------------------------------------


def resolve_benchmark_models(
    cli_value: str | None,
    *,
    env_var: str = "E2E_BENCHMARK_MODELS",
) -> list[str] | None:
    """Resolve the benchmark model list from CLI or environment variable.

    CLI value takes precedence over the environment variable.

    Parameters
    ----------
    cli_value:
        Raw string from ``--benchmark-models`` CLI option (comma-separated),
        or ``None`` if not provided.
    env_var:
        Environment variable name to fall back to.  Defaults to
        ``"E2E_BENCHMARK_MODELS"``.

    Returns
    -------
    list[str] | None
        Parsed list of model IDs (whitespace-stripped, empty strings removed),
        or ``None`` if neither CLI nor env var provided a value.
    """
    import os

    raw = cli_value or os.environ.get(env_var)
    if not raw:
        return None
    return [m.strip() for m in raw.split(",") if m.strip()]

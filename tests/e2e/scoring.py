"""Scoring engine for E2E benchmark results.

Consumes ``BenchmarkResult`` data from ``tests.e2e.benchmark`` and produces
structured scorecards for routing accuracy, tool-call accuracy, and cost.

Key design decisions:
- All accuracy values are percentages (0.0–100.0), not fractions.
- Per-tag breakdown uses the scenario's ``tags`` list from ``scenarios.py``.
- Confusion matrix keys are (expected_butler, actual_butler) tuples for misroutes.
- CostTracker is extended to key costs by (model, scenario_id) and load pricing
  from ``pricing.toml`` so that benchmark runs use the correct per-model rates.
- Pricing falls back to zero-cost if model is not in the pricing file.
"""

from __future__ import annotations

import logging
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tests.e2e.benchmark import BenchmarkEntry, BenchmarkResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table
# ---------------------------------------------------------------------------

# Path to the project-level pricing.toml (relative to repo root)
_PRICING_TOML_DEFAULT = Path(__file__).resolve().parent.parent.parent / "pricing.toml"


def load_pricing(pricing_path: Path | None = None) -> dict[str, dict[str, float]]:
    """Load per-model pricing from pricing.toml.

    Parameters
    ----------
    pricing_path:
        Override path to the pricing TOML file.  Defaults to
        ``<repo_root>/pricing.toml``.

    Returns
    -------
    dict
        Mapping of ``model_id -> {input_price_per_token, output_price_per_token}``.
        Returns an empty dict if the file does not exist or cannot be parsed.
    """
    path = pricing_path or _PRICING_TOML_DEFAULT
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return data.get("models", {})
    except FileNotFoundError:
        logger.debug("pricing.toml not found at %s — using zero-cost pricing", path)
        return {}
    except Exception:
        logger.warning("Failed to load pricing.toml from %s", path, exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Extended CostTracker (keyed by model + scenario_id)
# ---------------------------------------------------------------------------


@dataclass
class CostEntry:
    """Token usage and cost for a single (model, scenario_id) pair.

    Attributes:
        model: Model ID string.
        scenario_id: Scenario identifier.
        input_tokens: Input tokens consumed.
        output_tokens: Output tokens consumed.
        input_cost_usd: Estimated input cost in USD.
        output_cost_usd: Estimated output cost in USD.
    """

    model: str
    scenario_id: str
    input_tokens: int
    output_tokens: int
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_cost_usd(self) -> float:
        return self.input_cost_usd + self.output_cost_usd


@dataclass
class BenchmarkCostTracker:
    """Cost tracker keyed by (model, scenario_id) with configurable per-model pricing.

    This extends the simpler ``CostTracker`` in ``conftest.py`` with:
    - Per-(model, scenario_id) granularity for scorecard breakdowns.
    - Configurable pricing loaded from ``pricing.toml``.
    - Convenience aggregation helpers for reporting.

    Usage::

        pricing = load_pricing()
        tracker = BenchmarkCostTracker(pricing=pricing)
        tracker.record("claude-haiku", "telegram-weight-log", 1000, 200)

        by_model = tracker.for_model("claude-haiku")
        total = tracker.total_for_model("claude-haiku")
    """

    pricing: dict[str, dict[str, float]] = field(default_factory=dict)
    _entries: dict[tuple[str, str], CostEntry] = field(default_factory=dict, repr=False)

    def _compute_cost(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> tuple[float, float]:
        """Return (input_cost_usd, output_cost_usd) for the given token counts."""
        model_pricing = self.pricing.get(model, {})
        input_price = model_pricing.get("input_price_per_token", 0.0)
        output_price = model_pricing.get("output_price_per_token", 0.0)
        return (input_tokens * input_price, output_tokens * output_price)

    def record(
        self,
        model: str,
        scenario_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record token usage for a single (model, scenario_id) pair.

        If a record already exists for this pair, the tokens are **replaced**
        (not accumulated), since each benchmark run produces exactly one result
        per (model, scenario_id).
        """
        input_cost, output_cost = self._compute_cost(model, input_tokens, output_tokens)
        self._entries[(model, scenario_id)] = CostEntry(
            model=model,
            scenario_id=scenario_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
        )

    def for_model(self, model: str) -> list[CostEntry]:
        """Return all cost entries for a given model, sorted by scenario_id."""
        return sorted(
            (e for e in self._entries.values() if e.model == model),
            key=lambda e: e.scenario_id,
        )

    def total_for_model(self, model: str) -> dict[str, Any]:
        """Return aggregated cost totals for a model.

        Returns
        -------
        dict
            Keys: ``input_tokens``, ``output_tokens``, ``total_tokens``,
            ``input_cost_usd``, ``output_cost_usd``, ``total_cost_usd``.
        """
        entries = self.for_model(model)
        return {
            "input_tokens": sum(e.input_tokens for e in entries),
            "output_tokens": sum(e.output_tokens for e in entries),
            "total_tokens": sum(e.total_tokens for e in entries),
            "input_cost_usd": sum(e.input_cost_usd for e in entries),
            "output_cost_usd": sum(e.output_cost_usd for e in entries),
            "total_cost_usd": sum(e.total_cost_usd for e in entries),
        }

    def populate_from_results(self, results: BenchmarkResult) -> None:
        """Populate cost entries from a BenchmarkResult accumulator.

        Iterates all entries in the BenchmarkResult and records token usage
        for each (model, scenario_id) pair.

        Parameters
        ----------
        results:
            Fully-populated BenchmarkResult from a completed benchmark run.
        """
        for entry in results.all_entries():
            self.record(
                entry.model,
                entry.scenario_id,
                entry.input_tokens,
                entry.output_tokens,
            )


# ---------------------------------------------------------------------------
# Routing scorecard
# ---------------------------------------------------------------------------


@dataclass
class RoutingTagBreakdown:
    """Routing accuracy breakdown for a single tag.

    Attributes:
        tag: The scenario tag (e.g. ``"email"``, ``"telegram"``, ``"health"``).
        total: Number of scenarios with this tag and ``expected_routing`` set.
        passed: Number of correctly routed scenarios with this tag.
        accuracy_pct: Accuracy as a percentage (0.0–100.0).
    """

    tag: str
    total: int
    passed: int
    accuracy_pct: float


@dataclass
class RoutingScorecard:
    """Routing accuracy scorecard for a single model.

    Attributes:
        model: Model ID.
        total_scenarios: Number of scenarios with ``expected_routing`` set.
        passed: Number of correctly routed scenarios.
        accuracy_pct: Overall accuracy as a percentage (0.0–100.0).
        tag_breakdown: Per-tag accuracy breakdown.
        confusion_matrix: Mapping of ``(expected_butler, actual_butler)`` to
            misroute count.  Only contains entries for actual misroutes.
    """

    model: str
    total_scenarios: int
    passed: int
    accuracy_pct: float
    tag_breakdown: list[RoutingTagBreakdown]
    confusion_matrix: dict[tuple[str, str], int]


def compute_routing_scorecard(
    results: BenchmarkResult,
    model: str,
    scenario_tags: dict[str, list[str]] | None = None,
) -> RoutingScorecard:
    """Compute a RoutingScorecard for a single model.

    Parameters
    ----------
    results:
        Fully-populated BenchmarkResult.
    model:
        The model ID to compute the scorecard for.
    scenario_tags:
        Optional mapping of ``scenario_id -> [tags]`` for per-tag breakdown.
        When ``None``, tags are not resolved and the tag breakdown will be empty.
        Pass ``{s.id: s.tags for s in ALL_SCENARIOS}`` to populate this.

    Returns
    -------
    RoutingScorecard
        Computed scorecard with per-tag breakdown and confusion matrix.
    """
    entries = results.for_model(model)

    # Overall accuracy — only count scenarios with expected_routing set
    routing_entries = [e for e in entries if e.routing_expected is not None]
    total = len(routing_entries)
    passed = sum(1 for e in routing_entries if e.routing_passed)
    accuracy_pct = (passed / total * 100.0) if total > 0 else 0.0

    # Confusion matrix for misroutes
    confusion: dict[tuple[str, str], int] = defaultdict(int)
    for entry in routing_entries:
        if not entry.routing_passed:
            expected = entry.routing_expected or "unknown"
            actual = entry.routing_actual or "none"
            confusion[(expected, actual)] += 1

    # Per-tag breakdown
    tag_breakdown: list[RoutingTagBreakdown] = []
    if scenario_tags:
        # Collect all unique tags from the entries' scenarios
        all_tags: set[str] = set()
        for sid, tags in scenario_tags.items():
            all_tags.update(tags)

        for tag in sorted(all_tags):
            # Scenarios with this tag that have expected_routing
            tag_entries = [
                e for e in routing_entries if tag in (scenario_tags.get(e.scenario_id) or [])
            ]
            if not tag_entries:
                continue
            tag_total = len(tag_entries)
            tag_passed = sum(1 for e in tag_entries if e.routing_passed)
            tag_accuracy = (tag_passed / tag_total * 100.0) if tag_total > 0 else 0.0
            tag_breakdown.append(
                RoutingTagBreakdown(
                    tag=tag,
                    total=tag_total,
                    passed=tag_passed,
                    accuracy_pct=tag_accuracy,
                )
            )

    return RoutingScorecard(
        model=model,
        total_scenarios=total,
        passed=passed,
        accuracy_pct=accuracy_pct,
        tag_breakdown=tag_breakdown,
        confusion_matrix=dict(confusion),
    )


# ---------------------------------------------------------------------------
# Tool-call scorecard
# ---------------------------------------------------------------------------


@dataclass
class ToolCallButlerBreakdown:
    """Tool-call accuracy breakdown for a single target butler.

    Attributes:
        butler: Target butler name (derived from ``routing_expected`` of scenarios
            that have ``expected_tool_calls``).
        total: Number of tool-call scenarios targeting this butler.
        passed: Number of passing tool-call scenarios.
        accuracy_pct: Accuracy as a percentage (0.0–100.0).
    """

    butler: str
    total: int
    passed: int
    accuracy_pct: float


@dataclass
class ToolCallFailDetail:
    """Detail for a failing tool-call scenario.

    Attributes:
        scenario_id: Scenario identifier.
        expected: Expected tool names.
        actual: Actual tool names called.
        missing: Tools that were expected but not called.
    """

    scenario_id: str
    expected: list[str]
    actual: list[str]
    missing: list[str]


@dataclass
class ToolCallScorecard:
    """Tool-call accuracy scorecard for a single model.

    Attributes:
        model: Model ID.
        total_scenarios: Number of scenarios with ``expected_tool_calls`` set.
        passed: Number of scenarios where all expected tools were called.
        accuracy_pct: Overall accuracy as a percentage (0.0–100.0).
        butler_breakdown: Per-butler accuracy breakdown.
        fail_details: List of failing scenarios with missing tool details.
    """

    model: str
    total_scenarios: int
    passed: int
    accuracy_pct: float
    butler_breakdown: list[ToolCallButlerBreakdown]
    fail_details: list[ToolCallFailDetail]


def compute_tool_call_scorecard(
    results: BenchmarkResult,
    model: str,
    scenario_routing: dict[str, str | None] | None = None,
) -> ToolCallScorecard:
    """Compute a ToolCallScorecard for a single model.

    Parameters
    ----------
    results:
        Fully-populated BenchmarkResult.
    model:
        The model ID to compute the scorecard for.
    scenario_routing:
        Optional mapping of ``scenario_id -> expected_routing`` (butler name)
        used to compute per-butler breakdown.  When ``None``, the breakdown
        uses ``entry.routing_expected`` from the BenchmarkEntry.

    Returns
    -------
    ToolCallScorecard
        Computed scorecard with per-butler breakdown and failure details.
    """
    entries = results.for_model(model)

    # Only entries with expected_tool_calls count toward tool-call accuracy
    tc_entries = [e for e in entries if e.tool_calls_expected]
    total = len(tc_entries)
    passed = sum(1 for e in tc_entries if e.tool_calls_passed)
    accuracy_pct = (passed / total * 100.0) if total > 0 else 0.0

    # Per-butler breakdown — group by routing_expected (or override)
    butler_totals: dict[str, list[BenchmarkEntry]] = defaultdict(list)
    for entry in tc_entries:
        butler = (
            (scenario_routing.get(entry.scenario_id) if scenario_routing else None)
            or entry.routing_expected
            or "unknown"
        )
        butler_totals[butler].append(entry)

    butler_breakdown: list[ToolCallButlerBreakdown] = []
    for butler_name in sorted(butler_totals.keys()):
        butler_entries = butler_totals[butler_name]
        b_total = len(butler_entries)
        b_passed = sum(1 for e in butler_entries if e.tool_calls_passed)
        b_accuracy = (b_passed / b_total * 100.0) if b_total > 0 else 0.0
        butler_breakdown.append(
            ToolCallButlerBreakdown(
                butler=butler_name,
                total=b_total,
                passed=b_passed,
                accuracy_pct=b_accuracy,
            )
        )

    # Failure details
    fail_details: list[ToolCallFailDetail] = []
    for entry in tc_entries:
        if not entry.tool_calls_passed:
            missing = [t for t in entry.tool_calls_expected if t not in entry.tool_calls_actual]
            fail_details.append(
                ToolCallFailDetail(
                    scenario_id=entry.scenario_id,
                    expected=entry.tool_calls_expected,
                    actual=entry.tool_calls_actual,
                    missing=missing,
                )
            )

    return ToolCallScorecard(
        model=model,
        total_scenarios=total,
        passed=passed,
        accuracy_pct=accuracy_pct,
        butler_breakdown=butler_breakdown,
        fail_details=fail_details,
    )


# ---------------------------------------------------------------------------
# Combined scorecard bundle
# ---------------------------------------------------------------------------


@dataclass
class ModelScorecard:
    """Bundle of all scorecards for a single model.

    Attributes:
        model: Model ID.
        routing: Routing scorecard.
        tool_calls: Tool-call scorecard.
        cost: Cost totals for this model (from BenchmarkCostTracker).
    """

    model: str
    routing: RoutingScorecard
    tool_calls: ToolCallScorecard
    cost: dict[str, Any]


def compute_all_scorecards(
    results: BenchmarkResult,
    *,
    scenario_tags: dict[str, list[str]] | None = None,
    scenario_routing: dict[str, str | None] | None = None,
    pricing: dict[str, dict[str, float]] | None = None,
) -> list[ModelScorecard]:
    """Compute full scorecards for all models in the BenchmarkResult.

    Parameters
    ----------
    results:
        Fully-populated BenchmarkResult.
    scenario_tags:
        Mapping of ``scenario_id -> [tags]`` for per-tag routing breakdown.
        Typically ``{s.id: s.tags for s in ALL_SCENARIOS}``.
    scenario_routing:
        Mapping of ``scenario_id -> expected_routing`` for per-butler tool-call
        breakdown.  Typically ``{s.id: s.expected_routing for s in ALL_SCENARIOS}``.
    pricing:
        Per-model pricing from ``load_pricing()``.  When ``None``, zero cost.

    Returns
    -------
    list[ModelScorecard]
        One scorecard bundle per model, sorted by routing accuracy descending.
    """
    cost_tracker = BenchmarkCostTracker(pricing=pricing or {})
    cost_tracker.populate_from_results(results)

    scorecards: list[ModelScorecard] = []
    for model in results.all_models():
        routing_sc = compute_routing_scorecard(results, model, scenario_tags=scenario_tags)
        tool_call_sc = compute_tool_call_scorecard(
            results, model, scenario_routing=scenario_routing
        )
        cost_totals = cost_tracker.total_for_model(model)
        scorecards.append(
            ModelScorecard(
                model=model,
                routing=routing_sc,
                tool_calls=tool_call_sc,
                cost=cost_totals,
            )
        )

    # Sort by routing accuracy descending, then model name ascending for stable sort
    scorecards.sort(key=lambda sc: (-sc.routing.accuracy_pct, sc.model))
    return scorecards

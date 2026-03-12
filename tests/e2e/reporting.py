"""Scorecard reporting: write benchmark results to .tmp/e2e-scorecards/.

After a benchmark run completes, this module writes all scorecard files to a
timestamped directory.  The directory structure is:

    .tmp/e2e-scorecards/<timestamp>/
        summary.md                       — cross-model comparison table
        <model>/
            routing-scorecard.md         — per-model routing accuracy + tags + confusion
            tool-call-scorecard.md       — per-model tool-call accuracy + butler breakdown
            cost-summary.md              — per-scenario token usage + estimated cost
            raw-results.json             — machine-readable per-scenario results

Key design decisions:
- Timestamp format is ``%Y%m%d-%H%M%S`` (e.g. ``20260312-143000``).
- Model subdirectories use the model ID as-is (filesystem-safe characters assumed).
- ``raw-results.json`` uses schema_version ``"1.0"`` for future compat.
- Output path is printed to stdout after generation.
- All Markdown tables use GitHub-flavoured Markdown pipe syntax.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tests.e2e.benchmark import BenchmarkEntry, BenchmarkResult
    from tests.e2e.scoring import ModelScorecard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = "1.0"
_SCORECARD_BASE_DIR = Path(".tmp/e2e-scorecards")


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------


def _pct(value: float) -> str:
    """Format a float as a percentage string with 1 decimal place."""
    return f"{value:.1f}%"


def _fmt_cost(usd: float) -> str:
    """Format a USD cost value for display."""
    if usd < 0.001:
        return "$0.000"
    return f"${usd:.4f}"


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a GitHub-flavoured Markdown pipe table.

    Parameters
    ----------
    headers:
        Column header names.
    rows:
        List of rows, each a list of cell strings.

    Returns
    -------
    str
        Markdown table string (no trailing newline).
    """
    col_widths = [
        max(len(h), max((len(r[i]) for r in rows), default=0)) for i, h in enumerate(headers)
    ]
    header_row = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
    separator = "| " + " | ".join("-" * col_widths[i] for i in range(len(headers))) + " |"
    data_rows = [
        "| " + " | ".join(str(row[i]).ljust(col_widths[i]) for i in range(len(headers))) + " |"
        for row in rows
    ]
    return "\n".join([header_row, separator] + data_rows)


# ---------------------------------------------------------------------------
# Per-model file writers
# ---------------------------------------------------------------------------


def _write_routing_scorecard(
    scorecard: ModelScorecard,
    model_dir: Path,
) -> None:
    """Write routing-scorecard.md for a single model."""
    sc = scorecard.routing
    lines: list[str] = [
        f"# Routing Scorecard — {sc.model}",
        "",
        "## Overall Accuracy",
        "",
        f"- **Correct routes**: {sc.passed} / {sc.total_scenarios}",
        f"- **Accuracy**: {_pct(sc.accuracy_pct)}",
        "",
    ]

    # Per-tag breakdown
    if sc.tag_breakdown:
        lines += [
            "## Per-Tag Breakdown",
            "",
            _md_table(
                ["Tag", "Correct", "Total", "Accuracy"],
                [
                    [b.tag, str(b.passed), str(b.total), _pct(b.accuracy_pct)]
                    for b in sc.tag_breakdown
                ],
            ),
            "",
        ]

    # Confusion matrix
    if sc.confusion_matrix:
        lines += [
            "## Confusion Matrix (Misroutes)",
            "",
            "_Shows: expected butler → actual butler : count_",
            "",
            _md_table(
                ["Expected", "Actual", "Count"],
                [
                    [expected, actual, str(count)]
                    for (expected, actual), count in sorted(
                        sc.confusion_matrix.items(), key=lambda kv: -kv[1]
                    )
                ],
            ),
            "",
        ]
    else:
        lines += [
            "## Confusion Matrix",
            "",
            "_No misroutes recorded._",
            "",
        ]

    path = model_dir / "routing-scorecard.md"
    path.write_text("\n".join(lines))
    logger.debug("Written %s", path)


def _write_tool_call_scorecard(
    scorecard: ModelScorecard,
    model_dir: Path,
) -> None:
    """Write tool-call-scorecard.md for a single model."""
    sc = scorecard.tool_calls
    lines: list[str] = [
        f"# Tool-Call Scorecard — {sc.model}",
        "",
        "## Overall Accuracy",
        "",
        f"- **Scenarios with expected tools**: {sc.total_scenarios}",
        f"- **Passed**: {sc.passed}",
        f"- **Accuracy**: {_pct(sc.accuracy_pct)}",
        "",
    ]

    # Per-butler breakdown
    if sc.butler_breakdown:
        lines += [
            "## Per-Butler Breakdown",
            "",
            _md_table(
                ["Butler", "Passed", "Total", "Accuracy"],
                [
                    [b.butler, str(b.passed), str(b.total), _pct(b.accuracy_pct)]
                    for b in sc.butler_breakdown
                ],
            ),
            "",
        ]

    # Failure details
    if sc.fail_details:
        lines += [
            "## Failing Scenarios",
            "",
        ]
        for detail in sc.fail_details:
            missing_str = ", ".join(detail.missing) if detail.missing else "—"
            actual_str = ", ".join(detail.actual) if detail.actual else "—"
            expected_str = ", ".join(detail.expected) if detail.expected else "—"
            lines += [
                f"### {detail.scenario_id}",
                "",
                f"- **Expected tools**: {expected_str}",
                f"- **Actual tools**: {actual_str}",
                f"- **Missing tools**: {missing_str}",
                "",
            ]
    else:
        lines += [
            "## Failing Scenarios",
            "",
            "_All tool-call scenarios passed._",
            "",
        ]

    path = model_dir / "tool-call-scorecard.md"
    path.write_text("\n".join(lines))
    logger.debug("Written %s", path)


def _write_cost_summary(
    scorecard: ModelScorecard,
    model_dir: Path,
    entries: list[BenchmarkEntry],
) -> None:
    """Write cost-summary.md for a single model."""
    cost = scorecard.cost
    model = scorecard.model

    lines: list[str] = [
        f"# Cost Summary — {model}",
        "",
        "## Totals",
        "",
        f"- **Input tokens**:  {cost['input_tokens']:,}",
        f"- **Output tokens**: {cost['output_tokens']:,}",
        f"- **Total tokens**:  {cost['total_tokens']:,}",
        f"- **Estimated cost**: {_fmt_cost(cost['total_cost_usd'])}",
        "",
    ]

    # Per-scenario breakdown
    if entries:
        lines += [
            "## Per-Scenario Breakdown",
            "",
            _md_table(
                ["Scenario", "Input Tokens", "Output Tokens", "Duration (ms)", "Pass"],
                [
                    [
                        e.scenario_id,
                        str(e.input_tokens),
                        str(e.output_tokens),
                        str(e.duration_ms),
                        "✓" if (e.routing_passed and e.tool_calls_passed) else "✗",
                    ]
                    for e in sorted(entries, key=lambda e: e.scenario_id)
                ],
            ),
            "",
        ]

    path = model_dir / "cost-summary.md"
    path.write_text("\n".join(lines))
    logger.debug("Written %s", path)


def _write_raw_results(
    model: str,
    entries: list[BenchmarkEntry],
    model_dir: Path,
) -> None:
    """Write raw-results.json for a single model.

    Schema version ``"1.0"`` — format:
    {
        "schema_version": "1.0",
        "model": "<model_id>",
        "results": [
            {
                "scenario_id": ...,
                "routing_expected": ...,
                "routing_actual": ...,
                "routing_pass": ...,
                "tool_calls_expected": [...],
                "tool_calls_actual": [...],
                "tool_calls_pass": ...,
                "input_tokens": ...,
                "output_tokens": ...,
                "duration_ms": ...
            },
            ...
        ]
    }
    """
    results_list: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda e: e.scenario_id):
        results_list.append(
            {
                "scenario_id": entry.scenario_id,
                "routing_expected": entry.routing_expected,
                "routing_actual": entry.routing_actual,
                "routing_pass": entry.routing_passed,
                "tool_calls_expected": entry.tool_calls_expected,
                "tool_calls_actual": entry.tool_calls_actual,
                "tool_calls_pass": entry.tool_calls_passed,
                "input_tokens": entry.input_tokens,
                "output_tokens": entry.output_tokens,
                "duration_ms": entry.duration_ms,
            }
        )

    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "model": model,
        "results": results_list,
    }

    path = model_dir / "raw-results.json"
    path.write_text(json.dumps(payload, indent=2))
    logger.debug("Written %s", path)


# ---------------------------------------------------------------------------
# Cross-model summary
# ---------------------------------------------------------------------------


def _write_summary(
    scorecards: list[ModelScorecard],
    output_dir: Path,
) -> None:
    """Write summary.md with cross-model comparison table.

    The table is sorted by routing accuracy descending (scorecards are expected
    to arrive already sorted from ``compute_all_scorecards``).
    """
    lines: list[str] = [
        "# E2E Benchmark Summary",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Model Comparison",
        "",
        "_Sorted by routing accuracy, descending._",
        "",
        _md_table(
            [
                "Model",
                "Routing Accuracy",
                "Tool-Call Accuracy",
                "Input Tokens",
                "Output Tokens",
                "Est. Cost",
            ],
            [
                [
                    sc.model,
                    _pct(sc.routing.accuracy_pct),
                    _pct(sc.tool_calls.accuracy_pct),
                    f"{sc.cost['input_tokens']:,}",
                    f"{sc.cost['output_tokens']:,}",
                    _fmt_cost(sc.cost["total_cost_usd"]),
                ]
                for sc in scorecards
            ],
        ),
        "",
    ]

    # Per-model detail blocks
    lines += ["## Per-Model Details", ""]
    for sc in scorecards:
        routing = sc.routing
        tc = sc.tool_calls
        lines += [
            f"### {sc.model}",
            "",
            f"- Routing: {routing.passed}/{routing.total_scenarios} ({_pct(routing.accuracy_pct)})",
            f"- Tool-calls: {tc.passed}/{tc.total_scenarios} ({_pct(tc.accuracy_pct)})",
            f"- Cost: {_fmt_cost(sc.cost['total_cost_usd'])} ({sc.cost['total_tokens']:,} tokens)",
            "",
        ]

    path = output_dir / "summary.md"
    path.write_text("\n".join(lines))
    logger.debug("Written %s", path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def generate_scorecards(
    results: BenchmarkResult,
    scorecards: list[ModelScorecard],
    *,
    base_dir: Path | None = None,
    timestamp: str | None = None,
) -> Path:
    """Generate all scorecard files for a completed benchmark run.

    Writes the following structure under ``base_dir/<timestamp>/``:
    - ``summary.md``
    - ``<model>/routing-scorecard.md``
    - ``<model>/tool-call-scorecard.md``
    - ``<model>/cost-summary.md``
    - ``<model>/raw-results.json``

    After writing, prints the output directory path to stdout.

    Parameters
    ----------
    results:
        Fully-populated BenchmarkResult with all model runs.
    scorecards:
        Pre-computed ModelScorecard list from ``compute_all_scorecards()``.
        Expected to be sorted by routing accuracy descending.
    base_dir:
        Base output directory.  Defaults to ``Path(".tmp/e2e-scorecards")``.
    timestamp:
        Override the timestamp used in the directory name.  Defaults to
        ``time.strftime("%Y%m%d-%H%M%S")``.  Useful for deterministic testing.

    Returns
    -------
    Path
        The output directory path where scorecards were written.
    """
    effective_base = base_dir or _SCORECARD_BASE_DIR
    effective_ts = timestamp or time.strftime("%Y%m%d-%H%M%S")
    output_dir = effective_base / effective_ts
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Writing scorecards to: %s", output_dir)

    for scorecard in scorecards:
        model = scorecard.model
        model_dir = output_dir / model
        model_dir.mkdir(parents=True, exist_ok=True)

        entries = results.for_model(model)

        _write_routing_scorecard(scorecard, model_dir)
        _write_tool_call_scorecard(scorecard, model_dir)
        _write_cost_summary(scorecard, model_dir, entries)
        _write_raw_results(model, entries, model_dir)

        logger.info("Scorecards written for model: %s", model)

    _write_summary(scorecards, output_dir)

    # Print output path to terminal (acceptance criterion 8)
    output_path_str = str(output_dir.resolve())
    print(f"\nScorecard output directory: {output_path_str}")

    return output_dir

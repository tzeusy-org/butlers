"""Discretion LLM benchmarks — accuracy and latency.

All prompts are run exactly once via the session-scoped ``all_results`` fixture.
Individual test functions assert on different metrics from that shared result set.

NOT run in CI/CD. Requires a live Ollama endpoint.

Usage:
    uv run pytest tests/benchmarks/discretion_layer/ -v --override-ini="addopts=" \\
        --model gemma3:4b --junit-xml=bench-results.xml
"""

from __future__ import annotations

import statistics
from collections import defaultdict

import pytest

pytestmark = [pytest.mark.bench, pytest.mark.discretion_bench]

# The live-listener connector uses a 3s timeout for discretion calls.
CONNECTOR_TIMEOUT_MS = 3000.0


def _percentile(data: list[float], pct: float) -> float:
    """Calculate percentile from sorted data."""
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (pct / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[f]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


# ---------------------------------------------------------------------------
# Accuracy
# ---------------------------------------------------------------------------


def test_overall_accuracy(
    all_results: list[dict],
    model_name: str,
    bench_report: dict,
    record_property,
) -> None:
    """Overall classification accuracy across all prompts."""
    errors = [r for r in all_results if r["error"]]
    evaluated = [r for r in all_results if not r["error"]]
    unparseable = [r for r in evaluated if r["verdict"] is None]
    classified = [r for r in evaluated if r["verdict"] is not None]
    correct = [r for r in classified if r["verdict"] == r["expected"]]

    bench_report["counts"] = {
        "total": len(all_results),
        "errors": len(errors),
        "unparseable": len(unparseable),
        "classified": len(classified),
    }
    record_property("errors", str(len(errors)))
    record_property("unparseable", str(len(unparseable)))

    # Confusion matrix (only from classified results)
    tp = sum(1 for r in classified if r["expected"] == "FORWARD" and r["verdict"] == "FORWARD")
    fp = sum(1 for r in classified if r["expected"] == "IGNORE" and r["verdict"] == "FORWARD")
    tn = sum(1 for r in classified if r["expected"] == "IGNORE" and r["verdict"] == "IGNORE")
    fn = sum(1 for r in classified if r["expected"] == "FORWARD" and r["verdict"] == "IGNORE")
    bench_report["confusion_matrix"] = {"tp": tp, "fp": fp, "tn": tn, "fn": fn}
    for k, v in [("tp", tp), ("fp", fp), ("tn", tn), ("fn", fn)]:
        record_property(k, str(v))

    # Per-category accuracy
    cat_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in classified:
        cat = r["category"]
        cat_stats[cat]["total"] += 1
        if r["verdict"] == r["expected"]:
            cat_stats[cat]["correct"] += 1
    bench_report["per_category"] = dict(cat_stats)

    accuracy = len(correct) / len(classified) if classified else 0
    bench_report["accuracy"] = {"value": accuracy, "passed": accuracy >= 0.70}
    record_property("accuracy", f"{accuracy:.4f}")

    assert len(errors) <= len(all_results) * 0.05, (
        f"{len(errors)}/{len(all_results)} requests failed — check Ollama connectivity"
    )
    assert len(unparseable) <= len(evaluated) * 0.10, (
        f"{len(unparseable)}/{len(evaluated)} responses unparseable — "
        f"model may not follow FORWARD/IGNORE format"
    )
    assert accuracy >= 0.70, f"Overall accuracy {accuracy:.1%} below 70% threshold for {model_name}"


def test_forward_recall(
    all_results: list[dict],
    model_name: str,
    bench_report: dict,
    record_property,
) -> None:
    """FORWARD cases must have high recall — missing a real command is critical."""
    fwd = [r for r in all_results if r["expected"] == "FORWARD" and not r["error"]]
    correct = [r for r in fwd if r["verdict"] == "FORWARD"]

    recall = len(correct) / len(fwd) if fwd else 0
    bench_report["forward_recall"] = {"value": recall, "passed": recall >= 0.85}
    record_property("forward_recall", f"{recall:.4f}")

    assert recall >= 0.85, f"FORWARD recall {recall:.1%} below 85% threshold for {model_name}"


def test_ignore_precision(
    all_results: list[dict],
    model_name: str,
    bench_report: dict,
    record_property,
) -> None:
    """IGNORE cases: at least 60% should be correctly filtered.

    Threshold is intentionally lower than FORWARD recall — the system is
    fail-open (FORWARD-biased). Forwarding noise is acceptable; missing
    real commands is not.
    """
    ign = [r for r in all_results if r["expected"] == "IGNORE" and not r["error"]]
    correct = [r for r in ign if r["verdict"] == "IGNORE"]

    ignore_rate = len(correct) / len(ign) if ign else 0
    bench_report["ignore_precision"] = {"value": ignore_rate, "passed": ignore_rate >= 0.60}
    record_property("ignore_precision", f"{ignore_rate:.4f}")

    assert ignore_rate >= 0.60, (
        f"IGNORE rate {ignore_rate:.1%} below 60% threshold for {model_name}"
    )


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------


def test_latency_percentiles(
    all_results: list[dict],
    model_name: str,
    bench_report: dict,
    record_property,
) -> None:
    """p95 latency must stay within the live-listener connector's 3s budget.

    The cold-start (first) request is excluded to avoid skewing the
    steady-state distribution.
    """
    steady = [r for r in all_results[1:] if not r["error"]]
    latencies = [r["latency_ms"] for r in steady]

    assert len(latencies) > 0, "No successful requests — check Ollama connectivity"

    p95 = _percentile(latencies, 95)
    total_ms = sum(latencies)
    n = len(latencies)

    lat_data = {
        "mean": statistics.mean(latencies),
        "p50": _percentile(latencies, 50),
        "p75": _percentile(latencies, 75),
        "p90": _percentile(latencies, 90),
        "p95": p95,
        "p99": _percentile(latencies, 99),
        "min": min(latencies),
        "max": max(latencies),
        "stdev": statistics.stdev(latencies) if n > 1 else None,
        "throughput": n / (total_ms / 1000) if total_ms > 0 else None,
        "over_budget": sum(1 for lat in latencies if lat > CONNECTOR_TIMEOUT_MS),
        "total_requests": n,
        "passed": p95 <= CONNECTOR_TIMEOUT_MS,
    }
    bench_report["latency"] = lat_data
    for k, v in lat_data.items():
        if v is not None:
            record_property(f"latency_{k}", f"{v:.2f}" if isinstance(v, float) else str(v))

    assert p95 <= CONNECTOR_TIMEOUT_MS, (
        f"p95 latency {p95:.0f}ms exceeds {CONNECTOR_TIMEOUT_MS:.0f}ms "
        f"connector timeout for {model_name}"
    )


def test_cold_start(
    all_results: list[dict],
    model_name: str,
    bench_report: dict,
    record_property,
) -> None:
    """First request latency (includes model load if cold) must be under 30s."""
    first = all_results[0]

    assert first["error"] is None, f"Cold start failed: {first['error']}"

    bench_report["cold_start"] = {
        "value": first["latency_ms"],
        "passed": first["latency_ms"] < 30_000,
    }
    record_property("cold_start_ms", f"{first['latency_ms']:.2f}")

    assert first["latency_ms"] < 30_000, (
        f"Cold start took {first['latency_ms']:.0f}ms — model may be too large for GPU"
    )

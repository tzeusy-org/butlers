"""Switchboard routing benchmarks — accuracy and latency.

All scenarios are run exactly once via the session-scoped ``routing_results``
fixture. Uses the real OpenCodeAdapter to spawn ``opencode run`` with a mock
MCP server capturing ``route_to_butler`` decisions.

NOT run in CI/CD. Requires ``opencode`` CLI on PATH with a configured model.

Usage:
    uv run pytest tests/benchmarks/switchboard/ -v --override-ini="addopts=" \\
        --model glm-5 --junit-xml=switchboard-bench.xml
"""

from __future__ import annotations

import statistics
from collections import defaultdict

import pytest

pytestmark = [pytest.mark.bench, pytest.mark.switchboard_bench]


def _percentile(data: list[float], pct: float) -> float:
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


def test_routing_accuracy(
    routing_results: list[dict],
    model_name: str,
    sw_report: dict,
    record_property,
) -> None:
    """Overall routing accuracy across all scenarios."""
    errors = [r for r in routing_results if r["error"]]
    evaluated = [r for r in routing_results if not r["error"]]
    unparseable = [r for r in evaluated if r["routed_to"] is None]
    classified = [r for r in evaluated if r["routed_to"] is not None]
    correct = [r for r in classified if r["routed_to"] == r["expected"]]

    sw_report["counts"] = {
        "total": len(routing_results),
        "errors": len(errors),
        "unparseable": len(unparseable),
        "classified": len(classified),
    }
    record_property("errors", str(len(errors)))
    record_property("unparseable", str(len(unparseable)))

    # Per-butler accuracy
    per_butler: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in classified:
        butler = r["expected"]
        per_butler[butler]["total"] += 1
        if r["routed_to"] == r["expected"]:
            per_butler[butler]["correct"] += 1
    sw_report["per_butler"] = dict(per_butler)

    # Confusion matrix: expected → actual → count
    cm: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in classified:
        cm[r["expected"]][r["routed_to"]] += 1
    sw_report["confusion_matrix"] = {k: dict(v) for k, v in cm.items()}

    accuracy = len(correct) / len(classified) if classified else 0
    sw_report["accuracy"] = {"value": accuracy, "passed": accuracy >= 0.70}
    record_property("accuracy", f"{accuracy:.4f}")

    # Schema compliance: count route_to_butler calls where prompt was missing
    prompt_missing = 0
    for r in routing_results:
        for cap in r.get("captured_calls", []):
            if cap.get("tool") == "route_to_butler" and cap.get("prompt_missing"):
                prompt_missing += 1
    total_route_calls = sum(
        1
        for r in routing_results
        for cap in r.get("captured_calls", [])
        if cap.get("tool") == "route_to_butler"
    )
    if total_route_calls:
        schema_compliance = 1 - (prompt_missing / total_route_calls)
        sw_report["schema_compliance"] = {
            "value": schema_compliance,
            "prompt_missing": prompt_missing,
            "total_route_calls": total_route_calls,
        }
        record_property("schema_compliance", f"{schema_compliance:.4f}")
        record_property("prompt_missing_count", str(prompt_missing))

    assert len(errors) <= len(routing_results) * 0.05, (
        f"{len(errors)}/{len(routing_results)} requests failed — check opencode/model availability"
    )
    assert len(unparseable) <= len(evaluated) * 0.10, (
        f"{len(unparseable)}/{len(evaluated)} responses unparseable — "
        f"model may not follow single-butler-name format"
    )
    assert accuracy >= 0.70, f"Routing accuracy {accuracy:.1%} below 70% threshold for {model_name}"


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------


def test_routing_latency(
    routing_results: list[dict],
    model_name: str,
    sw_report: dict,
    record_property,
) -> None:
    """Routing latency distribution (excluding cold start)."""
    steady = [r for r in routing_results[1:] if not r["error"]]
    latencies = [r["latency_ms"] for r in steady]

    assert len(latencies) > 0, "No successful requests — check opencode/model availability"

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
        "total_requests": n,
        "passed": p95 <= 3000.0,
    }
    sw_report["latency"] = lat_data
    for k, v in lat_data.items():
        if v is not None:
            record_property(f"latency_{k}", f"{v:.2f}" if isinstance(v, float) else str(v))

    assert p95 <= 3000.0, f"p95 latency {p95:.0f}ms exceeds 3000ms budget for {model_name}"


def test_routing_cold_start(
    routing_results: list[dict],
    model_name: str,
    sw_report: dict,
    record_property,
) -> None:
    """First request latency (includes model load if cold) must be under 30s."""
    first = routing_results[0]

    assert first["error"] is None, f"Cold start failed: {first['error']}"

    sw_report["cold_start"] = {
        "value": first["latency_ms"],
        "passed": first["latency_ms"] < 30_000,
    }
    record_property("cold_start_ms", f"{first['latency_ms']:.2f}")

    assert first["latency_ms"] < 30_000, (
        f"Cold start took {first['latency_ms']:.0f}ms — model may be too large for GPU"
    )

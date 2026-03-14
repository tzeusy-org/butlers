"""Latency and throughput benchmarks for the discretion LLM.

Measures per-request latency across all prompts and reports p50/p95/p99
percentiles, mean, and throughput. Also validates that the model stays
within the 3-second timeout budget used by the live-listener connector.

NOT run in CI/CD. Requires a live Ollama endpoint.

Usage:
    uv run pytest tests/discretion-llm-bench/test_latency.py -v \
        --ollama-url https://ollama.parrot-hen.ts.net \
        --model gemma3:4b
"""

from __future__ import annotations

import statistics

import pytest

from .helpers import build_prompt, call_discretion

pytestmark = pytest.mark.discretion_bench

# The live-listener connector uses a 3s timeout for discretion calls.
CONNECTOR_TIMEOUT_MS = 3000.0


def _collect_latencies(
    prompts: list[dict], ollama_url: str, model: str, timeout: float
) -> tuple[list[float], int]:
    """Run all prompts and return (latencies_ms, error_count)."""
    latencies: list[float] = []
    errors = 0
    for entry in prompts:
        prompt = build_prompt(entry)
        result = call_discretion(
            prompt, ollama_url=ollama_url, model=model, timeout=timeout
        )
        if result["error"]:
            errors += 1
        else:
            latencies.append(result["latency_ms"])
    return latencies, errors


def _percentile(data: list[float], pct: float) -> float:
    """Calculate percentile from sorted data."""
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (pct / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[f]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def _print_report(latencies: list[float], errors: int, model: str) -> None:
    """Print a human-readable latency report."""
    total_ms = sum(latencies)
    n = len(latencies)

    print(f"\n{'=' * 70}")
    print(f"  DISCRETION LLM LATENCY REPORT — {model}")
    print(f"{'=' * 70}")
    print(f"  Requests:    {n + errors} ({errors} errors)")
    print(f"  Total time:  {total_ms / 1000:.1f}s")
    if n > 0:
        print(f"  Throughput:  {n / (total_ms / 1000):.1f} req/s")
        print()
        print(f"  {'Metric':15s} {'Value':>10s}")
        print(f"  {'-' * 27}")
        print(f"  {'Mean':15s} {statistics.mean(latencies):>9.0f}ms")
        print(f"  {'Median (p50)':15s} {_percentile(latencies, 50):>9.0f}ms")
        print(f"  {'p75':15s} {_percentile(latencies, 75):>9.0f}ms")
        print(f"  {'p90':15s} {_percentile(latencies, 90):>9.0f}ms")
        print(f"  {'p95':15s} {_percentile(latencies, 95):>9.0f}ms")
        print(f"  {'p99':15s} {_percentile(latencies, 99):>9.0f}ms")
        print(f"  {'Min':15s} {min(latencies):>9.0f}ms")
        print(f"  {'Max':15s} {max(latencies):>9.0f}ms")
        print(f"  {'Std Dev':15s} {statistics.stdev(latencies):>9.0f}ms" if n > 1 else "")

        over_budget = sum(1 for lat in latencies if lat > CONNECTOR_TIMEOUT_MS)
        pct = over_budget / n * 100
        print(f"\n  Over {CONNECTOR_TIMEOUT_MS:.0f}ms budget: {over_budget}/{n} ({pct:.1f}%)")
    print(f"{'=' * 70}\n")


def test_latency_percentiles(
    prompts: list[dict],
    ollama_url: str,
    model_name: str,
    bench_timeout: float,
) -> None:
    """Measure latency distribution across all prompts."""
    latencies, errors = _collect_latencies(prompts, ollama_url, model_name, bench_timeout)
    _print_report(latencies, errors, model_name)

    assert len(latencies) > 0, "No successful requests — check Ollama connectivity"

    p95 = _percentile(latencies, 95)

    # p95 must be under the connector's 3s timeout to be viable in production.
    assert p95 <= CONNECTOR_TIMEOUT_MS, (
        f"p95 latency {p95:.0f}ms exceeds {CONNECTOR_TIMEOUT_MS:.0f}ms "
        f"connector timeout for {model_name}"
    )


def test_cold_start(
    prompts: list[dict],
    ollama_url: str,
    model_name: str,
    bench_timeout: float,
) -> None:
    """First request latency (includes model load if cold).

    Ollama may need to load the model into VRAM on first request.
    This test measures worst-case first-request latency separately
    so it doesn't skew the main distribution.
    """
    entry = prompts[0]
    prompt = build_prompt(entry)

    result = call_discretion(
        prompt, ollama_url=ollama_url, model=model_name, timeout=30.0
    )

    assert result["error"] is None, f"Cold start failed: {result['error']}"

    print(f"\n  Cold start latency ({model_name}): {result['latency_ms']:.0f}ms")
    print(f"  Completion tokens: {result['completion_tokens']}")

    # Cold start can take a while if model needs loading.
    # We just want it under 30s (the extended timeout).
    assert result["latency_ms"] < 30_000, (
        f"Cold start took {result['latency_ms']:.0f}ms — model may be too large for GPU"
    )

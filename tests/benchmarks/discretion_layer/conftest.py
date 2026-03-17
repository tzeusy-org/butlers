"""Discretion layer benchmark configuration.

Fixtures for running all discretion prompts once and caching results.
CLI options and shared fixtures (ollama_url, model_name, bench_timeout)
are inherited from the parent tests/benchmarks/conftest.py.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from .helpers import build_prompt, call_discretion

PROMPTS_FILE = Path(__file__).parent / "prompts.jsonl"
RESULTS_FILE = Path(__file__).parent / "results.md"

_RESULTS_HEADER = (
    "| Model | Accuracy | FWD Recall | IGN Prec | p50 | p95 | p99"
    " | Cold Start | req/s | Prompts | Date |"
)
_RESULTS_SEP = (
    "|-------|----------|------------|----------|-----|-----|-----"
    "|------------|-------|---------|------|"
)


def pytest_configure(config: pytest.Config) -> None:
    """Initialize the discretion benchmark report store."""
    config._discretion_report = {}  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def bench_report(request: pytest.FixtureRequest) -> dict:
    """Session-scoped store for discretion benchmark metrics."""
    return request.config._discretion_report  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def prompts() -> list[dict]:
    """Load all test prompts from prompts.jsonl."""
    entries = []
    with PROMPTS_FILE.open() as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


@pytest.fixture(scope="session")
def all_results(
    prompts: list[dict],
    ollama_url: str,
    model_name: str,
    bench_timeout: float,
    bench_report: dict,
) -> list[dict]:
    """Run every prompt exactly once and cache results for all test functions.

    The first request uses an extended 30s timeout to accommodate model loading
    (cold start). All subsequent requests use the configured bench_timeout.
    """
    import sys
    import time

    bench_report["model"] = model_name
    bench_report["ollama_url"] = ollama_url
    total = len(prompts)
    t_start = time.monotonic()

    results: list[dict] = []
    for i, entry in enumerate(prompts):
        prompt = build_prompt(entry)
        timeout = 30.0 if i == 0 else bench_timeout
        result = call_discretion(prompt, ollama_url=ollama_url, model=model_name, timeout=timeout)
        result["id"] = entry["id"]
        result["expected"] = entry["expected"]
        result["category"] = entry["category"]
        result["text"] = entry["text"]
        results.append(result)

        elapsed = time.monotonic() - t_start
        avg = elapsed / (i + 1)
        eta = avg * (total - i - 1)
        verdict = result.get("verdict") or "ERR"
        sys.stderr.write(
            f"\r  discretion [{i + 1}/{total}] "
            f"{verdict:<8s} "
            f"({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining)   "
        )
        sys.stderr.flush()

    sys.stderr.write("\n")
    return results


# ---------------------------------------------------------------------------
# Results persistence
# ---------------------------------------------------------------------------


def _persist_results(report: dict) -> None:
    """Idempotently update the model's row in results.md."""
    model = report.get("model")
    if not model or "accuracy" not in report:
        return

    acc = report.get("accuracy", {}).get("value")
    fwd = report.get("forward_recall", {}).get("value")
    ign = report.get("ignore_precision", {}).get("value")
    lat = report.get("latency", {})
    cold = report.get("cold_start", {}).get("value")
    counts = report.get("counts", {})

    def fmt_pct(v: float | None) -> str:
        return f"{v:.1%}" if v is not None else "—"

    def fmt_ms(v: float | None) -> str:
        return f"{v:.0f}ms" if v is not None else "—"

    cols = [
        model,
        fmt_pct(acc),
        fmt_pct(fwd),
        fmt_pct(ign),
        fmt_ms(lat.get("p50")),
        fmt_ms(lat.get("p95")),
        fmt_ms(lat.get("p99")),
        fmt_ms(cold),
        f"{lat['throughput']:.1f}" if lat.get("throughput") else "—",
        str(counts.get("total", "—")),
        datetime.date.today().isoformat(),
    ]
    row = "| " + " | ".join(cols) + " |"

    if RESULTS_FILE.exists():
        lines = RESULTS_FILE.read_text().splitlines()
    else:
        lines = [
            "# Discretion Layer Benchmark Results",
            "",
            "Maintained by the `discretion_layer` benchmark suite. Each row is updated",
            "idempotently when the benchmark is run for that model.",
            "",
            "```",
            'uv run pytest tests/benchmarks/discretion_layer/ -v --override-ini="addopts="'
            " --model <name>",
            "```",
            "",
            _RESULTS_HEADER,
            _RESULTS_SEP,
        ]

    existing_idx = None
    for i, line in enumerate(lines):
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 1 and cells[1] == model:
            existing_idx = i
            break

    if existing_idx is not None:
        lines[existing_idx] = row
    else:
        last_table_idx = 0
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith("|"):
                last_table_idx = i
                break
        lines.insert(last_table_idx + 1, row)

    RESULTS_FILE.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Terminal summary
# ---------------------------------------------------------------------------


def pytest_terminal_summary(terminalreporter, exitstatus: int, config: pytest.Config) -> None:
    """Print a consolidated discretion benchmark report after all tests complete."""
    report: dict = getattr(config, "_discretion_report", {})
    if not report:
        return

    model = report.get("model", "unknown")
    url = report.get("ollama_url", "")
    W = 72

    lines: list[str] = []
    lines.append("")
    lines.append("=" * W)
    lines.append(f"  DISCRETION LLM BENCHMARK REPORT -- {model}")
    if url:
        lines.append(f"  {url}")
    lines.append("=" * W)

    counts = report.get("counts")
    if counts:
        lines.append("")
        lines.append(
            f"  Prompts: {counts['total']}  |  Errors: {counts['errors']}  |  "
            f"Unparseable: {counts['unparseable']}  |  Classified: {counts['classified']}"
        )

    acc = report.get("accuracy")
    fwd = report.get("forward_recall")
    ign = report.get("ignore_precision")

    if any(x is not None for x in [acc, fwd, ign]):
        lines.append("")
        lines.append("  ACCURACY")
        lines.append(f"  {'-' * 64}")
        lines.append(f"  {'Metric':<24s} {'Value':>8s}   {'Threshold':>10s}   {'Result':>6s}")
        lines.append(f"  {'-' * 64}")
        for label, entry, thresh in [
            ("Overall accuracy", acc, ">= 70%"),
            ("FORWARD recall", fwd, ">= 85%"),
            ("IGNORE precision", ign, ">= 60%"),
        ]:
            if entry is not None:
                v = f"{entry['value']:.1%}"
                r = "PASS" if entry["passed"] else "FAIL"
                lines.append(f"  {label:<24s} {v:>8s}   {thresh:>10s}   {r:>6s}")
        lines.append(f"  {'-' * 64}")

    cm = report.get("confusion_matrix")
    if cm:
        lines.append("")
        lines.append("  Confusion Matrix")
        lines.append(f"  {'':24s} {'Pred FORWARD':>14s} {'Pred IGNORE':>13s}")
        lines.append(f"  {'Actual FORWARD':<24s} {cm['tp']:>14d} {cm['fn']:>13d}")
        lines.append(f"  {'Actual IGNORE':<24s} {cm['fp']:>14d} {cm['tn']:>13d}")

    lat = report.get("latency")
    cold = report.get("cold_start")

    if lat is not None or cold is not None:
        lines.append("")
        lines.append("  LATENCY")
        lines.append(f"  {'-' * 64}")
        lines.append(f"  {'Metric':<24s} {'Value':>8s}   {'Threshold':>10s}   {'Result':>6s}")
        lines.append(f"  {'-' * 64}")
        if lat is not None:
            for label, key, thresh in [
                ("Mean", "mean", ""),
                ("p50", "p50", ""),
                ("p75", "p75", ""),
                ("p90", "p90", ""),
                ("p95", "p95", "<= 3000ms"),
                ("p99", "p99", ""),
                ("Min", "min", ""),
                ("Max", "max", ""),
                ("Std Dev", "stdev", ""),
            ]:
                val = lat.get(key)
                if val is None:
                    continue
                v = f"{val:.0f}ms"
                if thresh:
                    r = "PASS" if lat["passed"] else "FAIL"
                    lines.append(f"  {label:<24s} {v:>8s}   {thresh:>10s}   {r:>6s}")
                else:
                    lines.append(f"  {label:<24s} {v:>8s}")
        if cold is not None:
            v = f"{cold['value']:.0f}ms"
            r = "PASS" if cold["passed"] else "FAIL"
            lines.append(f"  {'Cold start':<24s} {v:>8s}   {'<= 30s':>10s}   {r:>6s}")
        lines.append(f"  {'-' * 64}")
        if lat is not None:
            parts = []
            if lat.get("throughput") is not None:
                parts.append(f"{lat['throughput']:.1f} req/s")
            over = lat.get("over_budget")
            n = lat.get("total_requests")
            if over is not None and n:
                pct = over / n * 100
                parts.append(f"Over budget: {over}/{n} ({pct:.1f}%)")
            if parts:
                lines.append(f"  {' | '.join(parts)}")

    checks = []
    for key in ["accuracy", "forward_recall", "ignore_precision", "latency", "cold_start"]:
        entry = report.get(key)
        if entry is not None and "passed" in entry:
            checks.append(entry["passed"])
    if checks:
        passed = sum(checks)
        total = len(checks)
        failed = total - passed
        lines.append("")
        verdict = f"  RESULT: {passed}/{total} passed"
        if failed:
            verdict += f", {failed} FAILED"
        lines.append(verdict)

    lines.append("=" * W)
    lines.append("")

    for line in lines:
        terminalreporter.write_line(line)

    _persist_results(report)

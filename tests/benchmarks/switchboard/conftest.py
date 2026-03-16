"""Switchboard routing benchmark configuration.

Fixtures for running all routing scenarios once and caching results.
CLI options and shared fixtures (ollama_url, model_name, bench_timeout)
are inherited from the parent tests/benchmarks/conftest.py.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from .helpers import build_routing_prompt, call_routing

SCENARIOS_FILE = Path(__file__).parent / "scenarios.jsonl"
RESULTS_FILE = Path(__file__).parent / "results.md"

_RESULTS_HEADER = (
    "| Model | Accuracy | p50 | p95 | p99"
    " | Cold Start | req/s | Scenarios | Date |"
)
_RESULTS_SEP = (
    "|-------|----------|-----|-----|-----"
    "|------------|-------|-----------|------|"
)


def pytest_configure(config: pytest.Config) -> None:
    """Initialize the switchboard benchmark report store."""
    config._switchboard_report = {}  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def sw_report(request: pytest.FixtureRequest) -> dict:
    """Session-scoped store for switchboard benchmark metrics."""
    return request.config._switchboard_report  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def scenarios() -> list[dict]:
    """Load all routing scenarios from scenarios.jsonl."""
    entries = []
    with SCENARIOS_FILE.open() as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


@pytest.fixture(scope="session")
def routing_results(
    scenarios: list[dict],
    ollama_url: str,
    model_name: str,
    bench_timeout: float,
    sw_report: dict,
) -> list[dict]:
    """Run every scenario exactly once and cache results for all test functions."""
    sw_report["model"] = model_name
    sw_report["ollama_url"] = ollama_url

    results: list[dict] = []
    for i, entry in enumerate(scenarios):
        prompt = build_routing_prompt(entry)
        timeout = 30.0 if i == 0 else bench_timeout
        result = call_routing(prompt, ollama_url=ollama_url, model=model_name, timeout=timeout)
        result["id"] = entry["id"]
        result["expected"] = entry["expected"]
        result["category"] = entry["category"]
        result["text"] = entry["text"]
        results.append(result)
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
            "# Switchboard Routing Benchmark Results",
            "",
            "Maintained by the `switchboard` benchmark suite. Each row is updated",
            "idempotently when the benchmark is run for that model.",
            "",
            "```",
            'uv run pytest tests/benchmarks/switchboard/ -v --override-ini="addopts="'
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
    """Print a consolidated switchboard benchmark report."""
    report: dict = getattr(config, "_switchboard_report", {})
    if not report:
        return

    model = report.get("model", "unknown")
    url = report.get("ollama_url", "")
    W = 72

    lines: list[str] = []
    lines.append("")
    lines.append("=" * W)
    lines.append(f"  SWITCHBOARD ROUTING BENCHMARK REPORT -- {model}")
    if url:
        lines.append(f"  {url}")
    lines.append("=" * W)

    counts = report.get("counts")
    if counts:
        lines.append("")
        lines.append(
            f"  Scenarios: {counts['total']}  |  Errors: {counts['errors']}  |  "
            f"Unparseable: {counts['unparseable']}  |  Classified: {counts['classified']}"
        )

    acc = report.get("accuracy")
    if acc is not None:
        lines.append("")
        lines.append(f"  Overall routing accuracy: {acc['value']:.1%}")

    # Per-butler breakdown
    per_butler = report.get("per_butler")
    if per_butler:
        lines.append("")
        lines.append("  PER-BUTLER ACCURACY")
        lines.append(f"  {'-' * 52}")
        lines.append(f"  {'Butler':<16s} {'Correct':>8s} {'Total':>6s} {'Acc':>7s}")
        lines.append(f"  {'-' * 52}")
        for butler in sorted(per_butler.keys()):
            s = per_butler[butler]
            pct = s["correct"] / s["total"] * 100 if s["total"] else 0
            lines.append(f"  {butler:<16s} {s['correct']:>8d} {s['total']:>6d} {pct:>6.1f}%")
        lines.append(f"  {'-' * 52}")

    # Confusion matrix
    cm = report.get("confusion_matrix")
    if cm:
        butlers = sorted(cm.keys())
        lines.append("")
        lines.append("  ROUTING CONFUSION MATRIX (expected \u2192 actual)")
        header = f"  {'':>16s}" + "".join(f" {b[:6]:>7s}" for b in butlers)
        lines.append(header)
        for expected in butlers:
            row_str = f"  {expected:<16s}"
            for actual in butlers:
                count = cm[expected].get(actual, 0)
                row_str += f" {count:>7d}"
            lines.append(row_str)

    lat = report.get("latency")
    cold = report.get("cold_start")
    if lat is not None or cold is not None:
        lines.append("")
        lines.append("  LATENCY")
        lines.append(f"  {'-' * 40}")
        if lat is not None:
            for label, key in [
                ("Mean", "mean"), ("p50", "p50"), ("p95", "p95"),
                ("p99", "p99"), ("Min", "min"), ("Max", "max"),
            ]:
                val = lat.get(key)
                if val is not None:
                    lines.append(f"  {label:<16s} {val:>8.0f}ms")
        if cold is not None:
            lines.append(f"  {'Cold start':<16s} {cold['value']:>8.0f}ms")
        lines.append(f"  {'-' * 40}")
        if lat and lat.get("throughput"):
            lines.append(f"  {lat['throughput']:.1f} req/s")

    checks = []
    for key in ["accuracy", "latency", "cold_start"]:
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

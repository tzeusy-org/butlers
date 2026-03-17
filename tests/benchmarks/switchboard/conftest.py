"""Switchboard routing benchmark configuration.

Runs each scenario through the real switchboard LLM pipeline:
  OpenCodeAdapter → opencode run → mock MCP server (captures route_to_butler)

The mock MCP server implements route_to_butler and notify as real MCP tools.
The LLM sees them exactly as it would in production, calls them, and we
capture which butler(s) it routed to.

CLI options (--model, --bench-timeout) are inherited from the parent
tests/benchmarks/conftest.py.
"""

from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import pytest

from .helpers import build_routing_prompt, call_routing
from .mock_mcp import MockMCPServer

SCENARIOS_FILE = Path(__file__).parent / "scenarios.jsonl"
RESULTS_FILE = Path(__file__).parent / "results.md"

_RESULTS_HEADER = "| Model | Accuracy | p50 | p95 | p99 | Cold Start | req/s | Scenarios | Date |"
_RESULTS_SEP = "|-------|----------|-----|-----|-----|------------|-------|-----------|------|"


def pytest_configure(config: pytest.Config) -> None:
    config._switchboard_report = {}  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def sw_report(request: pytest.FixtureRequest) -> dict:
    return request.config._switchboard_report  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def scenarios() -> list[dict]:
    entries = []
    with SCENARIOS_FILE.open() as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


@pytest.fixture(scope="session")
def mock_mcp_server() -> MockMCPServer:
    """Start a mock MCP server for the entire benchmark session."""
    server = MockMCPServer()
    server.start()
    yield server  # type: ignore[misc]
    server.stop()


@pytest.fixture(scope="session")
def _require_opencode():
    """Skip the entire benchmark if opencode CLI is not available."""
    if not shutil.which("opencode"):
        pytest.skip("opencode CLI not found on PATH")


@pytest.fixture(scope="session")
def routing_results(
    scenarios: list[dict],
    mock_mcp_server: MockMCPServer,
    model_name: str,
    ollama_url: str,
    bench_timeout: float,
    sw_report: dict,
    _require_opencode,
) -> list[dict]:
    """Run every scenario through the real OpenCodeAdapter and capture results."""
    import sys
    import time

    sw_report["model"] = model_name
    total = len(scenarios)
    t_start = time.monotonic()

    results: list[dict] = []
    for i, entry in enumerate(scenarios):
        mock_mcp_server.reset_captures()
        prompt = build_routing_prompt(entry)

        # OpenCode spawns a subprocess + LLM call; 10s bench_timeout is
        # far too short. Use 120s (enough for cold start + inference).
        result = call_routing(
            prompt,
            mock_mcp_url=mock_mcp_server.url,
            model=model_name,
            ollama_url=ollama_url,
            timeout=120.0,
        )
        result["id"] = entry["id"]
        result["expected"] = entry["expected"]
        result["category"] = entry["category"]
        result["text"] = entry["text"]
        result["captured_calls"] = mock_mcp_server.get_captured_calls()
        results.append(result)

        # Progress output
        elapsed = time.monotonic() - t_start
        avg = elapsed / (i + 1)
        eta = avg * (total - i - 1)
        ok = sum(1 for r in results if r.get("routed_to"))
        err = len(results) - ok
        status = result["routed_to"] or "ERR"
        sys.stderr.write(
            f"\r  switchboard [{i + 1}/{total}] "
            f"{status:<12s} "
            f"ok={ok} err={err}  "
            f"({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining)   "
        )
        sys.stderr.flush()

    sys.stderr.write("\n")
    return results


# ---------------------------------------------------------------------------
# Results persistence
# ---------------------------------------------------------------------------


def _persist_results(report: dict) -> None:
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
    report: dict = getattr(config, "_switchboard_report", {})
    if not report:
        return

    model = report.get("model", "unknown")
    W = 72

    lines: list[str] = []
    lines.append("")
    lines.append("=" * W)
    lines.append(f"  SWITCHBOARD ROUTING BENCHMARK REPORT -- {model}")
    lines.append("=" * W)

    counts = report.get("counts")
    if counts:
        lines.append("")
        lines.append(
            f"  Scenarios: {counts['total']}  |  Errors: {counts['errors']}  |  "
            f"Unparseable: {counts['unparseable']}  |  "
            f"Classified: {counts['classified']}"
        )

    acc = report.get("accuracy")
    if acc is not None:
        lines.append("")
        lines.append(f"  Overall routing accuracy: {acc['value']:.1%}")

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

    cm = report.get("confusion_matrix")
    if cm:
        butlers = sorted(cm.keys())
        lines.append("")
        lines.append("  ROUTING CONFUSION MATRIX (expected -> actual)")
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
                ("Mean", "mean"),
                ("p50", "p50"),
                ("p95", "p95"),
                ("p99", "p99"),
                ("Min", "min"),
                ("Max", "max"),
            ]:
                val = lat.get(key)
                if val is not None:
                    lines.append(f"  {label:<16s} {val:>8.0f}ms")
        if cold is not None:
            lines.append(f"  {'Cold start':<16s} {cold['value']:>8.0f}ms")
        lines.append(f"  {'-' * 40}")
        if lat and lat.get("throughput"):
            lines.append(f"  {lat['throughput']:.1f} req/s")

    sc = report.get("schema_compliance")
    if sc:
        lines.append("")
        lines.append("  SCHEMA COMPLIANCE")
        lines.append(f"  {'-' * 40}")
        lines.append(f"  {'Compliance':<16s} {sc['value']:>7.1%}")
        lines.append(
            f"  {'prompt missing':<16s} {sc['prompt_missing']:>3d} / "
            f"{sc['total_route_calls']} route_to_butler calls"
        )
        lines.append(f"  {'-' * 40}")

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

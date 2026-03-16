"""Pytest configuration for discretion LLM benchmarks.

These tests hit a live Ollama endpoint and are NOT run in CI/CD.
Run manually with:

    uv run pytest tests/discretion_llm_bench/ -v \\
        --override-ini="addopts=" \\
        --ollama-url https://ollama.parrot-hen.ts.net

To compare models:

    uv run pytest tests/discretion_llm_bench/ -v --override-ini="addopts=" --model gemma3:4b
    uv run pytest tests/discretion_llm_bench/ -v --override-ini="addopts=" --model qwen3:4b

JUnit XML output (intermediary data layer for report generation):

    uv run pytest tests/discretion_llm_bench/ -v --override-ini="addopts=" \\
        --model gemma3:4b --junit-xml=bench-results.xml

Each test case embeds its metrics as JUnit XML properties via record_property,
enabling downstream report generators, CI dashboards, and cross-model comparison
tools to consume results without parsing stdout.
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


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--ollama-url",
        default="https://ollama.parrot-hen.ts.net",
        help="Ollama base URL (without /v1 suffix)",
    )
    parser.addoption(
        "--model",
        default="gemma3:4b",
        help="Model name to benchmark (e.g. gemma3:4b, qwen3:4b)",
    )
    parser.addoption(
        "--bench-timeout",
        default=10.0,
        type=float,
        help="Per-request timeout in seconds (default: 10)",
    )


@pytest.fixture(scope="session")
def ollama_url(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--ollama-url").rstrip("/")


@pytest.fixture(scope="session")
def model_name(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--model")


@pytest.fixture(scope="session")
def bench_timeout(request: pytest.FixtureRequest) -> float:
    return request.config.getoption("--bench-timeout")


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


def pytest_configure(config: pytest.Config) -> None:
    """Initialize the benchmark report store."""
    config._bench_report = {}  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def bench_report(request: pytest.FixtureRequest) -> dict:
    """Session-scoped store for benchmark metrics, printed as consolidated report at session end."""
    return request.config._bench_report  # type: ignore[attr-defined]


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
    bench_report["model"] = model_name
    bench_report["ollama_url"] = ollama_url

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
    return results


def _persist_results(report: dict) -> None:
    """Idempotently update the model's row in results.md.

    If the model already has a row, it is replaced. Otherwise a new row is
    appended. The file is created with a header if it doesn't exist yet.
    """
    model = report.get("model")
    if not model or "accuracy" not in report:
        return  # Not enough data to persist

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

    # Read existing file or create template
    if RESULTS_FILE.exists():
        lines = RESULTS_FILE.read_text().splitlines()
    else:
        lines = [
            "# Discretion LLM Benchmark Results",
            "",
            "Maintained by the `discretion_llm_bench` suite. Each row is updated",
            "idempotently when the benchmark is run for that model.",
            "",
            "```",
            "uv run pytest tests/discretion_llm_bench/ -v --override-ini=\"addopts=\""
            " --model <name>",
            "```",
            "",
            _RESULTS_HEADER,
            _RESULTS_SEP,
        ]

    # Find existing row for this model (match first column)
    existing_idx = None
    for i, line in enumerate(lines):
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells: ['', 'Model', 'Accuracy', ...] — cells[1] is first column
        if len(cells) > 1 and cells[1] == model:
            existing_idx = i
            break

    if existing_idx is not None:
        lines[existing_idx] = row
    else:
        # Append after the last table row
        last_table_idx = 0
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith("|"):
                last_table_idx = i
                break
        lines.insert(last_table_idx + 1, row)

    RESULTS_FILE.write_text("\n".join(lines) + "\n")


def pytest_terminal_summary(terminalreporter, exitstatus: int, config: pytest.Config) -> None:
    """Print a consolidated benchmark report after all tests complete."""
    report: dict = getattr(config, "_bench_report", {})
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

    # --- Counts ---
    counts = report.get("counts")
    if counts:
        total = counts["total"]
        errs = counts["errors"]
        unp = counts["unparseable"]
        classified = counts["classified"]
        lines.append("")
        lines.append(
            f"  Prompts: {total}  |  Errors: {errs}  |  "
            f"Unparseable: {unp}  |  Classified: {classified}"
        )

    # --- Accuracy ---
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

    # --- Confusion matrix ---
    cm = report.get("confusion_matrix")
    if cm:
        lines.append("")
        lines.append("  Confusion Matrix")
        lines.append(f"  {'':24s} {'Pred FORWARD':>14s} {'Pred IGNORE':>13s}")
        lines.append(f"  {'Actual FORWARD':<24s} {cm['tp']:>14d} {cm['fn']:>13d}")
        lines.append(f"  {'Actual IGNORE':<24s} {cm['fp']:>14d} {cm['tn']:>13d}")

    # --- Latency ---
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

    # --- Verdict ---
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

    # Persist results to git-committed file for cross-model comparison.
    _persist_results(report)

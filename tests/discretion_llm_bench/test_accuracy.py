"""Accuracy benchmarks for the discretion LLM.

Runs every prompt in prompts.jsonl against the configured model and reports:
- Overall accuracy
- Per-category accuracy
- Confusion matrix (FORWARD/IGNORE)
- Misclassified cases with details

NOT run in CI/CD. Requires a live Ollama endpoint.

Usage:
    uv run pytest tests/discretion-llm-bench/test_accuracy.py -v \
        --ollama-url https://ollama.parrot-hen.ts.net \
        --model gemma3:4b
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from .helpers import build_prompt, call_discretion

# Mark the entire module as requiring a live Ollama endpoint.
pytestmark = pytest.mark.discretion_bench


def _run_all(prompts: list[dict], ollama_url: str, model: str, timeout: float) -> list[dict]:
    """Run all prompts and collect results."""
    results = []
    for entry in prompts:
        prompt = build_prompt(entry)
        result = call_discretion(prompt, ollama_url=ollama_url, model=model, timeout=timeout)
        result["id"] = entry["id"]
        result["expected"] = entry["expected"]
        result["category"] = entry["category"]
        result["text"] = entry["text"]
        results.append(result)
    return results


def _print_report(results: list[dict], model: str) -> None:
    """Print a human-readable accuracy report."""
    total = len(results)
    errors = [r for r in results if r["error"]]
    evaluated = [r for r in results if not r["error"]]
    correct = [r for r in evaluated if r["verdict"] == r["expected"]]
    wrong = [r for r in evaluated if r["verdict"] != r["expected"]]
    unparseable = [r for r in evaluated if r["verdict"] is None]

    print(f"\n{'=' * 70}")
    print(f"  DISCRETION LLM ACCURACY REPORT — {model}")
    print(f"{'=' * 70}")
    print(f"  Total prompts:  {total}")
    print(f"  Errors:         {len(errors)}")
    print(f"  Evaluated:      {len(evaluated)}")
    print(f"  Correct:        {len(correct)} ({len(correct) / len(evaluated) * 100:.1f}%)")
    print(f"  Wrong:          {len(wrong)} ({len(wrong) / len(evaluated) * 100:.1f}%)")
    print(f"  Unparseable:    {len(unparseable)}")

    # Confusion matrix
    tp = sum(1 for r in evaluated if r["expected"] == "FORWARD" and r["verdict"] == "FORWARD")
    fp = sum(1 for r in evaluated if r["expected"] == "IGNORE" and r["verdict"] == "FORWARD")
    tn = sum(1 for r in evaluated if r["expected"] == "IGNORE" and r["verdict"] == "IGNORE")
    fn = sum(1 for r in evaluated if r["expected"] == "FORWARD" and r["verdict"] == "IGNORE")

    print("\n  Confusion Matrix:")
    print(f"  {'':20s} Predicted FORWARD  Predicted IGNORE")
    print(f"  {'Actual FORWARD':20s} {tp:>17d}  {fn:>16d}")
    print(f"  {'Actual IGNORE':20s} {fp:>17d}  {tn:>16d}")

    if tp + fp > 0:
        precision = tp / (tp + fp)
        print(f"\n  Precision (FORWARD): {precision:.1%}")
    if tp + fn > 0:
        recall = tp / (tp + fn)
        print(f"  Recall (FORWARD):    {recall:.1%}")

    # Per-category breakdown
    cat_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in evaluated:
        cat = r["category"]
        cat_stats[cat]["total"] += 1
        if r["verdict"] == r["expected"]:
            cat_stats[cat]["correct"] += 1

    print("\n  Per-Category Accuracy:")
    print(f"  {'Category':25s} {'Correct':>8s} {'Total':>6s} {'Acc':>7s}")
    print(f"  {'-' * 48}")
    for cat in sorted(cat_stats.keys()):
        s = cat_stats[cat]
        acc = s["correct"] / s["total"] * 100 if s["total"] else 0
        print(f"  {cat:25s} {s['correct']:>8d} {s['total']:>6d} {acc:>6.1f}%")

    # Misclassified cases
    if wrong:
        print(f"\n  Misclassified ({len(wrong)}):")
        print(f"  {'-' * 68}")
        for r in wrong:
            print(f"  [{r['id']}] expected={r['expected']} got={r['verdict']}")
            print(f"    text: {r['text'][:80]}")
            print(f"    raw:  {r['raw'][:80]}")
            print()

    print(f"{'=' * 70}\n")


def test_overall_accuracy(
    prompts: list[dict],
    ollama_url: str,
    model_name: str,
    bench_timeout: float,
) -> None:
    """Run all prompts and assert minimum accuracy threshold."""
    results = _run_all(prompts, ollama_url, model_name, bench_timeout)
    _print_report(results, model_name)

    errors = [r for r in results if r["error"]]
    evaluated = [r for r in results if not r["error"]]
    correct = [r for r in evaluated if r["verdict"] == r["expected"]]

    # Hard fail on too many errors
    assert len(errors) <= len(results) * 0.05, (
        f"{len(errors)}/{len(results)} requests failed — check Ollama connectivity"
    )

    accuracy = len(correct) / len(evaluated) if evaluated else 0

    # Minimum accuracy threshold — adjust as models improve
    assert accuracy >= 0.70, f"Overall accuracy {accuracy:.1%} below 70% threshold for {model_name}"


def test_forward_recall(
    forward_prompts: list[dict],
    ollama_url: str,
    model_name: str,
    bench_timeout: float,
) -> None:
    """FORWARD cases must have high recall (don't miss real commands)."""
    results = _run_all(forward_prompts, ollama_url, model_name, bench_timeout)
    evaluated = [r for r in results if not r["error"]]
    correct = [r for r in evaluated if r["verdict"] == "FORWARD"]

    recall = len(correct) / len(evaluated) if evaluated else 0

    # Forward recall is critical — missing a real command is worse than
    # forwarding noise (which the butler can still ignore).
    assert recall >= 0.85, f"FORWARD recall {recall:.1%} below 85% threshold for {model_name}"


def test_ignore_precision(
    ignore_prompts: list[dict],
    ollama_url: str,
    model_name: str,
    bench_timeout: float,
) -> None:
    """IGNORE cases: at least 60% should be correctly filtered.

    This threshold is intentionally lower than FORWARD recall because
    the system is designed to be fail-open (FORWARD-biased). Some IGNORE
    cases being forwarded is acceptable; missing real commands is not.
    """
    results = _run_all(ignore_prompts, ollama_url, model_name, bench_timeout)
    evaluated = [r for r in results if not r["error"]]
    correct = [r for r in evaluated if r["verdict"] == "IGNORE"]

    ignore_rate = len(correct) / len(evaluated) if evaluated else 0

    assert ignore_rate >= 0.60, (
        f"IGNORE rate {ignore_rate:.1%} below 60% threshold for {model_name}"
    )

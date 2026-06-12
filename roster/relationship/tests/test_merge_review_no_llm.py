"""No-LLM guardrail for the merge-review (compare / merge / dismiss) paths.

The ``relationship-merge-review`` spec is binding: **no LLM-provider client,
spawner invocation, embedding call, or generated prose MAY appear in the compare,
merge, or merge-review code paths.** Matching and duplicate detection are
deterministic everywhere (``relationship-entity-lifecycle`` §"Match — deterministic
matching only", brief §0 binding rejection).

This is a static source-scan of the merge-review handlers/helpers in the
relationship router (compare, merge, dismiss-pair, plus the deterministic
duplicate-candidate suppression and snapshot helpers). The scan is scoped via AST
to exactly those function bodies so unrelated router prose does not widen — or
falsely trip — the surface. It needs no database and no async fixtures.

Pattern precedent: ``roster/relationship/tests/test_chronicler_boundary.py`` (the
chronicler-schema source-scan) and RFC 0014 §D5's no-LLM invariant on Chronicler
projection adapters.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROSTER_ROOT = _HERE.parents[1]  # roster/relationship/
_ROUTER = _ROSTER_ROOT / "api" / "router.py"

# Tokens that signal a model call: LLM-provider clients, the butler LLM-CLI
# spawner, embedding services, or generated-prose completion calls. Case-
# insensitive. Word-ish boundaries keep these from matching innocuous substrings.
_FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic client", re.compile(r"\banthropic\b", re.IGNORECASE)),
    ("openai client", re.compile(r"\bopenai\b", re.IGNORECASE)),
    ("claude SDK", re.compile(r"claude_agent_sdk|claude_code_sdk", re.IGNORECASE)),
    ("llm spawner", re.compile(r"\bspawn(er|_llm[_a-z]*)?\b", re.IGNORECASE)),
    ("llm cli", re.compile(r"\bllm[_-]?cli\b", re.IGNORECASE)),
    ("embedding call", re.compile(r"\bembed(ding[s]?|_)?\b", re.IGNORECASE)),
    ("completion call", re.compile(r"\bchat[._]completions?\b", re.IGNORECASE)),
]

# The merge-review handler/helper symbols whose source MUST be model-free.
_MERGE_REVIEW_SYMBOLS = (
    "compare_entities",
    "dismiss_pair",
    "merge_entities",
    "_compute_compare_snapshot",
    "_derive_shared_and_divergent",
    "_compare_fact_from_identity_row",
    "_compare_fact_from_narrative_row",
    "_fetch_identity_facts_for_compare",
    "_fetch_narrative_facts_for_compare",
    "_fetch_single_cardinality_predicates",
    "_write_merge_review",
    "_dismissed_pair_suppression_sql",
)


def _router_text() -> str:
    assert _ROUTER.exists(), f"Expected the relationship router at {_ROUTER}"
    return _ROUTER.read_text(encoding="utf-8")


def _merge_review_sources() -> dict[str, str]:
    """Return ``{symbol: source}`` for each merge-review handler/helper via AST."""
    text = _router_text()
    tree = ast.parse(text)
    wanted = set(_MERGE_REVIEW_SYMBOLS)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in wanted:
            found[node.name] = ast.get_source_segment(text, node) or ""
    return found


def _scan(text: str) -> list[tuple[int, str, str]]:
    violations: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for label, pat in _FORBIDDEN_PATTERNS:
            if pat.search(line):
                violations.append((lineno, label, line.strip()))
    return violations


def test_all_merge_review_symbols_present() -> None:
    """Every named merge-review handler/helper resolves in the router."""
    found = _merge_review_sources()
    missing = [s for s in _MERGE_REVIEW_SYMBOLS if s not in found]
    assert not missing, f"Merge-review handlers/helpers not found in router.py: {missing}"


def test_no_model_call_in_merge_review_paths() -> None:
    """Each merge-review handler/helper body MUST contain no LLM/spawner/embed token."""
    all_violations: list[str] = []
    for symbol, source in _merge_review_sources().items():
        for lineno, label, txt in _scan(source):
            all_violations.append(f"  {symbol}: [{label}] {txt!r} (line +{lineno})")
    assert not all_violations, (
        "Merge-review paths must be model-free (relationship-merge-review §"
        '"No model involvement"). Forbidden tokens found:\n' + "\n".join(all_violations)
    )


def test_scan_catches_synthetic_spawner_import() -> None:
    """The scan fails on a synthetic spawner import (negative control).

    Proves the guardrail is real: if a future edit wired a spawner/LLM call into a
    handler, this scan would flag it.
    """
    synthetic = "    from butlers.core.llm_cli_spawner import spawn_llm_cli\n"
    assert _scan(synthetic), "The no-LLM scan must flag a synthetic spawner import"
